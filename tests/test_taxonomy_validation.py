"""공통수학1 taxonomy 정적 데이터 검증 테스트.

data/skills.json · data/error_types.json · data/misconceptions.json의
무결성을 보장한다. skill_id는 BKT Knowledge State key이므로 중복·삭제·변경을
엄격히 차단한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas import AnalyzeSolutionRequest, AnalyzeSolutionResponse
from app.taxonomy import (
    API_ERROR_TYPES,
    BASE_UNITS,
    DATA_DIR,
    LEGACY_MISCONCEPTIONS,
    LEGACY_SKILL_IDS,
    OUT_OF_SCOPE_KEYWORDS,
    AnalysisExtension,
    all_error_subtype_ids,
    all_error_type_ids,
    all_skill_ids,
    check_common_math1_scope,
    check_legacy_preserved,
    check_skill_integrity,
    find_duplicate_ids,
    load_error_type_catalog,
    load_misconception_catalog,
    load_skill_catalog,
    validate_all,
)


@pytest.fixture(scope="module")
def skills():
    return load_skill_catalog()


@pytest.fixture(scope="module")
def error_types():
    return load_error_type_catalog()


@pytest.fixture(scope="module")
def misconceptions():
    return load_misconception_catalog()


# ---------------------------------------------------------------------------
# 스키마 로드 자체 검증 (Pydantic)
# ---------------------------------------------------------------------------

def test_data_files_load_and_validate(skills, error_types, misconceptions):
    assert len(skills.skills) > 0
    assert len(error_types.error_types) > 0
    assert len(misconceptions.misconceptions) > 0


def test_validate_all_reports_no_issues():
    assert validate_all() == []


# ---------------------------------------------------------------------------
# 고유성
# ---------------------------------------------------------------------------

def test_all_skill_ids_unique(skills):
    dupes = find_duplicate_ids(all_skill_ids(skills))
    assert dupes == [], f"skill_id 중복: {dupes}"


def test_all_misconception_ids_unique(misconceptions):
    ids = [m.misconception_id for m in misconceptions.misconceptions]
    dupes = find_duplicate_ids(ids)
    assert dupes == [], f"misconception_id 중복: {dupes}"


def test_all_error_type_ids_unique(error_types):
    dupes = find_duplicate_ids(all_error_type_ids(error_types))
    assert dupes == [], f"error_type_id 중복: {dupes}"


def test_all_error_subtype_ids_unique(error_types):
    dupes = find_duplicate_ids(all_error_subtype_ids(error_types))
    assert dupes == [], f"error_subtype_id 중복: {dupes}"


# ---------------------------------------------------------------------------
# 참조 무결성
# ---------------------------------------------------------------------------

def test_prerequisite_skills_exist(skills):
    assert check_skill_integrity(skills) == []


def test_parent_skill_ids_exist(skills):
    id_set = set(all_skill_ids(skills))
    for s in skills.skills + skills.expansion_skills:
        if s.parent_skill_id is not None:
            assert s.parent_skill_id in id_set, f"{s.skill_id}: parent 없음"


def test_misconception_related_skills_exist(skills, misconceptions):
    id_set = set(all_skill_ids(skills))
    for m in misconceptions.misconceptions:
        for sk in m.related_skill_ids:
            assert sk in id_set, f"{m.misconception_id}: skill '{sk}' 없음"


def test_misconception_typical_errors_exist(error_types, misconceptions):
    type_set = set(all_error_type_ids(error_types))
    subtype_set = set(all_error_subtype_ids(error_types))
    for m in misconceptions.misconceptions:
        for et in m.typical_error_types:
            assert et in type_set, f"{m.misconception_id}: error_type '{et}' 없음"
        for st in m.typical_error_subtypes:
            assert st in subtype_set, f"{m.misconception_id}: subtype '{st}' 없음"


def test_error_type_ids_match_api_contract(error_types):
    # 기존 API의 error_type 허용값과 정확히 일치해야 함 (하위호환)
    assert set(all_error_type_ids(error_types)) == set(API_ERROR_TYPES)


# ---------------------------------------------------------------------------
# 기존 ID 유지 (하위호환)
# ---------------------------------------------------------------------------

def test_legacy_skill_ids_preserved(skills):
    assert check_legacy_preserved(skills, load_misconception_catalog()) == []
    id_set = set(all_skill_ids(skills))
    for legacy in LEGACY_SKILL_IDS:
        assert legacy in id_set, f"기존 skill_id 삭제됨: {legacy}"


def test_legacy_misconception_ids_preserved(misconceptions):
    mis_map = {m.misconception_id: m for m in misconceptions.misconceptions}
    for mid, expected_name in LEGACY_MISCONCEPTIONS.items():
        assert mid in mis_map, f"기존 misconception_id 삭제됨: {mid}"
        assert expected_name in mis_map[mid].name, (
            f"{mid} 의미 변경됨: '{mis_map[mid].name}' (기대 '{expected_name}')"
        )


def test_legacy_skill_ids_still_used_by_existing_code():
    # knowledge.py / classifier.py가 참조하는 상수와 데이터가 일치하는지 원문 대조
    knowledge_src = (Path(__file__).parent.parent / "app" / "pipeline" / "knowledge.py").read_text(
        encoding="utf-8"
    )
    for legacy in LEGACY_SKILL_IDS:
        assert f'"{legacy}"' in knowledge_src, f"knowledge.py에 '{legacy}' 없음"


# ---------------------------------------------------------------------------
# 필드 타입
# ---------------------------------------------------------------------------

def test_bkt_eligible_is_boolean(skills):
    raw = json.loads((DATA_DIR / "skills.json").read_text(encoding="utf-8"))
    for s in raw["skills"] + raw.get("expansion_skills", []):
        assert isinstance(s["bkt_eligible"], bool), f"{s['skill_id']}: bkt_eligible이 boolean이 아님"


def test_skill_required_fields_present(skills):
    for s in skills.skills:
        assert s.skill_id and s.name and s.description and s.unit
        assert isinstance(s.prerequisite_skill_ids, list)
        assert isinstance(s.bkt_eligible, bool)


# ---------------------------------------------------------------------------
# 공통수학1 범위 (기본 목록)
# ---------------------------------------------------------------------------

def test_base_skills_within_common_math1_units(skills):
    for s in skills.skills:
        assert s.unit in BASE_UNITS, f"{s.skill_id}: unit '{s.unit}' 범위 밖"
        assert s.status == "active", f"{s.skill_id}: 기본 목록에 non-active 포함"


def test_base_lists_exclude_out_of_scope_keywords(skills, misconceptions):
    assert check_common_math1_scope(skills, misconceptions) == []


def test_expansion_skills_not_in_base(skills):
    base_ids = {s.skill_id for s in skills.skills}
    for s in skills.expansion_skills:
        assert s.skill_id not in base_ids, f"확장 skill '{s.skill_id}' 이(가) 기본 목록에 중복"
        assert s.status == "future"


def test_out_of_scope_subtypes_marked_future(error_types):
    # 범위 밖 키워드를 포함한 subtype은 future로만 존재해야 함
    for e in error_types.error_types:
        for st in e.subtypes:
            text = f"{st.name} {st.description}"
            if any(kw in text for kw in OUT_OF_SCOPE_KEYWORDS):
                assert st.status == "future", f"{st.error_subtype_id}: 범위 밖인데 active"


# ---------------------------------------------------------------------------
# 기존 API 호환성 (additive 설계)
# ---------------------------------------------------------------------------

# S3에서 AnalysisExtension의 placeholder가 실제 응답 필드로 채택되었다.
# 따라서 '충돌 없음' 대신 아래 불변식을 고정한다:
#   1) 기존 11개 응답 필드는 그대로 존재한다
#   2) 신규 4개 필드는 전부 optional(required 아님)이라 이전 응답이 그대로 성립
#   3) 신규 필드명이 기존 필드명을 덮어쓰지 않는다
_PRE_EXISTING_RESPONSE_FIELDS = {
    "correct", "error_step", "error_type", "skill", "state", "mastery",
    "misconception_id", "confidence", "steps_latex", "valid", "unrecognized",
}


def test_analysis_extension_fields_adopted_as_optional_response_fields():
    """placeholder → 실제 구현. 충돌이 아니라 '선택적 추가'가 되어야 한다."""
    adopted = set(AnalysisExtension.model_fields)
    response_fields = AnalyzeSolutionResponse.model_fields

    for name in adopted:
        assert name in response_fields, f"{name} 이(가) 응답 필드로 채택되지 않음"
        # is_required()가 False면 기본값이 존재한다(default=None 또는 default_factory)
        assert not response_fields[name].is_required(), (
            f"{name} 은(는) required면 기존 응답이 깨진다"
        )


# Phase 8-2: 앱 r49 대응으로 `analysis_status` 가 추가되었다 (선택 필드).
_ADDITIVE_STATUS_FIELD = {"analysis_status"}


def test_existing_response_fields_unchanged():
    response_fields = set(AnalyzeSolutionResponse.model_fields)
    missing = _PRE_EXISTING_RESPONSE_FIELDS - response_fields
    assert not missing, f"기존 출력 필드 누락: {missing}"
    # 신규 필드가 기존 필드명을 덮어쓰지 않았는지 (집합이 아니라 개수 대조)
    assert len(response_fields) == (
        len(_PRE_EXISTING_RESPONSE_FIELDS)
        + len(set(AnalysisExtension.model_fields))
        + len(_ADDITIVE_STATUS_FIELD)
    ), "응답 필드 구성이 '기존 11개 + additive 4개 + analysis_status' 이외의 형태다"


def test_request_contract_preserved():
    """요청 필드 계약과 기본값이 유지된다 (S4는 검증만 추가했다)."""
    fields = set(AnalyzeSolutionRequest.model_fields)
    assert fields >= {
        "problem_id", "problem_latex", "solution_text", "student_id", "engine", "problem_skill",
    }
    req = AnalyzeSolutionRequest(problem_latex="2x=6", solution_text="2x=6\nx=3")
    assert req.problem_skill == "linear_equation"
    assert req.engine == "rule"
    assert req.student_id == "anonymous"


def test_response_still_validates_legacy_payload_without_additive_fields():
    """additive 필드가 없는 기존 응답 payload도 그대로 통과해야 한다 (하위호환)."""
    legacy = {
        "correct": False,
        "error_step": 2,
        "error_type": "concept_error",
        "skill": "distribution",
        "state": "needs_practice",
        "mastery": 0.3,
        "misconception_id": "2.1",
        "confidence": 0.9,
        "steps_latex": ["2(x-3)=6", "2x-3=6"],
        "valid": [True, False],
        "unrecognized": [],
    }
    parsed = AnalyzeSolutionResponse(**legacy)
    assert parsed.correct is False
    assert parsed.skills == []  # additive 필드는 기본값으로 채워진다
    assert parsed.skill_ids == []
    assert parsed.error_subtype is None
    assert parsed.misconception_ids == []
