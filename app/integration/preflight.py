"""Preflight 구성 점검 (PHASE 9-9C.9A) — 환경변수만 읽는 오프라인 점검.

1단계 연결 확인(S1-2)·2단계 실호출(S2-2/S2-3)에 착수하기 전에 서버 환경이
최소한의 구성 요건을 갖췄는지 확인하는 유틸리티다.

**보안 계약**(R-11과 동일 원칙):
- 환경변수의 **값은 절대 반환·출력하지 않는다**. 결과는 ``code``/``ok``/
  ``message`` 3필드뿐이고, ``message`` 는 환경변수 **이름**만 언급한다.
- ``GOOGLE_APPLICATION_CREDENTIALS`` 는 **파일 존재 여부만** 검사한다. 파일을
  열거나 내용을 읽지 않는다 (서비스 계정 JSON 열람 금지).
- 네트워크 요청을 하지 않는다 (os.environ 과 pathlib 존재 검사만 사용).

점검 항목:
- ``AI1_OCR_PROVIDER``           선택 여부 (미선택 시 `/ocr` 은 503)
- ``GOOGLE_APPLICATION_CREDENTIALS`` 설정·경로가 **파일**인지
- ``GOOGLE_VISION_TIMEOUT_SECONDS``  파싱 · 유한값 0 < 값 <= 25 (§3.2 #7)
- ``AI1_OCR_ALLOWED_HOSTS``      파싱 · bare hostname 형식 (미설정 → HTTPS-only)
- ``AI1_INTERNAL_API_KEYS``      설정 여부 (미설정 → fail-closed 503)
- ``AI1_ENABLE_DOCS``            기본 비노출 확인 (truthy 값이면 노출 상태)

호스트 파싱 규칙은 ``app/integration/host_policy.py`` 가 단일 소스다. provider
런타임도 같은 규칙을 쓰므로 preflight 의 판정과 실제 동작이 어긋나지 않는다.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from app.integration.host_policy import split_allowed_hosts

MAX_TIMEOUT_SECONDS = 25.0
DEFAULT_TIMEOUT_SECONDS = 8.0
SUPPORTED_OCR_PROVIDER = "google_vision"

ENV_OCR_PROVIDER = "AI1_OCR_PROVIDER"
ENV_TIMEOUT = "GOOGLE_VISION_TIMEOUT_SECONDS"
ENV_ALLOWED_HOSTS = "AI1_OCR_ALLOWED_HOSTS"
ENV_DOCS = "AI1_ENABLE_DOCS"

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PreflightCheck:
    """점검 결과 1건. ``message`` 에 값이 아닌 환경변수 이름만 들어간다."""

    code: str
    ok: bool
    message: str


def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _TRUTHY


def check_ocr_provider() -> PreflightCheck:
    raw = os.environ.get(ENV_OCR_PROVIDER, "").strip().lower()
    if not raw:
        return PreflightCheck(
            code="OCR_PROVIDER",
            ok=False,
            message=(f"{ENV_OCR_PROVIDER} 미선택 — 설정 전에는 /ocr 이 "
                     "503 E_OCR_NOT_CONFIGURED 로 응답한다."),
        )
    if raw == SUPPORTED_OCR_PROVIDER:
        return PreflightCheck(
            code="OCR_PROVIDER",
            ok=True,
            message=f"{ENV_OCR_PROVIDER}=google_vision 선택됨.",
        )
    return PreflightCheck(
        code="OCR_PROVIDER",
        ok=False,
        message=(f"{ENV_OCR_PROVIDER} 가 미지원 값을 담고 있다 — "
                 "지원 값은 'google_vision' 뿐이다."),
    )


def check_google_credentials() -> PreflightCheck:
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not path:
        return PreflightCheck(
            code="GOOGLE_APPLICATION_CREDENTIALS",
            ok=False,
            message=("GOOGLE_APPLICATION_CREDENTIALS 미설정 — Vision 실호출과 "
                     "S2-2 잔량 확인 전에 서비스 계정 경로가 필요하다."),
        )
    # 경로 존재만 검사한다. 파일을 절대 열지 않는다 (서비스 계정 JSON 열람 금지).
    if Path(path).is_file():
        return PreflightCheck(
            code="GOOGLE_APPLICATION_CREDENTIALS",
            ok=True,
            message=("GOOGLE_APPLICATION_CREDENTIALS 설정됨, 경로 존재 확인 "
                     "(내용은 읽지 않음)."),
        )
    return PreflightCheck(
        code="GOOGLE_APPLICATION_CREDENTIALS",
        ok=False,
        message=("GOOGLE_APPLICATION_CREDENTIALS 가 담고 있는 경로가 존재하지 "
                 "않는다. 경로를 확인하십시오 (값은 비노출)."),
    )


def check_timeout() -> PreflightCheck:
    raw = os.environ.get(ENV_TIMEOUT)
    if raw is None or not raw.strip():
        return PreflightCheck(
            code="GOOGLE_VISION_TIMEOUT_SECONDS",
            ok=True,
            message=(f"{ENV_TIMEOUT} 미설정 — 기본 {DEFAULT_TIMEOUT_SECONDS:g}"
                     "초를 사용한다 (§3.2 #7)."),
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return PreflightCheck(
            code="GOOGLE_VISION_TIMEOUT_SECONDS",
            ok=False,
            message=(f"{ENV_TIMEOUT} 가 양의 수가 아니다 — 0보다 크고 "
                     f"{MAX_TIMEOUT_SECONDS:g}초 이하인 값이 필요하다."),
        )
    if not math.isfinite(value) or value <= 0:
        return PreflightCheck(
            code="GOOGLE_VISION_TIMEOUT_SECONDS",
            ok=False,
            message=(f"{ENV_TIMEOUT} 가 양의 유한한 수가 아니다 — 0보다 크고 "
                     f"{MAX_TIMEOUT_SECONDS:g}초 이하인 값이 필요하다."),
        )
    if value > MAX_TIMEOUT_SECONDS:
        return PreflightCheck(
            code="GOOGLE_VISION_TIMEOUT_SECONDS",
            ok=False,
            message=(f"{ENV_TIMEOUT} 가 상한 {MAX_TIMEOUT_SECONDS:g}초를 "
                     "초과한다 — 초과 설정은 fail-closed 503 이다 (§3.2 #7)."),
        )
    return PreflightCheck(
        code="GOOGLE_VISION_TIMEOUT_SECONDS",
        ok=True,
        message=(f"{ENV_TIMEOUT} 유효 — 0 초과 "
                 f"{MAX_TIMEOUT_SECONDS:g}초 이하 범위 안이다."),
    )


def check_allowed_hosts() -> PreflightCheck:
    raw = os.environ.get(ENV_ALLOWED_HOSTS)
    if raw is None or not raw.strip():
        return PreflightCheck(
            code="AI1_OCR_ALLOWED_HOSTS",
            ok=True,
            message=(f"{ENV_ALLOWED_HOSTS} 미설정 — HTTPS 스킴만 검사한다 "
                     "(호스트 제한 없음). S2-3 에서 Backend 호스트를 추가한다."),
        )
    hosts, invalid_count = split_allowed_hosts(raw)
    if invalid_count:
        # provider 는 hostname exact match 이므로 스킴·경로·포트가 붙은 항목은
        # 의도한 호스트를 허용하지 못한다. 값은 메시지에 남기지 않고 개수만 알린다.
        return PreflightCheck(
            code="AI1_OCR_ALLOWED_HOSTS",
            ok=False,
            message=(f"{ENV_ALLOWED_HOSTS} 에 형식 오류 {invalid_count}개 — "
                     "스킴·경로·포트 없이 호스트 이름만 쉼표로 구분해 "
                     "넣어야 한다 (값은 비노출)."),
        )
    return PreflightCheck(
        code="AI1_OCR_ALLOWED_HOSTS",
        ok=True,
        message=(f"{ENV_ALLOWED_HOSTS} 파싱 성공 — 유효 항목 {len(hosts)}개. "
                 "호스트 값 자체는 비노출이다."),
    )


def check_internal_keys() -> PreflightCheck:
    raw = os.environ.get("AI1_INTERNAL_API_KEYS")
    keys = [part for part in (raw or "").split(",") if part.strip()]
    if not keys:
        return PreflightCheck(
            code="AI1_INTERNAL_API_KEYS",
            ok=False,
            message=("AI1_INTERNAL_API_KEYS 미설정 — 보호 라우트 전체가 "
                     "fail-closed 503 E_AUTH_NOT_CONFIGURED 로 응답한다 "
                     "(의도된 동작). 실제 키 수령(S1-3) 후 설정한다."),
        )
    return PreflightCheck(
        code="AI1_INTERNAL_API_KEYS",
        ok=True,
        message=("AI1_INTERNAL_API_KEYS 설정됨 — 유효 항목 "
                 f"{len(keys)}개. 키 값 자체는 비노출이다."),
    )


def check_docs_hidden() -> PreflightCheck:
    if _is_truthy(os.environ.get(ENV_DOCS)):
        return PreflightCheck(
            code="AI1_ENABLE_DOCS",
            ok=False,
            message=(f"{ENV_DOCS} 켜짐 — /docs·/redoc·/openapi.json 이 노출된다. "
                     "로컬 개발용이며 연동 서버에서 끈다."),
        )
    return PreflightCheck(
        code="AI1_ENABLE_DOCS",
        ok=True,
        message=f"{ENV_DOCS} 기본 비노출 유지 (로컬 개발만 opt-in).",
    )


CHECKS = (
    check_ocr_provider,
    check_google_credentials,
    check_timeout,
    check_allowed_hosts,
    check_internal_keys,
    check_docs_hidden,
)


def run_preflight() -> list[PreflightCheck]:
    """모든 점검을 실행해 결과 목록을 돌려준다. 값을 절대 반환하지 않는다."""
    return [check() for check in CHECKS]


def preflight_report() -> str:
    """한 줄 요약 텍스트. 값 비노출 계약을 유지하며 사람이 읽는 용도다."""
    checks = run_preflight()
    lines = [
        "- {}: {}".format(
            check.code, "OK" if check.ok else "NOT OK"
        )
        for check in checks
    ]
    lines.insert(0, "preflight: {}/{} checks OK".format(
        sum(1 for check in checks if check.ok), len(checks)
    ))
    return "\n".join(lines)
