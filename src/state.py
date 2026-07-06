"""seen.json 상태 저장 — 재수집/중복 저장 방지. 30일 지난 항목은 purge."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from src.models import KST, now_kst

SEEN_PATH = Path("data/seen.json")
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
        try:
            first_seen = datetime.fromisoformat(rec["first_seen"])
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=KST)
        except Exception:
            continue
        if first_seen >= cutoff:
            kept[key] = rec
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")


def mark_seen(seen: dict, key: str, notion_saved: bool = False) -> None:
    rec = seen.setdefault(key, {"first_seen": now_kst().isoformat()})
    if notion_saved:
        rec["notion_saved"] = True
