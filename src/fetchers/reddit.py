"""Reddit — old.reddit.com의 .rss만 사용 (www.reddit.com/*.json 무인증은 403).

설명적 User-Agent 필수, 초당 1요청 이하 유지.
링크 포스트는 [link] 앵커에서 외부 기사 URL을 추출해 본문 링크로 쓰고,
Reddit 페이지는 discussion_url(토론)로 분리한다."""
from __future__ import annotations

import re
import time

import feedparser
import httpx

from src.fetchers.rss import extract_body, parse_entry_date
from src.models import Item, strip_html
from src.net import get_with_retry

_last_request_at = 0.0
_REQUEST_GAP = 6.0  # 무인증 .rss는 레이트 리밋이 빡빡함 — 서브레딧 간 6초 간격
_LINK_RE = re.compile(r'href="([^"]+)"\s*>\s*\[link\]', re.I)


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    global _last_request_at
    wait = _REQUEST_GAP - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    sub = source["subreddit"]
    resp = get_with_retry(client, f"https://old.reddit.com/r/{sub}/top/.rss?t=day")
    _last_request_at = time.monotonic()
    parsed = feedparser.parse(resp.content)
    items = []
    for entry in parsed.entries[:25]:
        link = entry.get("link", "")
        if not link:
            continue
        raw_body = extract_body(entry)
        url, discussion_url = link, ""
        match = _LINK_RE.search(raw_body or "")
        if match:
            external = match.group(1)
            if external.startswith("http") and "reddit.com" not in external:
                url, discussion_url = external, link  # 링크 포스트: 본문은 기사, 토론은 Reddit
        items.append(
            Item(
                title=(entry.get("title") or "(제목 없음)").strip(),
                url=url,
                source=source["name"],
                tier=source.get("tier", 1),
                published=parse_entry_date(entry),
                body=strip_html(raw_body)[:8000],
                discussion_url=discussion_url,
            )
        )
    return items
