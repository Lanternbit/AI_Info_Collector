"""AI 프론티어 데일리 브리핑 — 파이프라인 엔트리포인트.

수집 → 최신성 필터 → 중복 제거 → LLM 랭킹·요약 → HTML 렌더 → Notion 저장.
--dry-run: Notion 저장·상태(seen.json) 갱신 없이 수집~HTML 생성까지만.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx
import yaml
from dotenv import load_dotenv

from src.dedupe import dedupe, filter_fresh, filter_seen
from src.fetchers import FETCHERS
from src.models import today_kst
from src.render import render
from src.state import load_seen, mark_seen, save_seen

log = logging.getLogger("pipeline")


def collect(cfg: dict) -> tuple[list, list[dict]]:
    client = httpx.Client(
        timeout=20, follow_redirects=True, headers={"User-Agent": cfg["user_agent"]}
    )
    all_items: list = []
    source_status: list[dict] = []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        fetcher = FETCHERS.get(src["type"])
        if fetcher is None:
            source_status.append(
                {"name": src["name"], "ok": False, "count": 0, "error": f"알 수 없는 타입: {src['type']}"}
            )
            continue
        try:
            items = fetcher(src, client, cfg)
            all_items.extend(items)
            source_status.append({"name": src["name"], "ok": True, "count": len(items), "error": ""})
            log.info("%s: %d건", src["name"], len(items))
        except Exception as exc:  # noqa: BLE001 — 소스 하나 실패가 전체를 죽이면 안 된다
            source_status.append({"name": src["name"], "ok": False, "count": 0, "error": str(exc)[:200]})
            log.warning("%s 실패: %s", src["name"], exc)
    client.close()
    return all_items, source_status


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 프론티어 데일리 브리핑 파이프라인")
    parser.add_argument("--dry-run", action="store_true", help="Notion 저장·상태 갱신 없이 실행")
    args = parser.parse_args()

    load_dotenv()
    with open("config/sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    all_items, source_status = collect(cfg)
    collected = len(all_items)

    fresh = filter_fresh(all_items, cfg.get("freshness_hours", 36))
    seen = load_seen()
    unseen = filter_seen(fresh, seen)
    unique = dedupe(unseen)
    log.info("수집 %d건 → 최신성 필터 %d건 → 미확인 %d건 → 중복 제거 %d건", collected, len(fresh), len(unseen), len(unique))

    llm_ok = False
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and unique:
        from src.rank import rank_items  # anthropic import를 키 있을 때만

        llm_ok = rank_items(unique, cfg, api_key)
        if not llm_ok:
            log.error("LLM 랭킹 전체 실패 — 원제목 폴백으로 렌더링 (운영 요구사항 5)")
    elif not api_key:
        log.warning("ANTHROPIC_API_KEY 없음 — 요약 없이 원제목만으로 렌더링")

    render(unique, source_status, llm_ok)
    log.info("HTML 생성 완료: docs/index.html")

    notion_saved = 0
    if args.dry_run:
        log.info("dry-run — Notion 저장·seen.json 갱신 건너뜀")
    else:
        token = os.environ.get("NOTION_TOKEN")
        if token:
            from src import notion_client

            try:
                ds_id = notion_client.resolve_data_source_id(token)
                notion_client.bootstrap_schema(token, ds_id)
                candidates = sorted(
                    [i for i in unique if i.importance >= cfg.get("notion_min_importance", 3)],
                    key=lambda i: (-i.importance, i.tier),
                )[: cfg.get("notion_max_items", 30)]
                for item in candidates:
                    if notion_client.save_item(token, ds_id, item, today_kst()):
                        notion_saved += 1
                    mark_seen(seen, item.key, notion_saved=True)
            except Exception as exc:  # noqa: BLE001
                log.error("Notion 저장 실패: %s", exc)
        else:
            log.warning("NOTION_TOKEN 없음 — Notion 저장 건너뜀")
        for item in unseen:
            mark_seen(seen, item.key)
        save_seen(seen)

    headline_count = sum(1 for i in unique if i.importance >= 4)
    log.info(
        "실행 리포트: 수집 %d건 → 최신성/중복 필터 후 %d건 → 헤드라인 %d건 → Notion 저장 %d건",
        collected, len(unique), headline_count, notion_saved,
    )


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    main()
