"""Hacker News — Algolia HN Search API. 두 쿼리 세트를 모두 실행 후 story_id로 통합 dedupe."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from src.models import Item, strip_html
from src.net import get_json

API = "https://hn.algolia.com/api/v1"


def _to_item(hit: dict, source: dict) -> Item:
    object_id = hit.get("objectID") or hit.get("story_id")
    hn_page = f"https://news.ycombinator.com/item?id={object_id}"
    url = hit.get("url") or hn_page  # 링크 포스트면 기사 원문, Ask HN이면 HN 페이지
    created = hit.get("created_at_i")
    return Item(
        title=(hit.get("title") or "(제목 없음)").strip(),
        url=url,
        source=source["name"],
        tier=source.get("tier", 1),
        published=datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
        body=strip_html(hit.get("story_text") or "")[:8000],
        metrics={"points": hit.get("points") or 0, "comments": hit.get("num_comments") or 0},
        discussion_url=hn_page if url != hn_page else "",
    )


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    seen_ids: set[str] = set()
    items: list[Item] = []

    def add_hits(hits):
        for hit in hits:
            oid = str(hit.get("objectID") or hit.get("story_id") or "")
            if not oid or oid in seen_ids or not hit.get("title"):
                continue
            seen_ids.add(oid)
            items.append(_to_item(hit, source))

    # 쿼리 세트 1: 키워드별 프론트페이지
    for kw in source.get("keywords", ["AI"]):
        data = get_json(client, f"{API}/search?tags=front_page&query={quote(kw)}")
        add_hits(data.get("hits", []))

    # 쿼리 세트 2: 최근 고득점 스토리
    cutoff = int(time.time()) - cfg.get("freshness_hours", 36) * 3600
    data = get_json(
        client,
        f"{API}/search_by_date?tags=story&query=AI&numericFilters=points>100,created_at_i>{cutoff}",
    )
    add_hits(data.get("hits", []))
    return items
