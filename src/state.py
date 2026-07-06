"""상태 저장 — seen.json + 당일 브리핑 스냅샷.

- seen.json: 재수집/중복 저장 방지. last_seen 기준 30일 purge
  (피드에 오래 남는 무날짜 아이템이 purge 후 '새 아이템'으로 부활하는 것 방지)
- briefing_snapshot.json: 같은 날 재실행(cron 후 workflow_dispatch 등) 시
  seen 필터로 0건이 되어 그날 브리핑이 빈 페이지로 덮어써지는 것 방지
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from src.models import KST, Item, now_kst

SEEN_PATH = Path("data/seen.json")
SNAPSHOT_PATH = Path("data/briefing_snapshot.json")
RETENTION_DAYS = 30


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_seen(seen: dict) -> None:
    cutoff = now_kst() - timedelta(days=RETENTION_DAYS)
    kept = {}
    for key, rec in seen.items():
        stamp = rec.get("last_seen") or rec.get("first_seen", "")
        try:
            dt = datetime.fromisoformat(stamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
        except Exception:
            continue
        if dt >= cutoff:
            kept[key] = rec
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")


def mark_seen(seen: dict, key: str, notion_saved: bool = False) -> None:
    now = now_kst().isoformat()
    rec = seen.setdefault(key, {"first_seen": now})
    rec["last_seen"] = now
    if notion_saved:
        rec["notion_saved"] = True


def _item_to_dict(item: Item) -> dict:
    d = asdict(item)
    d["published"] = item.published.isoformat() if item.published else None
    return d


def _item_from_dict(d: dict) -> Item:
    data = dict(d)
    pub = data.get("published")
    data["published"] = datetime.fromisoformat(pub) if pub else None
    return Item(**data)


def load_day_snapshot(date_slug: str) -> tuple[list[Item], str]:
    """(당일 아이템, 오늘의 요약) 반환. 날짜가 다르거나 없으면 빈 값."""
    if not SNAPSHOT_PATH.exists():
        return [], ""
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if data.get("date") != date_slug:
            return [], ""
        items = [_item_from_dict(d) for d in data.get("items", [])]
        return items, data.get("daily_summary", "")
    except Exception:
        return [], ""


def save_day_snapshot(date_slug: str, items: list[Item], daily_summary: str = "") -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(
            {
                "date": date_slug,
                "daily_summary": daily_summary,
                "items": [_item_to_dict(i) for i in items],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
