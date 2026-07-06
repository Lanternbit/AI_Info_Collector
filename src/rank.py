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
놓치지 않으려는 한국인 **개발자** 1명이다 — 뉴스뿐 아니라 자신의 개발 역량에 바로 도움이 되는
실무 지식(기법·활용법·노하우)도 똑같이 중시한다.

편집 기준:
- 최전선 우선: 새 모델·새 능력·새 연구 결과 > 자금 조달·인사·가십
- 실무 지식(프롬프팅 기법, 모델·에이전트 활용법, 튜토리얼, 엔지니어링 노하우)은
  뉴스성이 없어도 실용 가치가 높으면 중요도 3~4를 줄 수 있다
- 같은 주제의 중복 보도는 대표 1건에만 최고 중요도를 주고 나머지는 낮게 매긴다
- 참여 지표(points/upvotes/likes)가 높으면 중요도 판단에 반영한다
- 본문(body)이 없거나 짧은 아이템은 제목과 지표에서 알 수 있는 사실만 서술하고 내용을 추측하지 말 것
- importance: 5=업계가 뒤집힐 소식, 4=꼭 알아야 함, 3=알아두면 좋음, 2=참고, 1=노이즈에 가까움

출력 필드:
- title_ko: 자연스러운 한국어 제목 (직역이 아니라 내용이 드러나게)
- summary_ko: 2~3문장의 자연스러운 한국어 요약
- why_it_matters_ko: "이게 왜 중요한가" 1문장
- category: 반드시 다음 중 하나 — 모델 릴리스 / 연구·논문 / 실무 지식 / 도구·오픈소스 / 업계 동향 / 정책·안전 / 커뮤니티 화제
  ("실무 지식"은 개발자가 읽고 바로 써먹을 수 있는 가이드·기법·노하우 콘텐츠)

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


def _call_gemini(model: str, api_key: str, system: str, schema: dict, payload_json: str) -> tuple[str, bool]:
    generation_config = {
        "responseMimeType": "application/json",
        "responseSchema": schema,
        "maxOutputTokens": MAX_TOKENS,
    }
    if model.startswith("gemini-2.5"):
        # 요약·분류에 thinking 불필요 — thinking 토큰이 출력 한도를 잠식해 절단을 유발한다
        # (2.5 계열만 thinkingBudget 지원; 3.x는 thinkingLevel이라 미전송)
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    body = {
        "system_instruction": {"parts": [{"text": system}]},
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


def _call_anthropic(model: str, api_key: str, system: str, schema: dict, payload_json: str) -> tuple[str, bool]:
    from anthropic import Anthropic  # 전환 시에만 import

    client = Anthropic(api_key=api_key)
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": payload_json}],
    )
    try:
        with client.messages.stream(
            **kwargs, output_config={"format": {"type": "json_schema", "schema": schema}}
        ) as stream:
            message = stream.get_final_message()
    except TypeError:
        # SDK가 output_config를 모르는 구버전 — 프롬프트 기반 JSON 폴백
        fallback = dict(kwargs)
        fallback["system"] = (
            system
            + "\n\n반드시 다음 JSON 스키마를 만족하는 JSON 객체만 출력하라. 그 외 텍스트 금지:\n"
            + json.dumps(schema, ensure_ascii=False)
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
    schema = GEMINI_SCHEMA if provider == "gemini" else ANTHROPIC_SCHEMA
    text, truncated = call(model, api_key, SYSTEM, schema, payload)
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


# ---------- 편집 패스: 오늘의 요약 + 헤드라인 엄선 ----------

EDITORIAL_SYSTEM = """너는 'AI 프론티어 데일리 브리핑'의 편집장이다. 오늘 브리핑에 실릴,
이미 랭킹이 끝난 아이템 목록을 받아 지면을 편집한다. 독자는 AI 최전선을 따라가려는
한국인 개발자 1명이다.

1. daily_summary_ko: 오늘 하루의 AI 동향을 3~4문장의 한국어로 종합하라.
   개별 뉴스의 나열이 아니라 '오늘의 큰 흐름'이 드러나게 쓰고, 가장 중요한 소식을 중심에 둔다.
2. headline_ids: '오늘의 헤드라인'에 올릴 3~5건의 id를 골라라.
   서로 다른 주제여야 하며(같은 사건의 중복 보도 금지), 최전선 임팩트가 큰 순서로 나열한다.
   개발자에게 실용 가치가 큰 실무 지식 아이템도 헤드라인이 될 수 있다."""

_EDITORIAL_PROPS = {
    "daily_summary_ko": {"type": "string"},
    "headline_ids": {"type": "array", "items": {"type": "string"}},
}
ANTHROPIC_EDITORIAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": _EDITORIAL_PROPS,
    "required": ["daily_summary_ko", "headline_ids"],
}
GEMINI_EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": _EDITORIAL_PROPS,
    "required": ["daily_summary_ko", "headline_ids"],
}


# ---------- 본문 번역: 카드 아이템의 '본문 읽기'를 한국어로 ----------

TRANSLATE_SYSTEM = """너는 전문 기술 번역가다. AI 뉴스·기술 글 본문 목록을 받아 각각을
자연스러운 한국어로 번역하라.
- 기술 용어·모델명·고유명사·코드는 원어를 유지한다
- 문단 구조(빈 줄)를 유지한다
- 요약하지 말고 전체를 번역하라
- 이미 한국어인 본문은 그대로 반환하라"""

_TRANSLATE_PROPS = {
    "id": {"type": "string"},
    "body_ko": {"type": "string"},
}
ANTHROPIC_TRANSLATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _TRANSLATE_PROPS,
                "required": ["id", "body_ko"],
            },
        }
    },
    "required": ["items"],
}
GEMINI_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": _TRANSLATE_PROPS, "required": ["id", "body_ko"]},
        }
    },
    "required": ["items"],
}

TRANSLATE_INPUT_CHARS = 6000  # 리더에 표시되는 분량만 번역
TRANSLATE_BATCH_CHARS = 10000  # 호출당 입력 한도 (출력 32K 토큰 내 안전)


def translate_bodies(targets: list[Item], cfg: dict, api_key: str) -> int:
    """카드 아이템 본문을 한국어로 번역해 body_ko에 저장. 실패한 아이템은 원문 유지."""
    provider = cfg.get("llm_provider", "gemini")
    model = cfg.get("model", "gemini-2.5-flash")
    call = _call_gemini if provider == "gemini" else _call_anthropic
    schema = GEMINI_TRANSLATE_SCHEMA if provider == "gemini" else ANTHROPIC_TRANSLATE_SCHEMA

    batches: list[list[Item]] = []
    batch: list[Item] = []
    size = 0
    for it in targets:
        body_len = len(it.body[:TRANSLATE_INPUT_CHARS])
        if batch and size + body_len > TRANSLATE_BATCH_CHARS:
            batches.append(batch)
            batch, size = [], 0
        batch.append(it)
        size += body_len
    if batch:
        batches.append(batch)

    done = 0
    for n, b in enumerate(batches, 1):
        if provider == "gemini":
            time.sleep(GEMINI_BATCH_GAP)  # 10 RPM 준수
        payload = json.dumps(
            [{"id": it.key, "body": it.body[:TRANSLATE_INPUT_CHARS]} for it in b],
            ensure_ascii=False,
        )
        try:
            text, truncated = call(model, api_key, TRANSLATE_SYSTEM, schema, payload)
            data = _parse_json(text)
        except Exception as exc:  # noqa: BLE001 — 번역 실패 시 원문 표시로 폴백
            log.warning("본문 번역 배치 %d/%d 실패: %s", n, len(batches), exc)
            continue
        by_id = {r["id"]: (r.get("body_ko") or "").strip() for r in data.get("items", []) if r.get("id")}
        for it in b:
            ko = by_id.get(it.key, "")
            if ko:
                it.body_ko = ko
                done += 1
    return done


def editorial_pass(items: list[Item], cfg: dict, api_key: str) -> str:
    """전체 아이템을 보고 오늘의 요약을 쓰고 헤드라인 3~5건을 선정(is_headline in-place).

    실패해도 브리핑은 나가야 하므로 예외 대신 빈 요약을 반환한다
    (render가 중요도 기반 헤드라인으로 폴백)."""
    ranked = [it for it in items if it.category or it.is_paper]
    if not ranked:
        return ""
    provider = cfg.get("llm_provider", "gemini")
    model = cfg.get("model", "gemini-2.5-flash")
    payload = json.dumps(
        [
            {
                "id": it.key,
                "title": it.title_ko or it.title,
                "category": it.category or ("연구·논문" if it.is_paper else ""),
                "importance": it.importance,
                "source": it.source,
            }
            for it in ranked
        ],
        ensure_ascii=False,
    )
    call = _call_gemini if provider == "gemini" else _call_anthropic
    schema = GEMINI_EDITORIAL_SCHEMA if provider == "gemini" else ANTHROPIC_EDITORIAL_SCHEMA
    try:
        if provider == "gemini":
            time.sleep(GEMINI_BATCH_GAP)  # 랭킹 호출 직후 — 10 RPM 준수
        text, _ = call(model, api_key, EDITORIAL_SYSTEM, schema, payload)
        data = _parse_json(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("편집 패스 실패 — 중요도 기반 헤드라인으로 폴백: %s", exc)
        return ""
    headline_ids = set((data.get("headline_ids") or [])[:5])
    for it in items:
        it.is_headline = it.key in headline_ids
    summary = (data.get("daily_summary_ko") or "").strip()
    log.info("편집 패스: 헤드라인 %d건 선정, 오늘의 요약 %d자", len(headline_ids), len(summary))
    return summary
