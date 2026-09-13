const qs = new URLSearchParams(location.search);
const CFG = window.SLIDE_SITE_CONFIG || {};

async function getJSON(url){
  if(location.protocol === 'file:'){
    throw new Error('LOCAL_FILE_MODE');
  }
  const r = await fetch(url, {cache:'no-cache'});
  if(!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

function showLoadError(err){
  const root = document.getElementById('chapters') || document.getElementById('slides') || document.querySelector('main');
  if(!root) return;
  const local = location.protocol === 'file:' || String(err?.message || '').includes('LOCAL_FILE_MODE');
  root.innerHTML = `<div class="load-error">
    <strong>${local ? 'Local server required' : 'Unable to load course data'}</strong>
    <p>${local
      ? 'Do not open index.html by double-clicking. Run <code>python server.py</code> in this folder, then open <code>http://127.0.0.1:8000</code>.'
      : esc(err?.message || err)}</p>
  </div>`;
}


function pad(n){ return String(n).padStart(4,'0'); }
function esc(s){
  return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function imageURL(chapter, kind, slide, revision){
  // Slide images are hosted in the same GitHub Pages repository.
  const q = revision ? `?v=${encodeURIComponent(revision)}` : '';
  return `slides/${encodeURIComponent(chapter)}/${kind}/${pad(slide)}.webp${q}`;
}

function discussionTerm(chapter, slide){
  // This string is intentionally stable. Do not change it after comments exist.
  return `slide:${chapter}:${pad(slide)}`;
}

async function loadCommentSummary(){
  try { return await getJSON('data/comment-summary.json'); }
  catch(e){ console.warn('comment-summary.json unavailable', e); return {}; }
}

async function loadChapters(){
  const [data, allSummary] = await Promise.all([
    getJSON('data/chapters.json'),
    loadCommentSummary()
  ]);
  const root = document.getElementById('chapters');
  root.innerHTML = data.chapters.map((c, idx)=>{
    const firstSlide = Math.max(1, Number(c.firstSlide || 1));
    const chapterCounts = allSummary[c.id] || {};
    const summary = Object.values(chapterCounts).reduce((acc, info) => {
      acc.total += Number(info.total || 0);
      acc.unanswered += Number(info.unanswered || 0);
      return acc;
    }, {total:0, unanswered:0});

    return `
    <a class="chapter-card" href="chapter.html?chapter=${encodeURIComponent(c.id)}">
      <div class="chapter-cover-wrap">
        <img class="chapter-cover" loading="lazy"
             src="${imageURL(c.id,'full',firstSlide,c.revision)}"
             alt="${esc(c.title)} first slide">
        <span class="chapter-number">${String(idx+1).padStart(2,'0')}</span>
      </div>
      <div class="chapter-card-body">
        <h2>${esc(c.title)}</h2>
        <div class="chapter-meta-row">
          <div class="meta">${c.slideCount} slides</div>
          ${(summary.total || summary.unanswered) ? `<div class="comment-badge-group inline-comment-badges">
            ${summary.total ? `<span class="comment-badge total-badge" title="${summary.total} total comments/replies in this chapter">${summary.total > 99 ? '99+' : summary.total}</span>` : ''}
            ${summary.unanswered ? `<span class="comment-badge unanswered-badge" title="${summary.unanswered} unanswered top-level comments in this chapter">${summary.unanswered > 99 ? '99+' : summary.unanswered}</span>` : ''}
          </div>` : ''}
        </div>
      </div>
      <div class="chapter-arrow" aria-hidden="true">→</div>
    </a>`;
  }).join('');
}

async function loadChapterPage(){
  const id = qs.get('chapter');
  const [data, allSummary] = await Promise.all([
    getJSON('data/chapters.json'),
    loadCommentSummary()
  ]);
  const c = data.chapters.find(x=>x.id===id);
  if(!c) throw new Error('chapter not found');

  document.title = c.title;
  document.getElementById('chapterTitle').textContent = c.title;
  document.getElementById('chapterMeta').textContent = `${c.slideCount} slides · select a slide to open discussion`;

  const counts = allSummary[id] || {};
  const summary = Object.values(counts).reduce((acc, info) => {
    acc.total += Number(info.total || 0);
    acc.unanswered += Number(info.unanswered || 0);
    return acc;
  }, {total:0, unanswered:0});

  document.getElementById('chapterTotalComments').textContent = summary.total > 999 ? '999+' : summary.total;
  document.getElementById('chapterUnansweredComments').textContent = summary.unanswered > 999 ? '999+' : summary.unanswered;

  const root = document.getElementById('slides');
  root.innerHTML = Array.from({length:c.slideCount},(_,k)=>thumbHTML(c,k+1,counts[String(k+1)]||counts[k+1]||{})).join('');

  const commentedSlides = Object.values(counts).filter(info => Number(info?.total || 0) > 0).length;
  const filterBtn = document.getElementById('commentFilterBtn');
  const filterCount = document.getElementById('commentFilterCount');
  let commentsOnly = false;

  filterCount.textContent = commentedSlides > 99 ? '99+' : String(commentedSlides);
  filterBtn.disabled = commentedSlides === 0;
  filterBtn.title = commentedSlides
    ? `Show only the ${commentedSlides} slide${commentedSlides === 1 ? '' : 's'} with comments`
    : 'No slides have comments yet';

  filterBtn.addEventListener('click', () => {
    if(filterBtn.disabled) return;
    commentsOnly = !commentsOnly;
    filterBtn.classList.toggle('active', commentsOnly);
    filterBtn.setAttribute('aria-pressed', commentsOnly ? 'true' : 'false');
    root.classList.toggle('comments-only', commentsOnly);
  });
}

function thumbHTML(c,i,countInfo={}){
  const total = Number(countInfo.total || 0);
  const unanswered = Number(countInfo.unanswered || 0);
  const heatClass = total >= 5 ? ' comment-heat-3' : total >= 3 ? ' comment-heat-2' : total >= 1 ? ' comment-heat-1' : '';
  const ariaBits = [];
  if(total) ariaBits.push(`${total} comments and replies`);
  if(unanswered) ariaBits.push(`${unanswered} unanswered comments`);
  const ariaExtra = ariaBits.length ? `, ${ariaBits.join(', ')}` : '';

  return `<a class="thumb${heatClass}" data-slide="${i}" data-has-comments="${total > 0 ? '1' : '0'}" href="viewer.html?chapter=${encodeURIComponent(c.id)}&slide=${i}" aria-label="Open slide ${i}${ariaExtra}">
    <div class="thumb-image-wrap">
      <img loading="lazy" src="${imageURL(c.id,'thumb',i,c.revision)}" alt="Slide ${i}">
    </div>
    <div class="thumb-footer">
      <span>Slide ${i}</span>
      ${(total || unanswered) ? `<div class="comment-badge-group inline-comment-badges">
        ${total ? `<span class="comment-badge total-badge" title="${total} total comments/replies">${total > 99 ? '99+' : total}</span>` : ''}
        ${unanswered ? `<span class="comment-badge unanswered-badge" title="${unanswered} top-level comments without a reply">${unanswered > 99 ? '99+' : unanswered}</span>` : ''}
      </div>` : ''}
    </div>
  </a>`;
}

function giscusConfigured(){
  const g = CFG.GISCUS || {};
  return !!(g.repo && g.repoId && g.category && g.categoryId &&
    !String(g.repo).startsWith('YOUR_') && !String(g.repoId).startsWith('YOUR_') && !String(g.categoryId).startsWith('YOUR_'));
}

function initGiscus(term, backLink){
  if(!giscusConfigured()){
    document.getElementById('giscusSetupNotice').hidden = false;
    return;
  }

  document.getElementById('giscusSetupNotice').hidden = true;
  const g = CFG.GISCUS;
  const root = document.getElementById('giscusRoot');

  // Always build a fresh giscus iframe for the current slide.
  // This avoids stale discussion state when moving between slides.
  root.innerHTML = '';

  // Used by giscus when it creates the Discussion backlink.
  let meta = document.querySelector('meta[name="giscus:backlink"]');
  if(!meta){
    meta = document.createElement('meta');
    meta.name = 'giscus:backlink';
    document.head.appendChild(meta);
  }
  meta.content = backLink;

  const script = document.createElement('script');
  script.src = 'https://giscus.app/client.js';
  script.dataset.repo = g.repo;
  script.dataset.repoId = g.repoId;
  script.dataset.category = g.category;
  script.dataset.categoryId = g.categoryId;
  script.dataset.mapping = g.mapping || 'specific';
  script.dataset.term = term;
  script.dataset.strict = g.strict || '1';
  script.dataset.reactionsEnabled = g.reactionsEnabled || '0';
  script.dataset.emitMetadata = g.emitMetadata || '0';
  script.dataset.inputPosition = g.inputPosition || 'top';
  script.dataset.theme = g.theme || 'preferred_color_scheme';
  script.dataset.lang = g.lang || 'ko';
  if(g.loading) script.dataset.loading = g.loading;
  script.crossOrigin = 'anonymous';
  script.async = true;
  root.appendChild(script);
}

function setGiscusTerm(term, backLink){
  // Recreate instead of postMessage so each slide starts with an unambiguous
  // mapping term such as slide:ch01:0001.
  initGiscus(term, backLink);
}

async function loadViewer(){
  const id = qs.get('chapter');
  let slide = parseInt(qs.get('slide') || '1',10);
  const [data, allSummary] = await Promise.all([
    getJSON('data/chapters.json'),
    loadCommentSummary()
  ]);
  const c = data.chapters.find(x=>x.id===id);
  if(!c) throw new Error('chapter not found');
  slide = Math.min(Math.max(1,slide),c.slideCount);

  const chapterUrl = `chapter.html?chapter=${encodeURIComponent(id)}`;
  document.getElementById('chapterLink').textContent = c.title;
  document.getElementById('chapterLink').href = chapterUrl;
  document.getElementById('backGrid').href = chapterUrl;
  document.getElementById('backGridTop').href = chapterUrl;
  document.getElementById('viewerChapterTitle').textContent = c.title;

  const img = document.getElementById('slideImage');
  const counter = document.getElementById('slideCounter');
  const prev = document.getElementById('prevBtn');
  const next = document.getElementById('nextBtn');
  const countLabel = document.getElementById('commentCountLabel');
  const counts = allSummary[id] || {};

  function render(){
    img.src = imageURL(id,'full',slide,c.revision);
    img.alt = `${c.title}, slide ${slide}`;
    counter.textContent = `Slide ${slide} of ${c.slideCount}`;
    prev.disabled = slide <= 1;
    next.disabled = slide >= c.slideCount;
    // IMPORTANT: after Giscus OAuth, giscus redirects back to this page with
    // ?giscus=<session>. The Giscus client must see that parameter once so it can
    // persist the session in this site's localStorage and then remove it itself.
    // Do not strip it here before client.js runs.
    const oauthSession = new URL(location.href).searchParams.get('giscus');
    const viewerUrl = new URL('viewer.html', location.href);
    viewerUrl.searchParams.set('chapter', id);
    viewerUrl.searchParams.set('slide', String(slide));
    if(oauthSession) viewerUrl.searchParams.set('giscus', oauthSession);
    history.replaceState(null, '', viewerUrl.pathname + viewerUrl.search);
    document.title = `${c.title} · Slide ${slide}`;

    const info = counts[String(slide)] || counts[slide] || {};
    const total = Number(info.total || 0);
    const unanswered = Number(info.unanswered || 0);
    countLabel.textContent = total ? `${total} messages${unanswered ? ` · ${unanswered} unanswered` : ''}` : '';

    const term = discussionTerm(id, slide);
    setGiscusTerm(term, location.href);
  }

  function go(n){
    slide = n;
    render();
    window.scrollTo({top:0,behavior:'smooth'});
  }

  prev.onclick = ()=> slide>1 && go(slide-1);
  next.onclick = ()=> slide<c.slideCount && go(slide+1);

  img.style.cursor = 'pointer';
  img.title = 'Click to go to the next slide';
  img.onclick = ()=> { if(slide < c.slideCount) go(slide + 1); };

  document.addEventListener('keydown',e=>{
    const tag = document.activeElement?.tagName;
    if(tag === 'TEXTAREA' || tag === 'INPUT') return;
    if(e.key==='ArrowLeft' && slide>1) go(slide-1);
    if(e.key==='ArrowRight' && slide<c.slideCount) go(slide+1);
  });

  // If giscus emits metadata, update the viewer label immediately for the open slide.
  window.addEventListener('message', event=>{
    if(event.origin !== 'https://giscus.app') return;
    const gd = event.data && event.data.giscus;
    if(!gd || !gd.discussion) return;
    // The static grid badges are refreshed by GitHub Actions; this label is best-effort live metadata.
    const replies = Number(gd.discussion.totalCommentCount || gd.discussion.comments?.totalCount || 0);
    if(replies > 0 && !countLabel.textContent) countLabel.textContent = `${replies} messages`;
  });

  render();
}


async function startChapters(){
  try { await loadChapters(); } catch(e) { console.error(e); showLoadError(e); }
}
async function startChapterPage(){
  try { await loadChapterPage(); } catch(e) { console.error(e); showLoadError(e); }
}
async function startViewer(){
  try { await loadViewer(); } catch(e) { console.error(e); showLoadError(e); }
}
