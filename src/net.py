"""HTTP 헬퍼 — 타임아웃 20초, 1회 재시도 (운영 품질 요구사항 1).

429 응답은 Retry-After를 존중해 별도로 재시도한다 (Reddit 등)."""
from __future__ import annotations

import time

import httpx


def get_with_retry(client: httpx.Client, url: str, headers: dict | None = None, retries: int = 1) -> httpx.Response:
    last_exc: Exception | None = None
    rate_limit_retries = 2
    attempt = 0
    while attempt <= retries:
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code == 429 and rate_limit_retries > 0:
                rate_limit_retries -= 1
                try:
                    wait = float(resp.headers.get("Retry-After", "10"))
                except ValueError:
                    wait = 10.0
                time.sleep(min(wait, 30))
                continue  # 레이트 리밋 재시도는 일반 재시도 횟수에서 차감하지 않음
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 — 소스별 격리는 상위에서
            last_exc = exc
            attempt += 1
            if attempt <= retries:
                time.sleep(2)
    raise last_exc  # type: ignore[misc]


def get_json(client: httpx.Client, url: str, headers: dict | None = None):
    return get_with_retry(client, url, headers=headers).json()
