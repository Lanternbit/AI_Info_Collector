"""arXiv — export API 사용 (RSS는 당일 공지분만 담고 주말·공휴일엔 비어 있음).

요청 간격 3초 이상 — 실행당 1회 호출이므로 자동 충족."""
from __future__ import annotations

import re

import feedparser
import httpx

from src.fetchers.rss import parse_entry_date
from src.models import Item, strip_html
from src.net import get_with_retry


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    resp = get_with_retry(client, source["url"])
    parsed = feedparser.parse(resp.content)
    items = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        title = re.sub(r"\s+", " ", entry.get("title") or "(제목 없음)").strip()
        items.append(
            Item(
                title=title,
                url=link,
                source=source["name"],
                tier=source.get("tier", 1),
                published=parse_entry_date(entry),
                body=strip_html(entry.get("summary", ""))[:2000],
            )
        )
    return items
