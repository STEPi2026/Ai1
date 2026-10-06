"""S4 회귀 — problem_skill(Skill) 검증.

problem_skill은 Knowledge State/BKT에 직접 연결되는 tracking skill이다.
frozen taxonomy(data/skills.json)이 source of truth이며 정책은:
  - 미등록 ID                -> E_UNKNOWN_SKILL           (422)
  - bkt_eligible=false      -> E_SKILL_NOT_TRACKABLE     (422)
  - leaf + bkt_eligible=true -> 통과
문제의 개념적 분류(group node 포함)는 response의 skills/skill_ids가 담당한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import (
    AnalysisError,
    UnknownSkillError,
    UntrackableSkillError,
    analyze,
    get_engine,
    reset_knowledge,
)
from app.taxonomy import (
    LEGACY_SKILL_IDS,
    is_tracking_skill,
    skill_by_id,
    validate_tracking_skill,
)

client = TestClient(app)

SOLUTION = "2x=6\nx=3"


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def post_skill(skill: str, **extra):
    payload = {"problem_latex": "2x=6", "solution_text": SOLUTION, **extra}
    if skill is not None:
        payload["problem_skill"] = skill
    return client.post("/analyze-solution", json=payload)


# ---------------------------------------------------------------------------
# 정책 3: leaf + bkt_eligible=true 허용
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skill", sorted(LEGACY_SKILL_IDS))
def test_legacy_leaf_skills_are_valid(skill):
    """기존 4개 skill은 모두 유효해야 한다 (하위호환)."""
    r = post_skill(skill)
    assert r.status_code == 200, r.text
    assert r.json()["skill"] == skill


def test_valid_leaf_skill_returns_200():
    r = post_skill("linear_equation")
    assert r.status_code == 200
    assert r.json()["correct"] is True


@pytest.mark.parametrize(
    "skill",
    [
        "polynomial_addition", "polynomial_multiplication", "factoring_common",
        "factoring_formula", "quadratic_equation", "set_operation", "rational_function",
    ],
)
def test_other_leaf_skills_accepted(skill):
    assert post_skill(skill).status_code == 200


# ---------------------------------------------------------------------------
# 정책 1: 미등록 ID -> E_UNKNOWN_SKILL
# ---------------------------------------------------------------------------

def test_unknown_skill_rejected():
    r = post_skill("존재하지않는스킬ZZZ")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL"


def test_unknown_skill_error_code_from_analyze():
    with pytest.raises(AnalysisError) as ei:
        analyze("2x=6", SOLUTION, student_id="s4-1", problem_skill="존재하지않는스킬ZZZ")
    assert ei.value.code == "E_UNKNOWN_SKILL"


@pytest.mark.parametrize("bad", ["skill_not_in_taxonomy", "Distribution", "DISTRIBUTION", " "])
def test_case_and_whitespace_variants_rejected(bad):
    """대소문자/공백 변형도 taxonomy에 없으므로 미등록으로 처리한다."""
    assert post_skill(bad).status_code == 422


def test_non_string_skill_rejected():
    """숫자 등 비문자열은 미등록 skill로 처리 (상태 키 생성 차단)."""
    with pytest.raises(AnalysisError) as ei:
        analyze("2x=6", SOLUTION, student_id="s4-2", problem_skill=123)  # type: ignore[arg-type]
    assert ei.value.code == "E_UNKNOWN_SKILL"


# ---------------------------------------------------------------------------
# 정책 2: group node -> E_SKILL_NOT_TRACKABLE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skill", ["factoring", "polynomial_arithmetic"])
def test_group_node_rejected(skill):
    r = post_skill(skill)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


def test_group_node_error_message_mentions_bkt_eligible():
    r = post_skill("factoring")
    assert "bkt_eligible" in r.json()["detail"]["message"]


def test_future_skill_rejected_as_not_trackable():
    """future 상태 skill은 taxonomy에 있어도 추적 대상이 아니다."""
    r = post_skill("logarithm_function")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


# ---------------------------------------------------------------------------
# 정책 4: 빈 문자열 -> Pydantic validation 422
# ---------------------------------------------------------------------------

def test_empty_skill_rejected():
    r = post_skill("")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 정책 5·6: Knowledge State에 invalid skill이 들어가지 않는다
# ---------------------------------------------------------------------------

def test_invalid_skill_never_creates_knowledge_state_key():
    for bad in ("존재하지않는스킬ZZZ", "factoring", "polynomial_arithmetic", "logarithm_function"):
        with pytest.raises(AnalysisError):
            analyze("2x=6", SOLUTION, student_id="s4-inv", problem_skill=bad)
    rule = get_engine("rule")
    bkt = get_engine("bkt")
    assert rule._state == {}, "invalid skill이 rule 상태 키를 만들었다"
    assert rule._streak == {}
    assert bkt._p == {}, "invalid skill이 BKT 키를 만들었다"


def test_valid_skill_does_create_state_key():
    analyze("2x=6", SOLUTION, student_id="s4-ok", problem_skill="linear_equation")
    assert ("s4-ok", "linear_equation") in get_engine("rule")._state


def test_error_path_uses_validated_skill_only():
    """오류 경로는 classifier가 taxonomy에서 뽑은 skill을 쓰므로 항상 유효하다."""
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "2(x-3)=6",
            "solution_text": "2(x-3)=6\n2x-3=6",
            "student_id": "s4-errpath",
            "problem_skill": "linear_equation",
        },
    )
    assert r.status_code == 200
    assert r.json()["skill"] == "distribution"
    keys = {k[1] for k in get_engine("rule")._state}
    assert keys <= {"linear_equation", "distribution"}
    assert keys, "오류 경로에서 상태가 갱신되지 않았다"
    assert all(is_tracking_skill(s) for s in keys)


# ---------------------------------------------------------------------------
# taxonomy 조회 계층 단위 테스트
# ---------------------------------------------------------------------------

def test_validate_tracking_skill_returns_same_value():
    assert validate_tracking_skill("arithmetic") == "arithmetic"


def test_validate_tracking_skill_rejects_group_node():
    from app.taxonomy import SkillNotTrackableError

    with pytest.raises(SkillNotTrackableError):
        validate_tracking_skill("factoring")


def test_validate_tracking_skill_rejects_unknown():
    from app.taxonomy import SkillNotFoundError

    with pytest.raises(SkillNotFoundError):
        validate_tracking_skill("nope")


def test_is_tracking_skill_matches_taxonomy_flag():
    for skill in skill_by_id("factoring"), skill_by_id("quadratic_equation"):
        assert is_tracking_skill(skill.skill_id) == (
            skill.bkt_eligible and skill.status == "active"
        )


# ---------------------------------------------------------------------------
# 에러 클래스 계층: 기존 AnalysisError 하위 타입 (기존 처리 구조 유지)
# ---------------------------------------------------------------------------

def test_new_errors_are_analysis_error_subclasses():
    assert issubclass(UnknownSkillError, AnalysisError)
    assert issubclass(UntrackableSkillError, AnalysisError)
    assert UnknownSkillError("x").code == "E_UNKNOWN_SKILL"
    assert UntrackableSkillError("x").code == "E_SKILL_NOT_TRACKABLE"


# ---------------------------------------------------------------------------
# 기존 에러 코드와의 우선순위 유지 (S4 검증이 기존 422를 바꾸지 않음)
# ---------------------------------------------------------------------------

def test_existing_error_codes_still_take_precedence():
    assert post_skill("", solution_text="2x=6\nx=3").status_code == 422
    # 풀이가 비면 skill 검증 전에 E_EMPTY_SOLUTION이 난다 (기존 동작 유지)
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "", "problem_skill": "존재하지않는스킬ZZZ"},
    )
    assert r.json()["detail"]["code"] == "E_EMPTY_SOLUTION"
