"""Internal service-to-service authentication for the AI1 API (PHASE 9-9C.8).

Contract source: Backend-AI1 연동협의서 (2026-11-03) §3.1.

- Header ``X-Internal-Api-Key`` (Backend-generated ``secrets.token_urlsafe(32)``).
- AI1 stores comma-separated keys in ``AI1_INTERNAL_API_KEYS``; during key
  rotation old and new keys are accepted at the same time.
- Missing header / no match -> HTTP 401 ``E_AUTH_INVALID_KEY`` (Backend never
  retries a 401). 403 is not used.
- Unset/empty ``AI1_INTERNAL_API_KEYS`` -> fail-closed: every protected route
  returns HTTP 503 ``E_AUTH_NOT_CONFIGURED``. Distinct from
  ``E_OCR_NOT_CONFIGURED`` by code.
- The value is compared with ``secrets.compare_digest`` against *every*
  configured key (no early exit on match).
- Key/header values, and the credential path, never appear in responses or
  logs: error messages reference the header/env variable names only.
- ``/health`` is the only exempt route; legacy routes are protected too.
"""
from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Header

ENV_KEYS = "AI1_INTERNAL_API_KEYS"
HEADER_NAME = "X-Internal-Api-Key"

E_AUTH_INVALID_KEY = "E_AUTH_INVALID_KEY"
E_AUTH_NOT_CONFIGURED = "E_AUTH_NOT_CONFIGURED"


def _configured_keys() -> list[bytes]:
    """환경변수를 매 요청마다 다시 읽는다 — 키 교체가 재시작 없이 반영된다.

    쉼표로 여러 키를 허용하고(교체 기간 구·신 동시 허용), 각 항목의 양쪽
    공백을 벗겨 낸다. 빈 항목은 무시한다. 값 자체를 로그/응답에 남기지 않는다.
    """
    raw = os.environ.get(ENV_KEYS)
    if raw is None:
        return []
    return [part.strip().encode("utf-8") for part in raw.split(",") if part.strip()]


def require_internal_auth(
    x_internal_api_key: str | None = Header(default=None, alias=HEADER_NAME),
) -> None:
    """FastAPI dependency: 보호 경로 전체에 부착한다.

    dependency 는 endpoint 본문보다 먼저 실행되므로, 이 검사는 provider
    초기화·호출·budget 예약보다 항상 선행한다.
    """
    keys = _configured_keys()
    if not keys:
        raise HTTPException(
            status_code=503,
            detail={
                "code": E_AUTH_NOT_CONFIGURED,
                "message": (
                    "AI1 서버에 인증 키가 설정되지 않았습니다 "
                    f"({ENV_KEYS}). 서비스 환경설정을 확인하십시오."
                ),
            },
        )

    provided = (x_internal_api_key or "").strip().encode("utf-8")
    # Constant-time comparison against every configured key — vault-safe
    # practice even inside a private network. No early exit on first match.
    matched = False
    for key in keys:
        if secrets.compare_digest(key, provided):
            matched = True
    if not matched:
        raise HTTPException(
            status_code=401,
            detail={
                "code": E_AUTH_INVALID_KEY,
                "message": (
                    "인증에 실패했습니다. 유효한 X-Internal-Api-Key 헤더가 필요합니다."
                ),
            },
        )
