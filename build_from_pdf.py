from pathlib import Path
import argparse
import io
import json
import shutil
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parent

# One PyMuPDF Document per worker process.  A Document object is not passed
# between processes; each worker opens the PDF once in its initializer.
_WORKER_DOC = None


def _worker_init(pdf_path: str):
    global _WORKER_DOC
    _WORKER_DOC = fitz.open(pdf_path)


def encode_webp_under_limit(img: Image.Image, out_path: Path, target_bytes: int,
                            max_quality: int = 68, min_quality: int = 24) -> tuple[int, int]:
    """Save WebP at the highest quality that fits under target_bytes.

    Returns (bytes_written, quality_used). If even min_quality is too large,
    caller should reduce resolution and try again.
    """
    lo, hi = min_quality, max_quality
    best = None
    while lo <= hi:
        q = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=q, method=6)
        data = buf.getvalue()
        if len(data) <= target_bytes:
            best = (data, q)
            lo = q + 1
        else:
            hi = q - 1

    if best is None:
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=min_quality, method=6)
        return len(buf.getvalue()), min_quality

    out_path.write_bytes(best[0])
    return len(best[0]), best[1]


def fit_and_save(img: Image.Image, out_path: Path, target_bytes: int,
                 widths: list[int], max_quality: int, min_quality: int) -> tuple[int, int, int]:
    """Try progressively smaller widths until the image fits the size target."""
    source_w, source_h = img.size
    last_data = None
    last_q = min_quality
    last_w = widths[-1]

    for width in widths:
        width = min(width, source_w)
        height = max(1, round(source_h * width / source_w))
        resized = img if width == source_w else img.resize((width, height), Image.Resampling.LANCZOS)

        lo, hi = min_quality, max_quality
        best = None
        while lo <= hi:
            q = (lo + hi) // 2
            buf = io.BytesIO()
            resized.save(buf, 'WEBP', quality=q, method=6)
            data = buf.getvalue()
            if len(data) <= target_bytes:
                best = (data, q)
                lo = q + 1
            else:
                hi = q - 1

        if best is not None:
            out_path.write_bytes(best[0])
            return len(best[0]), best[1], width

        buf = io.BytesIO()
        resized.save(buf, 'WEBP', quality=min_quality, method=6)
        last_data = buf.getvalue()
        last_q = min_quality
        last_w = width

    out_path.write_bytes(last_data)
    return len(last_data), last_q, last_w


def _make_full_widths(max_width: int) -> list[int]:
    widths = []
    for w in (max_width, 1152, 1024, 896, 768, 640, 560, 480, 400, 320):
        if w not in widths and w <= max_width:
            widths.append(w)
    return widths or [max_width]


def _process_page(page_index: int, full_dir: str, thumb_dir: str,
                  target_bytes: int, thumb_target_bytes: int,
                  max_width: int, thumb_width: int,
                  max_quality: int, min_quality: int,
                  full_widths: list[int]):
    """Render and encode one PDF page. page_index is zero-based."""
    page = _WORKER_DOC.load_page(page_index)
    slide_no = page_index + 1

    scale = max_width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)

    fpath = Path(full_dir) / f'{slide_no:04d}.webp'
    fsize, fq, fw = fit_and_save(
        img, fpath, target_bytes, full_widths,
        max_quality=max_quality, min_quality=min_quality,
    )

    tw = min(thumb_width, img.width)
    th = max(1, round(img.height * tw / img.width))
    timg = img.resize((tw, th), Image.Resampling.LANCZOS)
    tpath = Path(thumb_dir) / f'{slide_no:04d}.webp'
    tsize, tq, _ = fit_and_save(
        timg, tpath, thumb_target_bytes,
        [tw, min(tw, 240), min(tw, 200)],
        max_quality=64, min_quality=22,
    )

    return slide_no, fsize, fq, fw, tsize, tq


def _process_page_serial(doc, page_index: int, full: Path, thumb: Path,
                         target_bytes: int, thumb_target_bytes: int,
                         max_width: int, thumb_width: int,
                         max_quality: int, min_quality: int,
                         full_widths: list[int]):
    """Single-core path that reuses the already-open PDF document."""
    page = doc.load_page(page_index)
    slide_no = page_index + 1

    scale = max_width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)

    fpath = full / f'{slide_no:04d}.webp'
    fsize, fq, fw = fit_and_save(
        img, fpath, target_bytes, full_widths,
        max_quality=max_quality, min_quality=min_quality,
    )

    tw = min(thumb_width, img.width)
    th = max(1, round(img.height * tw / img.width))
    timg = img.resize((tw, th), Image.Resampling.LANCZOS)
    tpath = thumb / f'{slide_no:04d}.webp'
    tsize, tq, _ = fit_and_save(
        timg, tpath, thumb_target_bytes,
        [tw, min(tw, 240), min(tw, 200)],
        max_quality=64, min_quality=22,
    )

    return slide_no, fsize, fq, fw, tsize, tq


def render(pdf_path, out_root, chapter_id, title,
           target_kb=48, thumb_target_kb=10,
           max_width=1280, thumb_width=280,
           max_quality=68, min_quality=18,
           cores=1):
    if cores < 1:
        raise ValueError('--cores must be >= 1')

    pdf_path = Path(pdf_path).resolve()
    out_root = Path(out_root)
    doc = fitz.open(pdf_path)
    slide_count = len(doc)

    base = out_root / chapter_id
    full = base / 'full'
    thumb = base / 'thumb'

    if base.exists():
        shutil.rmtree(base)
    full.mkdir(parents=True, exist_ok=True)
    thumb.mkdir(parents=True, exist_ok=True)

    target_bytes = int(target_kb * 1024)
    thumb_target_bytes = int(thumb_target_kb * 1024)
    full_widths = _make_full_widths(max_width)

    print(f'Rendering {slide_count} slides from {pdf_path.name}')
    print(f'Workers: {cores}')
    print(f'Full target: <={target_kb} KiB each, max width={max_width}px, quality={min_quality}-{max_quality}')
    print(f'Thumb target: <={thumb_target_kb} KiB each, width<={thumb_width}px')

    # Keep results indexed by slide number so summary/progress is deterministic
    # even though multi-core tasks may finish out of order.
    results = [None] * slide_count

    if cores == 1:
        for page_index in range(slide_count):
            result = _process_page_serial(
                doc, page_index, full, thumb,
                target_bytes, thumb_target_bytes,
                max_width, thumb_width,
                max_quality, min_quality,
                full_widths,
            )
            slide_no, fsize, fq, fw, tsize, tq = result
            results[slide_no - 1] = result
            print(f'{slide_no:4d}/{slide_count}  full={fsize/1024:5.1f} KiB ({fw}px q{fq})  thumb={tsize/1024:4.1f} KiB')
    else:
        # Main process no longer needs the open document while workers render.
        doc.close()
        doc = None

        with ProcessPoolExecutor(
            max_workers=cores,
            initializer=_worker_init,
            initargs=(str(pdf_path),),
        ) as executor:
            futures = [
                executor.submit(
                    _process_page,
                    page_index,
                    str(full), str(thumb),
                    target_bytes, thumb_target_bytes,
                    max_width, thumb_width,
                    max_quality, min_quality,
                    full_widths,
                )
                for page_index in range(slide_count)
            ]

            completed = 0
            for future in as_completed(futures):
                result = future.result()
                slide_no, fsize, fq, fw, tsize, tq = result
                results[slide_no - 1] = result
                completed += 1
                print(
                    f'{completed:4d}/{slide_count} done  '
                    f'slide={slide_no:4d}  full={fsize/1024:5.1f} KiB '
                    f'({fw}px q{fq})  thumb={tsize/1024:4.1f} KiB'
                )

    if doc is not None:
        doc.close()

    full_sizes = []
    thumb_sizes = []
    oversize = []
    for slide_no, fsize, fq, fw, tsize, tq in results:
        full_sizes.append(fsize)
        thumb_sizes.append(tsize)
        if fsize > target_bytes:
            oversize.append((slide_no, fsize))

    meta_path = ROOT / 'data' / 'chapters.json'
    meta = {'chapters': []}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))

    revision = int(time.time())
    new_row = {
        'id': chapter_id,
        'title': title,
        'slideCount': slide_count,
        'revision': revision,
    }
    meta['chapters'] = [c for c in meta.get('chapters', []) if c.get('id') != chapter_id]
    meta['chapters'].append(new_row)
    meta['chapters'].sort(key=lambda x: x.get('id', ''))
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    mib = 1024 * 1024
    full_total = sum(full_sizes)
    thumb_total = sum(thumb_sizes)
    print('\nDone.')
    print(f'Chapter metadata updated: {meta_path}')
    print(f'Full images: {full_total/mib:.1f} MiB total, avg={statistics.mean(full_sizes)/1024:.1f} KiB')
    print(f'Thumbnails:  {thumb_total/mib:.1f} MiB total, avg={statistics.mean(thumb_sizes)/1024:.1f} KiB')
    if oversize:
        print(f'WARNING: {len(oversize)} unusually complex slide(s) still exceeded {target_kb} KiB at minimum settings:')
        print(', '.join(f'{n}:{b/1024:.1f}KiB' for n, b in oversize[:20]))
        print('This should be rare; lower --min-quality or --max-width if a pathological slide must be forced smaller.')
    else:
        print(f'All full slide images are <= {target_kb} KiB.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Convert a lecture PDF into compact WebP slides for GitHub Pages.')
    ap.add_argument('pdf')
    ap.add_argument('--id', required=True, help='Chapter id, e.g. ch01')
    ap.add_argument('--title', required=True, help='Chapter title shown on the site')
    ap.add_argument('--target-kb', type=int, default=48, help='Target maximum full-image size in KiB (default: 48)')
    ap.add_argument('--thumb-target-kb', type=int, default=10, help='Target maximum thumbnail size in KiB (default: 10)')
    ap.add_argument('--max-width', type=int, default=1280, help='Maximum full slide width (default: 1280)')
    ap.add_argument('--thumb-width', type=int, default=280, help='Maximum thumbnail width (default: 280)')
    ap.add_argument('--max-quality', type=int, default=68, help='Maximum WebP quality (default: 68)')
    ap.add_argument('--min-quality', type=int, default=18, help='Minimum WebP quality before reducing resolution (default: 18)')
    ap.add_argument('--cores', type=int, default=1, help='Number of worker processes (default: 1)')
    a = ap.parse_args()

    render(
        a.pdf, ROOT / 'slides', a.id, a.title,
        target_kb=a.target_kb,
        thumb_target_kb=a.thumb_target_kb,
        max_width=a.max_width,
        thumb_width=a.thumb_width,
        max_quality=a.max_quality,
        min_quality=a.min_quality,
        cores=a.cores,
    )
