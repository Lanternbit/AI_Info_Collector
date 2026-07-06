"""Hugging Face Daily Papers — 비공식이지만 수년간 안정적인 공개 JSON 엔드포인트.

업보트 기반 커뮤니티 큐레이션이라 일일 프론티어 논문 신호로 최상."""
from __future__ import annotations

from datetime import datetime

import httpx

from src.models import Item
from src.net import get_json


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    data = get_json(client, source["url"])
    items = []
    for rec in data:
        paper = rec.get("paper") or {}
        pid = paper.get("id")
        title = (paper.get("title") or "").strip()
        if not pid or not title:
            continue
        body = paper.get("ai_summary") or paper.get("summary") or ""
        items.append(
            Item(
                title=title,
                url=f"https://huggingface.co/papers/{pid}",
                source=source["name"],
                tier=source.get("tier", 1),
                published=_parse_iso(rec.get("publishedAt")),
                body=body[:2000],
                metrics={"upvotes": paper.get("upvotes") or 0},
                is_paper=True,
            )
        )
    return items
