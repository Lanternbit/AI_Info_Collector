"""Reddit — old.reddit.com의 .rss만 사용 (www.reddit.com/*.json 무인증은 403).

설명적 User-Agent 필수, 초당 1요청 이하 유지."""
from __future__ import annotations

import time

import feedparser
import httpx

from src.fetchers.rss import extract_body, parse_entry_date
from src.models import Item, strip_html
from src.net import get_with_retry

_last_request_at = 0.0
_REQUEST_GAP = 6.0  # 무인증 .rss는 레이트 리밋이 빡빡함 — 서브레딧 간 6초 간격


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
        items.append(
            Item(
                title=(entry.get("title") or "(제목 없음)").strip(),
                url=link,
                source=source["name"],
                tier=source.get("tier", 1),
                published=parse_entry_date(entry),
                body=strip_html(extract_body(entry))[:2000],
            )
        )
    return items
