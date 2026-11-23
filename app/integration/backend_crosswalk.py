"""Backend 연동 승인 코드 crosswalk.

역할
    AI① 의 frozen taxonomy ID 를 Backend 공식 코드로 변환한다.
    **Backend-facing 출력 경계에서만** 호출한다.

원칙 (PHASE 9-4A)
    1. AI① 의 frozen taxonomy ID 와 Knowledge State 키는 그대로 유지한다.
    2. 변환은 이 모듈(경계 adapter)에서만 수행한다.
    3. 공식 매핑이 없는 내부 ID 는 임의 변환하지 않고 ``None`` / unmapped 로 남긴다.
    4. ``SIGN_ERROR`` 같은 AI 태그는 공식 MIS 코드로 사용하지 않는다.
    5. 기존 API 응답·taxonomy JSON 은 변경하지 않는다.
    6. Backend ``AIAnalysisResponse`` 전체 조립은 하지 않는다
       (``analysis_status`` / ``confidence`` / multi-error 계약 미확정).

Concept(STP) 처리
    STP 코드는 AI① 이 반환하지 않는다. Backend 가 공식 Skill-Concept 관계에서
    도출한다. 따라서 이 모듈에는 concept 변환 함수가 없다.

출처
    Backend 공식 코드: ``AI1_공식_STP_SKL_MIS_관계자료.xlsx``
    (원본 ``STEPi 기능명세서.xlsx`` 기준 데이터 전사)
    Backend 계약: ``app/schemas/ai/analysis.py`` — ``AIAnalysisTarget.code``
"""
from __future__ import annotations

import re

from app.taxonomy import is_known_skill as _is_known_skill
from app.taxonomy import is_tracking_skill as _is_tracking_skill

__all__ = [
    "APPROVED_SKILL_CODES",
    "APPROVED_SKILL_CODE_TO_ID",
    "APPROVED_MISCONCEPTION_CODES",
    "UNMAPPED",
    "misconception_code",
    "skill_code",
    "mapped_skill_codes",
    "mapped_misconception_codes",
    "is_approved_skill_code",
    "is_approved_misconception_code",
]

#: 미매핑을 나타내는 값. 코드 추측을 하지 않고 빈 상태로 남긴다.
UNMAPPED: None = None

#: Backend 가 승인한 Skill 매핑만 담는다. Backend 승인 외에는 추가 금지.
#:
#: 정방향(AI① skill_id → Backend 코드)은 N:1 공유 Skill 의 **대표 코드 1개**만
#: 내보낸다. 예: polynomial_addition 은 SKL-1-002 로 표시하고, 입력으로는
#: SKL-1-003 도 받는다(역방향 표 참조). 2026-11-23 Backend 승인.
APPROVED_SKILL_CODES: dict[str, str] = {
    "distribution": "SKL-1-004",  # 다항식의 곱 전개하기
    "polynomial_addition": "SKL-1-002",  # 다항식의 덧셈 수행하기(대표)
    "polynomial_multiplication": "SKL-1-005",  # 전개식 정리하기
    "factoring_common": "SKL-1-023",  # 공통인수로 묶기
    "factoring_formula": "SKL-1-025",  # 인수분해 공식 적용하기(대표)
}

#: Backend 코드 → AI① skill_id 역방향 승인표. N:1(여러 코드 → 한 Skill)을 허용한다.
#: 정방향 표를 단순히 뒤집으면 003/026 이 사라지므로 별도 표로 둔다.
APPROVED_SKILL_CODE_TO_ID: dict[str, str] = {
    "SKL-1-002": "polynomial_addition",
    "SKL-1-003": "polynomial_addition",
    "SKL-1-004": "distribution",
    "SKL-1-005": "polynomial_multiplication",
    "SKL-1-023": "factoring_common",
    "SKL-1-025": "factoring_formula",
    "SKL-1-026": "factoring_formula",
}

#: Backend 가 승인한 오개념 매핑만 담는다. Backend 승인 외에는 추가 금지.
APPROVED_MISCONCEPTION_CODES: dict[str, str] = {
    "2.1": "MIS-1-004",  # 분배법칙 적용 오류
}

# Backend 공식 코드 형식. AI 태그(SIGN_ERROR 등)는 이 형식이 아니므로 걸러진다.
_CODE_PATTERN = re.compile(r"^(?:SKL|MIS)-\d+-\d+$")


def _validate(codes: dict[str, str]) -> dict[str, str]:
    for skill_id, code in codes.items():
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError(f"내부 ID 가 비어 있습니다: {skill_id!r}")
        if not isinstance(code, str) or not _CODE_PATTERN.match(code):
            raise ValueError(f"Backend 공식 코드 형식이 아닙니다: {code!r}")
    return dict(codes)


def _validate_reverse(codes: dict[str, str]) -> dict[str, str]:
    """역방향 표(코드 → 내부 ID)의 형식과 정방향 표와의 정합성을 검사한다."""
    for code, skill_id in codes.items():
        if not isinstance(code, str) or not _CODE_PATTERN.match(code):
            raise ValueError(f"Backend 공식 코드 형식이 아닙니다: {code!r}")
        if not isinstance(skill_id, str) or not _is_known_skill(skill_id):
            raise ValueError(f"taxonomy 에 없는 skill_id 입니다: {skill_id!r}")
        if not _is_tracking_skill(skill_id):
            raise ValueError(
                f"Knowledge State 관측 대상이 아닌 skill 은 역변환 대표가 될 수 없습니다: "
                f"{skill_id!r}"
            )
    for skill_id, code in APPROVED_SKILL_CODES.items():
        if codes.get(code) != skill_id:
            raise ValueError(
                f"정방향 대표 코드가 역방향 표와 일치하지 않습니다: "
                f"{skill_id!r} → {code!r}"
            )
    if set(codes.values()) != set(APPROVED_SKILL_CODES):
        raise ValueError("역방향 표의 내부 ID 집합이 정방향 표와 다릅니다.")
    return dict(codes)


_validate(APPROVED_SKILL_CODES)
_validate(APPROVED_MISCONCEPTION_CODES)
_validate_reverse(APPROVED_SKILL_CODE_TO_ID)


def skill_code(skill_id: str | None) -> str | None:
    """AI① skill_id → Backend Skill 코드.

    승인된 매핑이 없으면 ``None`` 을 반환한다 (추측·생성 금지).
    """
    if not skill_id:
        return UNMAPPED
    return APPROVED_SKILL_CODES.get(skill_id, UNMAPPED)


def misconception_code(misconception_id: str | None) -> str | None:
    """AI① misconception_id → Backend Misconception 코드.

    승인된 매핑이 없으면 ``None`` 을 반환한다. 계산 오류처럼 taxonomy 은
    오개념을 부여하지 않는 경우도 ``None`` 이며, 이는 정상 상태다.
    """
    if not misconception_id:
        return UNMAPPED
    return APPROVED_MISCONCEPTION_CODES.get(misconception_id, UNMAPPED)


def mapped_skill_codes(skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """skill_ids 목록 중 승인된 것만 Backend 코드로 변환한다.

    미매핑 ID 는 결과에서 제외된다(임의 변환하지 않기 위함).
    Caller 가 "미매핑이 있었다"는 사실을 알 수 없으므로, 집계가 필요하면
    :func:`skill_code` 를 항목별로 호출해 ``None`` 을 확인한다.
    """
    if not skill_ids:
        return []
    out: list[str] = []
    for skill_id in skill_ids:
        code = skill_code(skill_id)
        if code is not None and code not in out:
            out.append(code)
    return out


def mapped_misconception_codes(
    misconception_ids: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """misconception_ids 목록 중 승인된 것만 Backend 코드로 변환한다(중복 제거)."""
    if not misconception_ids:
        return []
    out: list[str] = []
    for misconception_id in misconception_ids:
        code = misconception_code(misconception_id)
        if code is not None and code not in out:
            out.append(code)
    return out


def is_approved_skill_code(code: str | None) -> bool:
    """Backend 승인 목록에 있는 코드인지 (역방향 승인표 기준)."""
    return code is not None and code in APPROVED_SKILL_CODE_TO_ID


def is_approved_misconception_code(code: str | None) -> bool:
    """Backend 승인 목록에 있는 코드인지."""
    return code is not None and code in set(APPROVED_MISCONCEPTION_CODES.values())