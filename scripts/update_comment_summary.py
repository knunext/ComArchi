#!/usr/bin/env python3
"""Build data/comment-summary.json from giscus-backed GitHub Discussions.

Discussion titles are expected to be exactly:
    slide:<chapter-id>:<4-digit-slide-number>
Example:
    slide:ch03:0247

Output semantics:
  total      = top-level comments + all replies
  unanswered = top-level comments that have zero replies
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://api.github.com/graphql"
TITLE_RE = re.compile(r"(?:^|\s)slide:([^:]+):(\d{4})(?:\s|$)")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "comment-summary.json"

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()

if not TOKEN:
    raise SystemExit("GITHUB_TOKEN is required")
if "/" not in REPO:
    raise SystemExit("GITHUB_REPOSITORY must be owner/repo")
OWNER, NAME = REPO.split("/", 1)


def graphql(query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "lecture-slide-comment-summary",
        },
    )
    with urllib.request.urlopen(req) as r:
        payload = json.load(r)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], ensure_ascii=False))
    return payload["data"]


DISCUSSIONS_QUERY = r"""
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    discussions(first:100, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        title
        comments(first:100) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { replies { totalCount } }
        }
      }
    }
  }
}
"""

MORE_COMMENTS_QUERY = r"""
query($id:ID!, $after:String) {
  node(id:$id) {
    ... on Discussion {
      comments(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes { replies { totalCount } }
      }
    }
  }
}
"""


def all_discussions():
    after = None
    while True:
        data = graphql(DISCUSSIONS_QUERY, {"owner": OWNER, "name": NAME, "after": after})
        conn = data["repository"]["discussions"]
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]


def reply_counts(discussion: dict) -> list[int]:
    conn = discussion["comments"]
    out = [int(n["replies"]["totalCount"]) for n in conn["nodes"]]
    after = conn["pageInfo"]["endCursor"]
    has_next = conn["pageInfo"]["hasNextPage"]
    while has_next:
        data = graphql(MORE_COMMENTS_QUERY, {"id": discussion["id"], "after": after})
        more = data["node"]["comments"]
        out.extend(int(n["replies"]["totalCount"]) for n in more["nodes"])
        has_next = more["pageInfo"]["hasNextPage"]
        after = more["pageInfo"]["endCursor"]
    return out


def main():
    summary: dict[str, dict[str, dict[str, int]]] = {}
    matched = 0
    for d in all_discussions():
        m = TITLE_RE.search(d["title"].strip())
        if not m:
            continue
        matched += 1
        chapter, slide4 = m.groups()
        slide = str(int(slide4))
        top_level = int(d["comments"]["totalCount"])
        replies = reply_counts(d)
        total = top_level + sum(replies)
        unanswered = sum(1 for n in replies if n == 0)
        summary.setdefault(chapter, {})[slide] = {
            "total": total,
            "unanswered": unanswered,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} from {matched} slide discussions")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
