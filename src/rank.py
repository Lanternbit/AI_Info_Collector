"""LLM 중요도 랭킹 + 한국어 요약.

- 100건 단위 분할 호출, 스트리밍(대형 max_tokens 비스트리밍은 SDK가 거부)
- 구조화 출력(json_schema) 강제, stop_reason=max_tokens 시 절반 재분할
- 배치 실패 시 1회 재시도 (운영 품질 요구사항 5)
"""
from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from src.models import CATEGORIES, Item

log = logging.getLogger("pipeline")

BATCH_SIZE = 100
MAX_TOKENS = 32000
MIN_SPLIT = 10

# 주의: Anthropic 구조화 출력은 숫자 제약(minimum/maximum)을 지원하지 않고
# 모든 object에 additionalProperties: false가 필수다. importance 1~5 범위는
# rank_items에서 클램핑으로 보장한다.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "title_ko": {"type": "string"},
                    "importance": {"type": "integer", "description": "1~5 정수"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "summary_ko": {"type": "string"},
                    "why_it_matters_ko": {"type": "string"},
                },
                "required": ["id", "title_ko", "importance", "category", "summary_ko", "why_it_matters_ko"],
            },
        }
    },
    "required": ["items"],
}

SYSTEM = """너는 'AI 프론티어 데일리 브리핑'의 편집장이다. 입력으로 오늘 수집된 AI 뉴스 아이템들의
JSON 배열을 받아, 각 아이템의 중요도를 매기고 한국어로 요약한다. 독자는 AI 업계 최전선을
놓치지 않으려는 한국인 실무자 1명이다.

편집 기준:
- 최전선 우선: 새 모델·새 능력·새 연구 결과 > 자금 조달·인사·가십
- 같은 주제의 중복 보도는 대표 1건에만 최고 중요도를 주고 나머지는 낮게 매긴다
- 참여 지표(points/upvotes/likes)가 높으면 중요도 판단에 반영한다
- 본문(body)이 없거나 짧은 아이템은 제목과 지표에서 알 수 있는 사실만 서술하고 내용을 추측하지 말 것
- importance: 5=업계가 뒤집힐 소식, 4=꼭 알아야 함, 3=알아두면 좋음, 2=참고, 1=노이즈에 가까움

출력 필드:
- title_ko: 자연스러운 한국어 제목 (직역이 아니라 내용이 드러나게)
- summary_ko: 2~3문장의 자연스러운 한국어 요약
- why_it_matters_ko: "이게 왜 중요한가" 1문장
- category: 반드시 다음 중 하나 — 모델 릴리스 / 연구·논문 / 도구·오픈소스 / 업계 동향 / 정책·안전 / 커뮤니티 화제

입력의 모든 아이템에 대해 결과를 반환하라 (id를 그대로 유지)."""


class RankingError(RuntimeError):
    pass


def _response_text(message) -> str:
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def _call(client: Anthropic, model: str, payload_json: str):
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": payload_json}],
    )
    try:
        with client.messages.stream(
            **kwargs, output_config={"format": {"type": "json_schema", "schema": SCHEMA}}
        ) as stream:
            return stream.get_final_message()
    except TypeError:
        # SDK가 output_config를 모르는 구버전 — 프롬프트 기반 JSON 폴백
        fallback = dict(kwargs)
        fallback["system"] = (
            SYSTEM
            + "\n\n반드시 다음 JSON 스키마를 만족하는 JSON 객체만 출력하라. 그 외 텍스트 금지:\n"
            + json.dumps(SCHEMA, ensure_ascii=False)
        )
        with client.messages.stream(**fallback) as stream:
            return stream.get_final_message()


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def _rank_batch(client: Anthropic, model: str, batch: list[Item], results: dict) -> None:
    payload = json.dumps(
        [
            {
                "id": it.key,
                "title": it.title,
                "source": it.source,
                "published": it.published.isoformat() if it.published else None,
                "metrics": it.metrics or None,
                "body": it.body[: it.body_limit],
            }
            for it in batch
        ],
        ensure_ascii=False,
    )
    message = _call(client, model, payload)
    if message.stop_reason == "max_tokens":
        if len(batch) <= MIN_SPLIT:
            raise RankingError("최소 배치에서도 max_tokens 절단")
        mid = len(batch) // 2
        log.warning("출력 절단(stop_reason=max_tokens) — 배치 %d건을 절반으로 재분할", len(batch))
        _rank_batch(client, model, batch[:mid], results)
        _rank_batch(client, model, batch[mid:], results)
        return
    data = _parse_json(_response_text(message))
    for rec in data.get("items", []):
        if rec.get("id"):
            results[rec["id"]] = rec


def rank_items(items: list[Item], cfg: dict, api_key: str) -> bool:
    """아이템에 랭킹·요약을 in-place 적용. 하나 이상의 배치가 성공하면 True."""
    client = Anthropic(api_key=api_key)
    model = cfg.get("model", "claude-haiku-4-5")
    results: dict[str, dict] = {}
    ok_batches = 0
    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    for n, batch in enumerate(batches, 1):
        for attempt in (1, 2):  # 1회 재시도
            try:
                _rank_batch(client, model, batch, results)
                ok_batches += 1
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("LLM 배치 %d/%d 시도 %d 실패: %s", n, len(batches), attempt, exc)
    for it in items:
        rec = results.get(it.key)
        if rec:
            it.title_ko = (rec.get("title_ko") or "").strip() or it.title
            try:
                it.importance = max(1, min(5, int(rec.get("importance") or 1)))
            except (TypeError, ValueError):
                it.importance = 1
            it.category = rec.get("category") if rec.get("category") in CATEGORIES else "커뮤니티 화제"
            it.summary_ko = (rec.get("summary_ko") or "").strip()
            it.why_ko = (rec.get("why_it_matters_ko") or "").strip()
        # 결과 누락 아이템은 category를 비워 '수집된 소식 (요약 없음)' 섹션으로 보낸다
    missing = sum(1 for it in items if it.key not in results)
    if missing:
        log.warning("랭킹 결과 누락 %d건 — '요약 없음' 섹션으로 표시", missing)
    return ok_batches > 0
