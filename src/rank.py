"""LLM 중요도 랭킹 + 한국어 요약 — 백엔드 선택형.

- gemini (기본): 무료 티어. REST 직접 호출 — google-genai SDK의 의존성(cryptography)이
  이 로컬 파이썬(3.14t 32bit)에서 빌드 불가라 httpx로 직접 호출한다 (Notion과 같은 방식).
- anthropic: config의 llm_provider/model 두 줄 변경으로 전환.

공통: 100건 단위 분할, 출력 절단 시 절반 재분할, 배치 실패 1회 재시도 (운영 품질 요구사항 5).
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from src.models import CATEGORIES, Item

log = logging.getLogger("pipeline")

BATCH_SIZE = 100
MAX_TOKENS = 32000
MIN_SPLIT = 10
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_BATCH_GAP = 7  # 무료 티어 10 RPM — 배치 간 최소 간격(초)

PROVIDER_KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def key_env_for(cfg: dict) -> str:
    return PROVIDER_KEY_ENV.get(cfg.get("llm_provider", "gemini"), "GEMINI_API_KEY")


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


_ITEM_PROPS = {
    "id": {"type": "string"},
    "title_ko": {"type": "string"},
    "importance": {"type": "integer", "description": "1~5 정수"},
    "category": {"type": "string", "enum": CATEGORIES},
    "summary_ko": {"type": "string"},
    "why_it_matters_ko": {"type": "string"},
}
_ITEM_REQUIRED = list(_ITEM_PROPS)

# Anthropic 구조화 출력: 모든 object에 additionalProperties:false 필수, 숫자 제약(min/max) 미지원
# (importance 1~5 범위는 rank_items에서 클램핑으로 보장)
ANTHROPIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _ITEM_PROPS,
                "required": _ITEM_REQUIRED,
            },
        }
    },
    "required": ["items"],
}

# Gemini responseSchema: OpenAPI 서브셋 — additionalProperties 없이 구성
GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_PROPS,
                "required": _ITEM_REQUIRED,
            },
        }
    },
    "required": ["items"],
}


def _call_gemini(model: str, api_key: str, payload_json: str) -> tuple[str, bool]:
    generation_config = {
        "responseMimeType": "application/json",
        "responseSchema": GEMINI_SCHEMA,
        "maxOutputTokens": MAX_TOKENS,
    }
    if model.startswith("gemini-2.5"):
        # 요약·분류에 thinking 불필요 — thinking 토큰이 출력 한도를 잠식해 절단을 유발한다
        # (2.5 계열만 thinkingBudget 지원; 3.x는 thinkingLevel이라 미전송)
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": payload_json}]}],
        "generationConfig": generation_config,
    }
    resp = None
    for attempt in range(4):  # 무료 티어/preview 모델은 일시적 429·503이 흔함
        resp = httpx.post(
            f"{GEMINI_API}/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=300,
        )
        if resp.status_code in (429, 500, 503) and attempt < 3:
            wait = 10.0 * (attempt + 1)
            try:
                wait = max(wait, float(resp.headers.get("Retry-After", 0)))
            except ValueError:
                pass
            log.warning("Gemini %d — %.0f초 후 재시도 (%d/3)", resp.status_code, min(wait, 60), attempt + 1)
            time.sleep(min(wait, 60))
            continue
        break
    if resp.status_code >= 400:
        raise RankingError(f"Gemini API {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RankingError(f"Gemini 응답에 candidates 없음: {json.dumps(data, ensure_ascii=False)[:300]}")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    truncated = cand.get("finishReason") == "MAX_TOKENS"
    if not text and not truncated:
        # thinking 모델이 출력 없이 종료한 경우 등 — 재시도/폴백 대상으로 처리
        raise RankingError(f"Gemini 응답 텍스트 없음 (finishReason={cand.get('finishReason')})")
    return text, truncated


def _call_anthropic(model: str, api_key: str, payload_json: str) -> tuple[str, bool]:
    from anthropic import Anthropic  # 전환 시에만 import

    client = Anthropic(api_key=api_key)
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": payload_json}],
    )
    try:
        with client.messages.stream(
            **kwargs, output_config={"format": {"type": "json_schema", "schema": ANTHROPIC_SCHEMA}}
        ) as stream:
            message = stream.get_final_message()
    except TypeError:
        # SDK가 output_config를 모르는 구버전 — 프롬프트 기반 JSON 폴백
        fallback = dict(kwargs)
        fallback["system"] = (
            SYSTEM
            + "\n\n반드시 다음 JSON 스키마를 만족하는 JSON 객체만 출력하라. 그 외 텍스트 금지:\n"
            + json.dumps(ANTHROPIC_SCHEMA, ensure_ascii=False)
        )
        with client.messages.stream(**fallback) as stream:
            message = stream.get_final_message()
    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return text, message.stop_reason == "max_tokens"


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def _rank_batch(provider: str, model: str, api_key: str, batch: list[Item], results: dict) -> None:
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
    call = _call_gemini if provider == "gemini" else _call_anthropic
    text, truncated = call(model, api_key, payload)
    if truncated:
        if len(batch) <= MIN_SPLIT:
            raise RankingError("최소 배치에서도 출력 절단(max tokens)")
        mid = len(batch) // 2
        log.warning("출력 절단 — 배치 %d건을 절반으로 재분할", len(batch))
        _rank_batch(provider, model, api_key, batch[:mid], results)
        _rank_batch(provider, model, api_key, batch[mid:], results)
        return
    data = _parse_json(text)
    for rec in data.get("items", []):
        if rec.get("id"):
            results[rec["id"]] = rec


def rank_items(items: list[Item], cfg: dict, api_key: str) -> bool:
    """아이템에 랭킹·요약을 in-place 적용. 하나 이상의 배치가 성공하면 True."""
    provider = cfg.get("llm_provider", "gemini")
    model = cfg.get("model", "gemini-3-flash-preview")
    results: dict[str, dict] = {}
    ok_batches = 0
    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    fallback_model = cfg.get("fallback_model")
    for n, batch in enumerate(batches, 1):
        if provider == "gemini" and n > 1:
            time.sleep(GEMINI_BATCH_GAP)  # 무료 티어 10 RPM 준수
        for attempt in (1, 2):  # 1회 재시도
            try:
                _rank_batch(provider, model, api_key, batch, results)
                ok_batches += 1
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("LLM 배치 %d/%d 시도 %d 실패: %s", n, len(batches), attempt, exc)
        else:
            # 기본 모델이 완전히 실패 — 폴백 모델로 마지막 시도 (브리핑이 안 나오는 것이 최악)
            if fallback_model and fallback_model != model:
                try:
                    _rank_batch(provider, fallback_model, api_key, batch, results)
                    ok_batches += 1
                    log.info("폴백 모델 %s 로 배치 %d 성공", fallback_model, n)
                except Exception as exc:  # noqa: BLE001
                    log.warning("폴백 모델(%s)도 실패: %s", fallback_model, exc)
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
