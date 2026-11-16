"""PHASE 9-7 — Backend 전달용 Text Analysis Adapter (경계 전용).

역할
    AI① 내부 분석 결과(``AttemptAnalysis``)를 **Backend 로 넘길 값**으로 변환한다.
    crosswalk 변환은 이 모듈에서만 수행한다 — parser / classifier / knowledge /
    taxonomy 안에는 Backend 코드가 들어가지 않는다.

코드 매핑 상태 (approved 와 provisional 을 분리한다)
    approved   : Backend 가 승인 확정. ``backend_crosswalk`` 가 단독 소유한다.
        - ``distribution`` → ``SKL-1-004``
        - ``2.1``           → ``MIS-1-004``
    provisional: 후보. **Backend 승인 아님.** 승인되면 ``backend_crosswalk`` 로 이관한다.
        A2(2026-11-10) 이후 **표가 비어 있다**. 관계자료 xlsx 에만 있고 공식
        Seed 에 없는 코드 3건을 제거했기 때문이다:
        - ``transposition`` → ``SKL-2-037`` (제거 — Seed 는 SKL-2-035 까지)
        - ``3.1``           → ``MIS-2-001`` (제거 — Seed 는 MIS-1-023 까지)
        - ``3.2``           → ``MIS-2-002`` (제거)
        시드 밖 코드는 내보내지도, ``problem_skills`` 역방향으로 수용하지도 않는다.
    unmapped   : 표에 없음. 코드를 지어내지 않고 빈 배열로 둔다.

출력하지 않는 코드 (초안 규칙)
    - ``MIS-2-003`` : 일반 ``arithmetic_slip`` 에 자동 부여하지 않는다.
      계산 오류는 오개-concept을 부여하지 않는 것이 AI¹ 정책이다.
    - ``MIS-2-004`` (division by zero) : runtime detector 가 없다. 출력이 아니라
      협�� 아이템이며, 코드가 나오면 그 detector 가 새로 추가된 것이다.
    - ``STP-*``    : AI¹ 은 Concept/STP ID 를 생성하지 않는다. 공식 Skill code
      로 Concept 을 연결하는 책임은 Backend 에 있다.

내부 오개념 ``3.2`` 의 탐지 범위 한계 (Backend 코드 아님 — A2 로 미출력)
    detector 는 계속 ``3.1`` / ``3.2`` 를 내부 taxonomy 로 분류하지만, 공식 Seed 에
    대응 코드가 없으므로 응답의 ``official_*`` / ``provisional_*`` 에는 나가지 않는다.
    ``3.2`` 는 **항 누락 / 일부 비대칭 연산**에서만 나온다. 이항 계열 오류
    전체를 탐지한다고 주장하지 않는다. 현재 detector 기준 실측:

        2x+6=14 → 2x=14   : concept_error / 3.2  (항 ``+6`` 제거가 우변에 미반영)
        3x+5=20 → 3x=25   : concept_error / 3.1  (부호 미변경)
        2x=10   → 4x=10   : calculation / 없음    (일방 곱셈)
        2x=10   → 2x=5    : calculation / 없음    (일방 나눗셈)

    즉 일방 곱셈·나눗셈은 3.2 로 분류되지 않는다. Backend 가 이 범위를 넓은
    의미로 읽으면 안 된다.

IDENTIFIER 정책 (잠정)
    - ``attempt_id`` / ``student_id`` 는 AI¹ 내부에서 문자열로 다룬다.
      Backend 가 정수를 보내면 **십진 문자열**로 정규화하고 접두사는 붙이지 않는다.
    - 문자열 식별자는 **그대로 보존**한다. 선행 0 을 지우면 서로 다른 ID 가
      같은 key 로 합쳐지므로(``"004821"`` 과 ``"4821"``) 정규화하지 않는다.
    - ``problem_id`` 는 Backend 가 준 불투명 문자열을 그대로 쓴다.
      촬영 문제 ``captured:<id>`` 와 seed 문제 ``PRB-*`` 를 추측·병합하지 않는다.

``state`` / ``mastery`` / ``observations`` 소유권 (잠정)
    이 값들은 AI¹ 로컬 Rule/BKT 엔진 결과다. Backend 가 최종 Student State 와
    weakness 를 소유하므로, Backend 는 이 값을 최종 상태로 취급하지 않아야 한다.
    weakness 계산은 Backend responsibility(``1 - mastery``)이며 이 모듈은 계산하지 않는다.
"""
from __future__ import annotations

import re

from app.integration.backend_crosswalk import (
    APPROVED_MISCONCEPTION_CODES,
    APPROVED_SKILL_CODES,
    UNMAPPED,
    is_approved_misconception_code,
    is_approved_skill_code,
)
from app.taxonomy import is_known_skill as _is_known_skill
from app.taxonomy import is_tracking_skill as _is_tracking_skill

__all__ = [
    "APPROVED_MISCONCEPTION_CODES",
    "APPROVED_SKILL_CODES",
    "PROVISIONAL_MISCONCEPTION_CODES",
    "PROVISIONAL_SKILL_CODES",
    "FORBIDDEN_CODES",
    "UNKNOWN_SKILL_CODE",
    "BackendIdError",
    "NoTrackingSkillError",
    "UnknownSkillCodeError",
    "UnknownSkillIdError",
    "confirm_concept_ids",
    "is_approved_skill_code",
    "is_approved_misconception_code",
    "is_provisional_skill_code",
    "is_provisional_misconception_code",
    "misconception_code",
    "normalize_backend_id",
    "official_misconception_codes",
    "official_skill_codes",
    "primary_error",
    "provisional_misconception_codes",
    "provisional_skill_codes",
    "require_known_skills",
    "require_problem_skills",
    "resolve_problem_skills",
    "skill_code",
    "skill_id_for_code",
    "text_analysis_view",
]

#: Backend 승인분 재노출 (소스 오직 backend_crosswalk). 여기서 값을 늘리지 않는다.
APPROVED_SKILL_CODES = APPROVED_SKILL_CODES
APPROVED_MISCONCEPTION_CODES = APPROVED_MISCONCEPTION_CODES

#: PHASE 9-7 후보 매핑. **approved 아님** — Backend 승인 전까지 잠정 표다.
#:
#: A2(2026-11-10, Backend 확인): 관계자료 xlsx 에만 있던 아래 3건은 공식 Seed 에
#: 없다. 시드 밖 코드를 내보내거나 역방향으로 수용하지 않기로 했으므로 표에서
#: 제거했다. 근거는 `docs/AI1_Backend_회신_A1-A8_후속.md` §2.
#:
#:   - ``transposition`` → ``SKL-2-037`` (Seed: SKL-2-035 까지)
#:   - ``3.1`` → ``MIS-2-001`` (Seed: MIS-1-001~023)
#:   - ``3.2`` → ``MIS-2-002``
#:
#: 내부 taxonomy 의 Skill/오개념 자체는 그대로 동작한다. Backend 로 나가는
#: 공식/잠정 코드만 없다. 시드 편입 여부는 기획 결정 사항이다.
PROVISIONAL_SKILL_CODES: dict[str, str] = {}

PROVISIONAL_MISCONCEPTION_CODES: dict[str, str] = {}

#: 출력이 금지된 코드와 사유. 매핑 테이블에 절대 넣지 않는다.
FORBIDDEN_CODES: dict[str, str] = {
    "MIS-2-003": "일반 arithmetic_slip 에 자동 부여하지 않는다 (AI¹ 정책: 계산 오류는 오개념 없음)",
    "MIS-2-004": "runtime detector 가 없다. 협의 아이템이며 코드가 나오면 detector 가 추가된 것",
}

UNKNOWN_SKILL_CODE = "E_UNKNOWN_SKILL_CODE"

#: Backend 공식 코드 형식 (SKL/MIS). 이 형식이면 'AI¹ 내부 ID 가 아니라 코드'로 본다.
_CODE_PATTERN = re.compile(r"^(?:SKL|MIS)-\d+-\d+$")


class BackendIdError(ValueError):
    """Backend 가 준 식별자를 정규화할 수 없다 (잠정 ID 정책 위반)."""

    code = "E_INVALID_BACKEND_ID"


class UnknownSkillCodeError(ValueError):
    """crosswalk(approved + provisional)에 없는 Backend Skill code."""

    code = UNKNOWN_SKILL_CODE


class NoTrackingSkillError(ValueError):
    """Knowledge State 관측용 tracking Skill 이 하나도 없다.

    코드 이름은 기존 파이프라인 ``UntrackableSkillError`` 와 **같은** ``E_SKILL_NOT_TRACKABLE``
    을 쓴다. 하나의 사실(추적 불가)에 하나의 코드만 존재해야 하므로 새 코드를 늘리지 않는다.
    """

    code = "E_SKILL_NOT_TRACKABLE"


class UnknownSkillIdError(ValueError):
    """frozen taxonomy 에 없는 내부 Skill ID."""

    code = "E_UNKNOWN_SKILL"


def _validate(codes: dict[str, str], label: str) -> dict[str, str]:
    for skill_id, code in codes.items():
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError(f"{label} 내부 ID 가 비어 있습니다: {skill_id!r}")
        if not isinstance(code, str) or not _CODE_PATTERN.match(code):
            raise ValueError(f"Backend 공식 코드 형식이 아닙니다: {code!r}")
        if code in FORBIDDEN_CODES:
            raise ValueError(f"출력 금지 코드입니다: {code} ({FORBIDDEN_CODES[code]})")
    return dict(codes)


_validate(PROVISIONAL_SKILL_CODES, "PROVISIONAL_SKILL_CODES")
_validate(PROVISIONAL_MISCONCEPTION_CODES, "PROVISIONAL_MISCONCEPTION_CODES")


# ---------------------------------------------------------------------------
# IDENTIFIER 정규화
# ---------------------------------------------------------------------------

def normalize_backend_id(value: str | int, field: str) -> str:
    """Backend 가 준 식별자를 AI① 내부 문자열로 정규화한다.

    - ``int`` → 십진 문자열 (``4821`` → ``"4821"``). 접두사를 붙이지 않는다.
    - ``str`` → 앞뒤 공백만 제거하고 **그대로** 쓴다. 선행 0 을 지우지 않는다.
    - ``bool`` 은 정수 서브클래스이므로 명시적으로 거절한다.
    - 빈 값·공백만 있는 값·다른 타입은 422 대상이다.
    """
    if isinstance(value, bool):
        raise BackendIdError(f"{field} 는 bool 을 받을 수 없습니다: {value!r}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise BackendIdError(f"{field} 는 빈 문자열일 수 없습니다.")
        return normalized
    raise BackendIdError(
        f"{field} 는 문자열 또는 정수이어야 합니다: {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Skill code 역변환 (Backend code → AI① frozen skill_id)
# ---------------------------------------------------------------------------

#: 코드 → 내부 skill_id. approved 와 provisional 을 합친 역방향 인덱스.
#: 정방향 표(skill_id → code)를 뒤집어야 한다 — 그대로 펼치면 코드가 키로 남는다.
_SKILL_CODE_TO_ID: dict[str, str] = {
    **{
        code: skill_id
        for skill_id, code in APPROVED_SKILL_CODES.items()
    },
    **{
        code: skill_id
        for skill_id, code in PROVISIONAL_SKILL_CODES.items()
    },
}


def skill_id_for_code(code: str) -> str:
    """Backend Skill 코드 → AI① 내부 skill_id. 모르는 코드면 422 예외."""
    if not _CODE_PATTERN.match(code):
        raise UnknownSkillCodeError(f"Backend Skill 코드 형식이 아닙니다: '{code}'")
    skill_id = _SKILL_CODE_TO_ID.get(code)
    if skill_id is None:
        raise UnknownSkillCodeError(
            f"crosswalk 에 없는 Backend Skill 코드입니다: '{code}' "
            "(기본 Skill 로 치환하지 않습니다)"
        )
    return skill_id


def resolve_problem_skills(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """``problem_skills`` 를 AI① 내부 frozen skill_id 목록으로 변환한다.

    Backend Skill code(``SKL-*``/``MIS-*`` 형식)가 들어오면 crosswalk 에 있는
    코드만 역변환한다. 모르는 코드는 예외로 거절한다 — ``linear_equation`` 같은
    기본 Skill 으로 추측해서 채우지 않는다. 이미 내부 ID 인 값은 그대로 통과한다.
    """
    if not values:
        return []
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise UnknownSkillCodeError(
                f"problem_skills 항목은 문자열이어야 합니다: {type(value).__name__}"
            )
        candidate = value.strip()
        if not candidate:
            raise UnknownSkillCodeError("problem_skills 에 빈 문자열이 있습니다.")
        if _CODE_PATTERN.match(candidate):
            resolved = skill_id_for_code(candidate)
        else:
            resolved = candidate
        if resolved not in out:
            out.append(resolved)
    return out


# ---------------------------------------------------------------------------
# tracking Skill 계약 (PHASE 9-7.1)
#
# Knowledge State 는 '실제로 관측된 Skill' 키로 쌓인다. 관측 대상이 하나도 없으면
# 분석 결과는 나와도 상태가 갱신되지 않으므로, Backend 요청은 tracking Skill 을 최소
# 1개 선언해야 한다. 기본 Skill 을 추측해서 채우지 않는다.
# ---------------------------------------------------------------------------

def require_known_skills(skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """모든 ID 가 frozen taxonomy 에 등록되어 있는지 확인한다 (tracking 여부는 보지 않음).

    taxonomy 에 없는 ID 는 ``Problem`` 생성 전에 거절해야 한다 — 그대로 두면
    pydantic ``ValidationError`` 가 500 으로 새어 나가 422 계약이 깨진다.
    """
    ids = list(skill_ids or ())
    unknown = [sid for sid in ids if not _is_known_skill(sid)]
    if unknown:
        raise UnknownSkillIdError(
            f"taxonomy에 없는 skill_id: {', '.join(repr(u) for u in unknown)}"
        )
    return ids


def require_problem_skills(skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """문제 선언분이 Knowledge State 관측에 쓸 수 있는지 검증한다.

    규칙
      1. 모든 ID 는 frozen taxonomy 에 등록되어 있어야 한다 (아니면 ``E_UNKNOWN_SKILL``).
      2. group node 는 함께 선언해도 된다. 다만 Knowledge State 는 관측하지 않는다.
      3. tracking Skill 이 **최소 1개** 있어야 한다 (아니면 ``E_SKILL_NOT_TRACKABLE``).
         누락·빈 목록도 여기에 해당한다.
      4. 기본 Skill 을 추측해 넣지 않는다.

    **검증 순서에 주의.** 엔드포인트는 taxonomy 미등록 ID 만 이 단계에서 미리 거절하고,
    tracking Skill 부재는 파싱·검증(``E_UNRECOGNIZED`` / ``E_UNVERIFIABLE`` /
    ``E_EMPTY_SOLUTION`` / ``E_INPUT_TOO_LARGE``) **이후** ``primary_tracking_skill()`` 이
    같은 코드 ``E_SKILL_NOT_TRACKABLE`` 로 거절한다. 입력 오류가 먼저 와야 하는 기존
    오류 우선순위를 바꾸지 않기 위한 것이며, 최종 오류 코드·HTTP 상태는 동일하다.
    """
    ids = require_known_skills(skill_ids)
    if not any(_is_tracking_skill(sid) for sid in ids):
        raise NoTrackingSkillError(
            "Knowledge State 관측용 tracking Skill 이 없습니다: "
            f"problem_skills={ids or '(빈 목록)'} — 기본 Skill 은 추측하지 않습니다"
        )
    return ids


# ---------------------------------------------------------------------------
# 정방향 매핑 (내부 ID → Backend code)
# ---------------------------------------------------------------------------

def skill_code(skill_id: str | None) -> str | None:
    """AI① skill_id → Backend Skill 코드 (approved 먼저, 없으면 provisional).

    둘 다 없으면 ``UNMAPPED``(``None``). 코드를 만들어내지 않는다.
    """
    if not skill_id:
        return UNMAPPED
    if skill_id in APPROVED_SKILL_CODES:
        return APPROVED_SKILL_CODES[skill_id]
    return PROVISIONAL_SKILL_CODES.get(skill_id, UNMAPPED)


def misconception_code(misconception_id: str | None) -> str | None:
    """AI① misconception_id → Backend Misconception 코드 (approved → provisional)."""
    if not misconception_id:
        return UNMAPPED
    if misconception_id in APPROVED_MISCONCEPTION_CODES:
        return APPROVED_MISCONCEPTION_CODES[misconception_id]
    return PROVISIONAL_MISCONCEPTION_CODES.get(misconception_id, UNMAPPED)


def is_provisional_skill_code(code: str | None) -> bool:
    return code is not None and code in set(PROVISIONAL_SKILL_CODES.values())


def is_provisional_misconception_code(code: str | None) -> bool:
    return code is not None and code in set(PROVISIONAL_MISCONCEPTION_CODES.values())


def _split_codes(
    ids: list[str] | tuple[str, ...] | None, mapper
) -> tuple[list[str], list[str]]:
    """(approved 코드 목록, provisional 코드 목록) 으로 나눈다.

    중복은 최초 등장 순서대로 제거한다(입력 순서가 결과 순서를 결정한다).
    매핑이 없는 ID 는 양쪽 모두에서 제외된다 — 지어내지 않는다.
    """
    official: list[str] = []
    provisional: list[str] = []
    for internal_id in ids or ():
        code = mapper(internal_id)
        if code is None:
            continue
        target = (
            official
            if is_approved_skill_code(code) or is_approved_misconception_code(code)
            else provisional
        )
        if code not in target:
            target.append(code)
    return official, provisional


def official_skill_codes(skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """Backend 승인 Skill 코드만. 없으면 ``[]``."""
    return _split_codes(skill_ids, skill_code)[0]


def official_misconception_codes(
    misconception_ids: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Backend 승인 Misconception 코드만. 없으면 ``[]``."""
    return _split_codes(misconception_ids, misconception_code)[0]


def provisional_skill_codes(skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """후보(미승인) Skill 코드만. 없으면 ``[]``."""
    return _split_codes(skill_ids, skill_code)[1]


def provisional_misconception_codes(
    misconception_ids: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """후보(미승인) Misconception 코드만. 없으면 ``[]``."""
    return _split_codes(misconception_ids, misconception_code)[1]


# ---------------------------------------------------------------------------
# 대표 오류 (policy: 가장 낮은 step_no)
# ---------------------------------------------------------------------------

def primary_error(errors: list | tuple | None):
    """독립 오류 중 ``step_no`` 가 가장 작은 1건을 대표 오류로 고른다.

    단계 번호는 1-based 다. 모든 독립 오류는 ``errors[]`` 에 그대로 보존하고
    여기서는 대표 **선택**만 한다(버리지 않는다). ``confidence`` 는 이 오류의
    분류 confidence 를 그대로 쓰며 합산·평균하지 않는다.
    """
    if not errors:
        return None
    return min(errors, key=lambda err: err.step_no)


def confirm_concept_ids(errors: list | tuple | None) -> list[str]:
    """확정된 오류 근거에 붙은 내부 오개-concept ID 전체(오름차순).

    확정 오류(``errors[]``)에 붙은 것만 모은다. 계산 오류처럼 오개념 ID 가 없는
    경우는 빈 목록이 되고, ``comprehension`` 에 임의 오개념을 붙이지 않는다.
    """
    if not errors:
        return []
    return sorted({mid for err in errors for mid in (err.misconception_ids or ())})


# ---------------------------------------------------------------------------
# Backend 전달 뷰
# ---------------------------------------------------------------------------

def text_analysis_view(analysis) -> dict:
    """분석 결과에서 Backend 전달용 additive 필드 5개를 만든다.

    저장된 ``AttemptAnalysis`` 에서 **매번 재생성**한다(저장하지 않는다).
    POST 응답 / 멱등 재응답 / 분석 상세 GET 이 같은 입력을 받으므로 세 경로의
    매핑 결과는 동일하다. 읽기 전용 — Knowledge State 를 건드리지 않는다.

    반환 키
        official_skill_codes / official_misconception_codes   : Backend 승인분
        provisional_skill_codes / provisional_misconception_codes : 후보(미승인)
        misconception_tag : 앱 r48 호환 단수 필드. 대표 오류의 내부 오개념 ID
                            또는 ``null`` (복수 ``misconception_tags`` 는 유지)
    """
    api = analysis.api_response or {}
    skill_ids = api.get("skill_ids") or ([api["skill"]] if api.get("skill") else [])
    confirmed = confirm_concept_ids(analysis.errors)
    off_skill, prov_skill = _split_codes(skill_ids, skill_code)
    off_mis, prov_mis = _split_codes(confirmed, misconception_code)
    primary = primary_error(analysis.errors)
    tag = None
    if primary is not None and primary.misconception_ids:
        tag = primary.misconception_ids[0]
    return {
        "official_skill_codes": off_skill,
        "official_misconception_codes": off_mis,
        "provisional_skill_codes": prov_skill,
        "provisional_misconception_codes": prov_mis,
        "misconception_tag": tag,
    }