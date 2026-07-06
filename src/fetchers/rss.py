"""공용 RSS/Atom 수집기."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import feedparser
import httpx

from src.models import Item, strip_html
from src.net import get_with_retry

MAX_ENTRIES = 50


def parse_entry_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        st = entry.get(attr)
        if st:
            try:
                return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
            except Exception:
                continue
    return None


def extract_body(entry) -> str:
    content = entry.get("content")
    if content:
        try:
            return content[0].get("value", "")
        except Exception:
            pass
    return entry.get("summary", "")


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    headers = {}
    if source.get("user_agent"):
        headers["User-Agent"] = source["user_agent"]
    if source.get("accept"):
        headers["Accept"] = source["accept"]
    resp = get_with_retry(client, source["url"], headers=headers or None)
    parsed = feedparser.parse(resp.content)
    items = []
    for entry in parsed.entries[:MAX_ENTRIES]:
        link = entry.get("link", "")
        if not link:
            continue
        items.append(
            Item(
                title=(entry.get("title") or "(제목 없음)").strip(),
                url=link,
                source=source["name"],
                tier=source.get("tier", 2),
                published=parse_entry_date(entry),
                body=strip_html(extract_body(entry))[:2000],
            )
        )
    return items
