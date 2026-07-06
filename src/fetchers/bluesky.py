"""Bluesky — 공식 무료 API (무인증, ToS 문제 없음).

filter=posts_no_replies + 리포스트(reason 필드 존재) 제외, 원본 포스트만."""
from __future__ import annotations

import time

import httpx

from src.models import Item, parse_iso_utc
from src.net import get_json

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    handles = cfg.get("bluesky_handles") or []
    items: list[Item] = []
    for handle in handles:
        try:
            data = get_json(client, f"{API}?actor={handle}&filter=posts_no_replies&limit=30")
        except Exception:
            continue  # 핸들 하나 실패는 소스 전체 실패가 아님
        author_name = None
        for feed_view in data.get("feed", []):
            if feed_view.get("reason"):  # 리포스트
                continue
            post = feed_view.get("post") or {}
            record = post.get("record") or {}
            text = (record.get("text") or "").strip()
            if not text:
                continue
            if author_name is None:
                author_name = (post.get("author") or {}).get("displayName") or handle
            rkey = (post.get("uri") or "").rsplit("/", 1)[-1]
            first_line = text.splitlines()[0][:100]
            items.append(
                Item(
                    title=f"{author_name}: {first_line}",
                    url=f"https://bsky.app/profile/{handle}/post/{rkey}",
                    source=source["name"],
                    tier=source.get("tier", 1),
                    published=parse_iso_utc(record.get("createdAt")),
                    body=text[:2000],
                    metrics={"likes": post.get("likeCount") or 0, "reposts": post.get("repostCount") or 0},
                )
            )
        time.sleep(0.2)
    return items
