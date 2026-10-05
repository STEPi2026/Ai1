"""공통수학1 taxonomy 정적 데이터 모델과 검증 헬퍼.

data/skills.json · data/error_types.json · data/misconceptions.json을 Pydantic로
검증하며, 기존 API 스키마(app/schemas.py)와 충돌하지 않는다.

- skill_id는 BKT Knowledge State의 key로 사용되므로 확정 후 변경 금지.
- error_type_id 값은 기존 API 응답의 error_type 필드 값과 동일 문자열(하위호환).
- 오류 '형태'(error_type)와 개념적 '원인'(misconception)은 분리되어 있다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 기존 코드에 하드코딩된 값 — 삭제/개명 금지 (knowledge.py, classifier.py 참조)
LEGACY_SKILL_IDS = ("linear_equation", "transposition", "distribution", "arithmetic")
LEGACY_MISCONCEPTIONS = {
    "2.1": "분배법칙 누락",
    "3.1": "이항 시 부호 미변경",
}

# 기존 API 응답의 error_type 허용값 (schemas.AnalyzeSolutionResponse.error_type)
API_ERROR_TYPES = ("calculation", "concept_error", "procedure", "comprehension")

# 공통수학1 대단원 (기본 목록 허용 단원)
BASE_UNITS = (
    "기초 (선행 복습)",
    "다항식의 연산과 인수분해",
    "방정식과 부등식",
    "집합과 명제",
    "함수",
)

# 공통수학1 범위 밖 키워드 — 기본 목록에 포함되면 안 됨
OUT_OF_SCOPE_KEYWORDS = ("지수", "로그", "삼각", "수열")


class Skill(BaseModel):
    skill_id: str
    name: str
    description: str
    unit: str
    parent_skill_id: str | None = None
    prerequisite_skill_ids: list[str] = Field(default_factory=list)
    bkt_eligible: bool
    status: Literal["active", "future"] = "active"


class ErrorSubtype(BaseModel):
    error_subtype_id: str
    name: str
    description: str
    status: Literal["active", "future"] = "active"


class ErrorType(BaseModel):
    error_type_id: str
    name: str
    description: str
    subtypes: list[ErrorSubtype] = Field(default_factory=list)


class Misconception(BaseModel):
    misconception_id: str
    name: str
    description: str
    related_skill_ids: list[str] = Field(default_factory=list)
    typical_error_types: list[str] = Field(default_factory=list)
    typical_error_subtypes: list[str] = Field(default_factory=list)
    example_patterns: list[str] = Field(default_factory=list)


class SkillCatalog(BaseModel):
    skills: list[Skill] = Field(default_factory=list)
    expansion_skills: list[Skill] = Field(default_factory=list)


class ErrorTypeCatalog(BaseModel):
    error_types: list[ErrorType] = Field(default_factory=list)


class MisconceptionCatalog(BaseModel):
    misconceptions: list[Misconception] = Field(default_factory=list)


class AnalysisExtension(BaseModel):
    """기존 AnalyzeSolutionResponse에 additive로 추가할 확장 필드 설계 (미적용).

    기존 필드(correct, error_step, error_type, skill, state, mastery,
    misconception_id, confidence, steps_latex, valid, unrecognized)는 절대
    삭제/개명하지 않는다. 아래 필드들은 신규 키로만 추가한다.
    """

    skills: list[str] = Field(default_factory=list, description="문제가 사용하는 전체 skill_id (요구사항 6)")
    skill_ids: list[str] = Field(default_factory=list, description="오류와 연결된 skill_id 목록 (요구사항 7)")
    error_subtype: str | None = Field(None, description="오류 세부 형태 (error_subtype_id)")
    misconception_ids: list[str] = Field(default_factory=list, description="오류와 연결된 오개념 목록")


# ---------------------------------------------------------------------------
# 로더 (Pydantic 검증 포함)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_skill_catalog(data_dir: Path = DATA_DIR) -> SkillCatalog:
    raw = _load_json(data_dir / "skills.json")
    return SkillCatalog(skills=raw.get("skills", []), expansion_skills=raw.get("expansion_skills", []))


def load_error_type_catalog(data_dir: Path = DATA_DIR) -> ErrorTypeCatalog:
    raw = _load_json(data_dir / "error_types.json")
    return ErrorTypeCatalog(error_types=raw.get("error_types", []))


def load_misconception_catalog(data_dir: Path = DATA_DIR) -> MisconceptionCatalog:
    raw = _load_json(data_dir / "misconceptions.json")
    return MisconceptionCatalog(misconceptions=raw.get("misconceptions", []))


# ---------------------------------------------------------------------------
# 검증 헬퍼 (실패 사유 문자열 목록 반환 — 비어 있으면 통과)
# ---------------------------------------------------------------------------

def find_duplicate_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for i in ids:
        if i in seen and i not in dupes:
            dupes.append(i)
        seen.add(i)
    return dupes


def all_skill_ids(catalog: SkillCatalog) -> list[str]:
    return [s.skill_id for s in catalog.skills] + [s.skill_id for s in catalog.expansion_skills]


def all_error_type_ids(catalog: ErrorTypeCatalog) -> list[str]:
    return [e.error_type_id for e in catalog.error_types]


def all_error_subtype_ids(catalog: ErrorTypeCatalog) -> list[str]:
    return [st.error_subtype_id for e in catalog.error_types for st in e.subtypes]


def check_skill_integrity(catalog: SkillCatalog) -> list[str]:
    issues: list[str] = []
    ids = all_skill_ids(catalog)
    issues += [f"중복 skill_id: {d}" for d in find_duplicate_ids(ids)]
    id_set = set(ids)
    for s in catalog.skills + catalog.expansion_skills:
        if s.parent_skill_id is not None and s.parent_skill_id not in id_set:
            issues.append(f"{s.skill_id}: parent_skill_id '{s.parent_skill_id}' 없음")
        for p in s.prerequisite_skill_ids:
            if p not in id_set:
                issues.append(f"{s.skill_id}: prerequisite_skill_ids '{p}' 없음")
        if not isinstance(s.bkt_eligible, bool):
            issues.append(f"{s.skill_id}: bkt_eligible이 boolean이 아님")
    return issues


def check_error_type_integrity(catalog: ErrorTypeCatalog) -> list[str]:
    issues: list[str] = []
    type_ids = all_error_type_ids(catalog)
    subtype_ids = all_error_subtype_ids(catalog)
    issues += [f"중복 error_type_id: {d}" for d in find_duplicate_ids(type_ids)]
    issues += [f"중복 error_subtype_id: {d}" for d in find_duplicate_ids(subtype_ids)]
    for t in type_ids:
        if t not in API_ERROR_TYPES:
            issues.append(f"기존 API 계약에 없는 error_type_id: {t}")
    return issues


def check_misconception_integrity(
    mcat: MisconceptionCatalog, scat: SkillCatalog, ecat: ErrorTypeCatalog
) -> list[str]:
    issues: list[str] = []
    ids = [m.misconception_id for m in mcat.misconceptions]
    issues += [f"중복 misconception_id: {d}" for d in find_duplicate_ids(ids)]
    skill_set = set(all_skill_ids(scat))
    type_set = set(all_error_type_ids(ecat))
    # S9: subtype -> 소속 error_type 역방향 맵. subtype이 '존재하는지'만
    # 보는 것으로는 부족하고, 이 misconception이 선언한 typical_error_types에
    # 실제로 속하는지까지 확인해야 한다.
    parent_type_of: dict[str, str] = {
        st.error_subtype_id: e.error_type_id
        for e in ecat.error_types
        for st in e.subtypes
    }
    for m in mcat.misconceptions:
        if not m.misconception_id.strip():
            issues.append(f"misconception_id 가 비어 있음: {m.misconception_id!r}")
        if not m.name.strip():
            issues.append(f"{m.misconception_id}: name 이 비어 있음")
        for sk in m.related_skill_ids:
            # S9 정책: related_skill_ids는 '개념적 관계'이므로 group node
            # (bkt_eligible=false, 예: factoring)를 허용한다. 존재하지 않는
            # Skill만 거부한다. BKT tracking eligibility는 knowledge 계층의
            # validate_tracking_skill()이 별도로 강제한다 — 두 관심사를
            # 혼동하지 않는다.
            if sk not in skill_set:
                issues.append(f"{m.misconception_id}: related_skill_ids '{sk}' 없음")
        for et in m.typical_error_types:
            if et not in type_set:
                issues.append(f"{m.misconception_id}: typical_error_types '{et}' 없음")
        for st in m.typical_error_subtypes:
            if st not in parent_type_of:
                issues.append(f"{m.misconception_id}: typical_error_subtypes '{st}' 없음")
                continue
            parent = parent_type_of[st]
            if parent not in m.typical_error_types:
                issues.append(
                    f"{m.misconception_id}: typical_error_subtypes '{st}' 는 "
                    f"'{parent}' 소속이지만 typical_error_types 에 없음 "
                    f"(현재 {m.typical_error_types})"
                )
        issues += _check_example_patterns(m)
    return issues


# S9 설계 결정: 현재 frozen taxonomy의 19개 오개념은 전부 example_patterns를
# 2개 이상 갖고 있다(최소 2개 확인). taxonomy 설계 원칙상 '구체적 오류 형태를
# 최소 1개는 예시로 남겨야' 재사용 가능하므로 빈 목록을 실패로 본다.
# 새 항목을 추가할 때 예시를 빼고 싶다면 이 정책도 함께 동결해야 한다.
_MIN_EXAMPLE_PATTERNS = 1


def _check_example_patterns(m: "Misconception") -> list[str]:
    issues: list[str] = []
    for i, pattern in enumerate(m.example_patterns):
        if not pattern.strip():
            issues.append(f"{m.misconception_id}: example_patterns[{i}] 가 빈 문자열")
    if len(m.example_patterns) < _MIN_EXAMPLE_PATTERNS:
        issues.append(
            f"{m.misconception_id}: example_patterns 가 비어 있음 "
            f"(최소 {_MIN_EXAMPLE_PATTERNS}개 필요)"
        )
    return issues


def check_legacy_preserved(scat: SkillCatalog, mcat: MisconceptionCatalog) -> list[str]:
    issues: list[str] = []
    skill_set = set(all_skill_ids(scat))
    for legacy in LEGACY_SKILL_IDS:
        if legacy not in skill_set:
            issues.append(f"기존 skill_id 삭제됨: {legacy}")
    mis_map = {m.misconception_id: m for m in mcat.misconceptions}
    for mid, expected_name in LEGACY_MISCONCEPTIONS.items():
        if mid not in mis_map:
            issues.append(f"기존 misconception_id 삭제됨: {mid}")
        elif expected_name not in mis_map[mid].name:
            issues.append(
                f"기존 misconception_id {mid} 의미 변경됨: '{mis_map[mid].name}' (기대 '{expected_name}')"
            )
    return issues


def check_common_math1_scope(scat: SkillCatalog, mcat: MisconceptionCatalog) -> list[str]:
    """공통수학1 범위 밖 taxonomy가 기본 목록에 포함되지 않았는지 검사."""
    issues: list[str] = []
    for s in scat.skills:
        if s.status != "active":
            issues.append(f"기본 목록 skill '{s.skill_id}' 가 active가 아님")
        if s.unit not in BASE_UNITS:
            issues.append(f"기본 목록 skill '{s.skill_id}' 의 unit '{s.unit}' 이(가) 공통수학1 대단원이 아님")
        text = f"{s.name} {s.description}"
        for kw in OUT_OF_SCOPE_KEYWORDS:
            if kw in text:
                issues.append(f"기본 목록 skill '{s.skill_id}' 에 범위 밖 키워드 '{kw}' 포함")
    base_ids = {s.skill_id for s in scat.skills}
    for s in scat.expansion_skills:
        if s.skill_id in base_ids:
            issues.append(f"확장 영역 skill '{s.skill_id}' 이(가) 기본 목록에 중복 포함")
    for m in mcat.misconceptions:
        text = f"{m.name} {m.description}"
        for kw in OUT_OF_SCOPE_KEYWORDS:
            if kw in text:
                issues.append(f"기본 목록 misconception '{m.misconception_id}' 에 범위 밖 키워드 '{kw}' 포함")
        for sk in m.related_skill_ids:
            if sk not in base_ids:
                issues.append(
                    f"기본 목록 misconception '{m.misconception_id}' 이(가) 기본 목록 밖 skill '{sk}' 를 참조"
                )
    return issues


def validate_all(data_dir: Path = DATA_DIR) -> list[str]:
    scat = load_skill_catalog(data_dir)
    ecat = load_error_type_catalog(data_dir)
    mcat = load_misconception_catalog(data_dir)
    return (
        check_skill_integrity(scat)
        + check_error_type_integrity(ecat)
        + check_misconception_integrity(mcat, scat, ecat)
        + check_legacy_preserved(scat, mcat)
        + check_common_math1_scope(scat, mcat)
    )


# ---------------------------------------------------------------------------
# runtime 조회 계층 (S3)
#
# frozen taxonomy(data/*.json)이 runtime source of truth다.
# 코드에는 ID를 새로 정의하지 않고 여기서만 조회한다.
# ---------------------------------------------------------------------------

class TaxonomyLookupError(LookupError):
    """taxonomy 조회 실패 공통 예외."""


class SkillNotFoundError(TaxonomyLookupError):
    """taxonomy에 없는 skill_id."""


class SkillNotTrackableError(TaxonomyLookupError):
    """taxonomy에는 있지만 Knowledge State 추적 대상이 아닌 skill_id."""


class UnknownErrorTypeError(TaxonomyLookupError):
    """taxonomy에 없는 error_type."""


class UnknownMisconceptionError(TaxonomyLookupError):
    """taxonomy에 없는 misconception_id."""


SKILL_CATALOG = load_skill_catalog()
ERROR_TYPE_CATALOG = load_error_type_catalog()
MISCONCEPTION_CATALOG = load_misconception_catalog()

SKILLS_BY_ID: dict[str, Skill] = {
    s.skill_id: s for s in SKILL_CATALOG.skills + SKILL_CATALOG.expansion_skills
}
ERROR_SUBTYPES_BY_TYPE: dict[str, frozenset[str]] = {
    e.error_type_id: frozenset(st.error_subtype_id for st in e.subtypes)
    for e in ERROR_TYPE_CATALOG.error_types
}
ERROR_TYPES = frozenset(ERROR_SUBTYPES_BY_TYPE)
ACTIVE_ERROR_SUBTYPES = frozenset(
    st.error_subtype_id
    for e in ERROR_TYPE_CATALOG.error_types
    for st in e.subtypes
    if st.status == "active"
)
MISCONCEPTIONS_BY_ID: dict[str, Misconception] = {
    m.misconception_id: m for m in MISCONCEPTION_CATALOG.misconceptions
}


def skill_by_id(skill_id: str) -> Skill:
    """Skill을 taxonomy에서 조회한다. 없으면 SkillNotFoundError."""
    if not isinstance(skill_id, str):
        raise SkillNotFoundError(repr(skill_id))
    try:
        return SKILLS_BY_ID[skill_id]
    except KeyError:
        raise SkillNotFoundError(skill_id) from None


def validate_tracking_skill(skill_id: str) -> str:
    """Knowledge State/BKT 추적용 skill_id를 검증하고 그대로 반환한다.

    정책 — frozen taxonomy의 bkt_eligible/status가 source of truth:
      - 미등록 ID            -> SkillNotFoundError     (E_UNKNOWN_SKILL)
      - bkt_eligible=false  -> SkillNotTrackableError (E_SKILL_NOT_TRACKABLE)
        group node와 future 상태 skill은 추적 대상이 아니다.
      - leaf + bkt_eligible=true + active -> 그대로 통과

    analyzer는 검증 통과한 값만 Knowledge State에 넘기므로
    임의 문자열이 상태 키를 만들 수 없다.
    """
    skill = skill_by_id(skill_id)
    if not skill.bkt_eligible or skill.status != "active":
        raise SkillNotTrackableError(skill_id)
    return skill_id


def is_known_skill(skill_id: str) -> bool:
    return isinstance(skill_id, str) and skill_id in SKILLS_BY_ID


def is_tracking_skill(skill_id: str) -> bool:
    """Knowledge State 추적 가능 여부 (raises하지 않는 조회)."""
    if not is_known_skill(skill_id):
        return False
    skill = SKILLS_BY_ID[skill_id]
    return skill.bkt_eligible and skill.status == "active"


def error_subtypes_for(error_type: str) -> frozenset[str]:
    try:
        return ERROR_SUBTYPES_BY_TYPE[error_type]
    except KeyError:
        raise UnknownErrorTypeError(error_type) from None


def is_valid_error_type(error_type: str) -> bool:
    return error_type in ERROR_TYPES


def is_valid_error_subtype(error_type: str, error_subtype: str) -> bool:
    return error_subtype in ERROR_SUBTYPES_BY_TYPE.get(error_type, frozenset())


def is_active_error_subtype(error_subtype: str) -> bool:
    return error_subtype in ACTIVE_ERROR_SUBTYPES


def misconception_by_id(misconception_id: str) -> Misconception:
    try:
        return MISCONCEPTIONS_BY_ID[misconception_id]
    except KeyError:
        raise UnknownMisconceptionError(misconception_id) from None


def is_valid_misconception_id(misconception_id: str) -> bool:
    return misconception_id in MISCONCEPTIONS_BY_ID


def related_skill_ids(misconception_id: str) -> list[str]:
    """오개념과 연결된 canonical skill_id 목록 (taxonomy 기준).

    Problem의 개념적 분류에는 group node가 포함될 수 있으므로
    bkt_eligible 여부를 여기서 강제하지 않는다.
    Knowledge State 추적은 validate_tracking_skill()이 별도로 담당한다.
    """
    return list(misconception_by_id(misconception_id).related_skill_ids)
