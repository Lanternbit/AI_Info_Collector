"""Notion REST 직접 호출 — Notion-Version 2025-09-03 고정, data_source_id parent.

- 스키마 부트스트랩: 빈 DB에 속성 자동 생성 (사용자가 손으로 만들 필요 없음)
- 멱등 저장: URL 속성으로 기존 페이지 조회 후 있으면 건너뜀
- 레이트 리밋: 요청당 ~0.34초 대기(초당 3요청), 429는 Retry-After 준수, 5xx는 지수 백오프
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from src.models import Item

log = logging.getLogger("pipeline")

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"

DESIRED_PROPS = {
    "원제": {"rich_text": {}},
    "날짜": {"date": {}},
    "출처": {"select": {}},
    "카테고리": {"select": {}},
    "중요도": {"number": {}},
    "요약": {"rich_text": {}},
    "왜 중요한가": {"rich_text": {}},
    "URL": {"url": {}},
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    for attempt in range(4):
        resp = httpx.request(method, API + path, headers=_headers(token), json=payload, timeout=30)
        if resp.status_code == 429:
            time.sleep(float(resp.headers.get("Retry-After", "2")))
            continue
        if resp.status_code in (500, 502, 503):
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion API {resp.status_code} ({method} {path}): {resp.text[:300]}")
        time.sleep(0.34)
        return resp.json()
    raise RuntimeError(f"Notion API 재시도 초과 ({method} {path})")


def resolve_data_source_id(token: str) -> str:
    ds_id = os.environ.get("NOTION_DATA_SOURCE_ID")
    if ds_id:
        return ds_id
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not db_id:
        raise RuntimeError("NOTION_DATA_SOURCE_ID 또는 NOTION_DATABASE_ID 환경변수가 필요하다")
    data = _request("GET", f"/databases/{db_id}", token)
    sources = data.get("data_sources") or []
    if not sources:
        raise RuntimeError("데이터베이스에 data source가 없다 — DB가 통합에 공유되었는지 확인")
    if len(sources) > 1:
        log.warning("data source가 %d개 — 첫 번째를 사용", len(sources))
    ds_id = sources[0]["id"]
    log.info("data_source_id: %s (Actions Secrets의 NOTION_DATA_SOURCE_ID에 이 값을 등록)", ds_id)
    return ds_id


def bootstrap_schema(token: str, ds_id: str) -> None:
    """빈 DB에 필요한 속성을 자동 생성하고 title 속성을 '제목'으로 rename.

    같은 이름·다른 타입 속성이 이미 있으면 조용히 지나가지 않고 명확한 에러로 중단한다
    (그대로 두면 이후 모든 save_item이 400으로 실패하기 때문)."""
    data = _request("GET", f"/data_sources/{ds_id}", token)
    existing = data.get("properties") or {}
    patch: dict = {}
    mismatches: list[str] = []
    title_name = next((n for n, p in existing.items() if p.get("type") == "title"), None)
    if title_name and title_name != "제목":
        if "제목" in existing:
            mismatches.append(f"'제목' 속성이 title 타입이 아님(현재 {existing['제목'].get('type')}) — 이름 변경 또는 삭제 필요")
        else:
            patch[title_name] = {"name": "제목"}
    for name, schema in DESIRED_PROPS.items():
        if name in existing:
            want = next(iter(schema))
            got = existing[name].get("type")
            if got != want:
                mismatches.append(f"'{name}': {got} → {want} 타입이어야 함")
        else:
            patch[name] = schema
    if mismatches:
        raise RuntimeError("Notion DB 속성 타입 불일치 — DB에서 수동 수정 필요: " + "; ".join(mismatches))
    if patch:
        _request("PATCH", f"/data_sources/{ds_id}", token, {"properties": patch})
        log.info("Notion 스키마 부트스트랩: %d개 속성 생성/변경", len(patch))


def _rt(text: str) -> dict:
    if not text:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": text[:1990]}}]}  # rich_text 2,000자 제한


def exists_by_url(token: str, ds_id: str, url: str) -> bool:
    data = _request(
        "POST",
        f"/data_sources/{ds_id}/query",
        token,
        {"filter": {"property": "URL", "url": {"equals": url}}, "page_size": 1},
    )
    return bool(data.get("results"))


def save_item(token: str, ds_id: str, item: Item, date_str: str) -> bool:
    """저장했으면 True, 이미 있어서 건너뛰었으면 False."""
    if exists_by_url(token, ds_id, item.url):
        return False
    props = {
        "제목": {"title": [{"text": {"content": item.display_title[:1990]}}]},
        "원제": _rt(item.title),
        "날짜": {"date": {"start": date_str}},
        "출처": {"select": {"name": item.source[:100].replace(",", " ")}},
        "카테고리": {"select": {"name": (item.category or "미분류").replace(",", " ")}},
        "중요도": {"number": item.importance},
        "요약": _rt(item.summary_ko),
        "왜 중요한가": _rt(item.why_ko),
        "URL": {"url": item.url[:2000]},
    }
    _request(
        "POST",
        "/pages",
        token,
        {"parent": {"type": "data_source_id", "data_source_id": ds_id}, "properties": props},
    )
    return True
