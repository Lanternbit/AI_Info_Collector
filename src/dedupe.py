"""최신성 필터 + seen 필터 + 중복 제거(URL 정규화 / 제목 유사도)."""
from __future__ import annotations

from datetime import timedelta, timezone
from difflib import SequenceMatcher

from src.models import Item, now_kst

TITLE_SIM_THRESHOLD = 0.92


def filter_fresh(items: list[Item], hours: int) -> list[Item]:
    """게시 시각이 최근 N시간 이내인 아이템만 통과. 게시 시각이 없으면 최초 발견으로 간주해 통과
    (이후 실행에서는 seen 필터가 걸러 준다)."""
    cutoff = now_kst() - timedelta(hours=hours)
    fresh = []
    for it in items:
        pub = it.published
        if pub is not None and pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)  # 방어: naive는 UTC로 간주 (비교 크래시 방지)
        if pub is None or pub >= cutoff:
            fresh.append(it)
    return fresh


def filter_seen(items: list[Item], seen: dict) -> list[Item]:
    return [it for it in items if it.key not in seen]


def _better(a: Item, b: Item) -> Item:
    """중복 쌍 중 남길 쪽: 낮은 tier 우선, 그다음 본문이 긴 쪽."""
    if a.tier != b.tier:
        return a if a.tier < b.tier else b
    return a if len(a.body) >= len(b.body) else b


def dedupe(items: list[Item]) -> list[Item]:
    # 1단계: 정규화 URL 키 기준
    by_key: dict[str, Item] = {}
    for it in items:
        if it.key in by_key:
            by_key[it.key] = _better(by_key[it.key], it)
        else:
            by_key[it.key] = it

    # 2단계: 제목 유사도 (O(n^2), quick_ratio 프리필터)
    unique: list[Item] = []
    for it in by_key.values():
        title = it.title.lower().strip()
        dup_of = None
        for kept in unique:
            kt = kept.title.lower().strip()
            m = SequenceMatcher(None, title, kt)
            if m.quick_ratio() >= TITLE_SIM_THRESHOLD and m.ratio() >= TITLE_SIM_THRESHOLD:
                dup_of = kept
                break
        if dup_of is None:
            unique.append(it)
        elif _better(dup_of, it) is it:
            unique[unique.index(dup_of)] = it
    return unique
