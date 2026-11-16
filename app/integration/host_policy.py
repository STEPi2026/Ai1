"""OCR 허용 호스트 파싱 정책 (PHASE 9-9C.9A 후속).

``AI1_OCR_ALLOWED_HOSTS`` 는 provider 의 ``urlsplit(url).hostname`` 과
**exact match** 된다. 따라서 스킴(``https://``)·경로·포트가 붙은 항목은
절대 매칭되지 않는다. 이 모듈은 그 규칙을 한 곳에 두고 preflight(진단)와
provider(런타임)가 같은 판정을 쓰도록 한다.

값은 절대 메시지·반환에 포함하지 않는다 — 유효 항목 tuple 과 형식 오류
개수만 돌려준다. 의존성은 표준 라이브러리뿐이다.
"""
from __future__ import annotations

import re

# bare hostname 만 허용: 소문자/숫자/하이픈 라벨, 점으로 연결.
# 스킴·경로·포트·공백·언더스코어·선행/후행 하이픈·빈 라벨은 거부된다.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$"
)


def is_valid_host(host: str) -> bool:
    return bool(_HOSTNAME_RE.fullmatch(host))


def split_allowed_hosts(raw: str | None) -> tuple[tuple[str, ...], int]:
    """쉼표 목록을 (유효 호스트 tuple, 형식 오류 개수)로 나눈다.

    - 항목별 앞뒤 공백 제거, 소문자 정규화, 빈 항목 무시
    - 미설정/빈 설정 → ((), 0) — HTTPS-only 로 동작
    """
    entries = [
        part.strip().lower() for part in (raw or "").split(",") if part.strip()
    ]
    valid = tuple(entry for entry in entries if is_valid_host(entry))
    invalid_count = len(entries) - len(valid)
    return valid, invalid_count
