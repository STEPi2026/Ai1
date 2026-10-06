"""Phase 7-2-1 회귀 — 다중 오류(ErrorRecord[]) 구조.

기존 "시도당 최대 1개 오류"에서 "여러 독립 오류를 안전하게 기록"으로 확장한다.

핵심 원칙:
- 후보는 verifier 의 valid=False 단계 전부지만, '모든 False = 독립 오류'가 아니다.
- 연쇄 오류(기준 줄 자체가 오류인 단계의 계산 차이)는 기록하지 않는다.
- 첫 오류는 항상 기록된다 → 기존 단일 오류 동작이 보존된다.
- KnowledgeObservation 은 attempt 당 같은 tracking skill 1회만 갱신한다.
"""
from __future__ import annotations

import pytest

from app.pipeline.analyzer import analyze, get_engine, reset_knowledge
from app.pipeline.session import (
    CorrectSolution,
    Problem,
    SolutionStep,
    StudentAttempt,
    analyze_attempt,
)

PROBLEM = Problem(
    problem_id="P-M",
    problem_latex="2(x-3)=10",
    skills=["linear_equation", "distribution", "arithmetic"],
)


def run(attempt_id: str, solution_text: str, solution=None, engine="rule"):
    a = StudentAttempt(
        attempt_id=attempt_id, student_id="S1", problem_id="P-M", solution_text=solution_text
    )
    return analyze_attempt(PROBLEM, a, solution=solution, engine=engine)


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


# ===========================================================================
# A. 단일 오류 — 기존과 동일하게 1개
# ===========================================================================

def test_single_error_produces_one_record():
    r = run("A1", "2(x-3)=10\n2x-3=10")
    assert len(r.errors) == 1
    e = r.errors[0]
    assert e.step_no == 2
    assert e.error_type == "concept_error"
    assert e.error_subtype == "distribution_omit"
    assert e.misconception_ids == ["2.1"]
    assert r.suppressed_downstream_steps == []


def test_single_error_fields_identical_to_previous_behavior():
    r = run("A1b", "2(x-3)=10\n2x-3=10")
    e = r.errors[0]
    assert e.ref_step_no == 1
    assert e.ref_solution_step_no is None
    assert e.evidence_latex == "2x-3=10"
    assert e.confidence == 0.9
    assert e.description_ko
    assert e.attempt_id == "A1b"
    assert e.error_id == "A1b-E2"


# ===========================================================================
# B. 독립적인 다중 오류
# ===========================================================================

def test_two_independent_errors_recorded():
    r = run("A2", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.errors[0].error_type == "concept_error"
    assert r.errors[1].error_type == "calculation"
    assert r.suppressed_downstream_steps == []


def test_all_candidates_are_examined_not_only_first():
    """첫 오류만 보지 않는다 — 독립 오류가 2개면 2개를 만든다.

    2(x-3)=10 → 2x-6=11(오류) → 2x=17(오답 값 전파, 유효) → x=8(오류)
    """
    r = run("A2b", "2(x-3)=10\n2x-6=11\n2x=17\nx=8")
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.errors[1].ref_step_no == 3, "두 번째 오류의 기준은 유효한 3단계"


def test_three_independent_errors_when_each_has_valid_reference():
    """오류-전파-오류-전파-오류: 각 오류의 기준이 유효한 단계여야 독립으로 기록된다."""
    r = run("A2c", "2(x-3)=10\n2x-6=11\n2x=17\nx=8\nx+1=9\nx=7")
    assert [e.step_no for e in r.errors] == [2, 4, 6]
    assert all(e.error_type == "calculation" for e in r.errors)
    assert r.suppressed_downstream_steps == []
    assert r.api_response["error_step"] == 2, "기존 계약은 첫 오류를 대표로 한다"


# ===========================================================================
# C. downstream 오류 억제
# ===========================================================================

def test_downstream_calculation_after_error_is_suppressed():
    """오류 줄의 바로 다음 단계가 계산 차이면 연쇄로 보고 기록하지 않는다."""
    r = run("A3", "2(x-3)=10\n2x-3=10\n2x=14")
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [3]


def test_verifier_marks_value_propagation_as_valid():
    """정답 값이 그대로 전파되는 연쇄는 verifier 가 이미 valid 로 본다(후보 아님).

    2x-3=10 → 2x=13 은 틀린 줄의 값을 그대로 옮긴 것이므로 동치로 판정된다.
    """
    r = run("A3b", "2(x-3)=10\n2x-3=10\n2x=13")
    assert [e.step_no for e in r.errors] == [2]
    assert r.api_response["valid"] == [True, False, True]
    assert r.suppressed_downstream_steps == []


def test_concept_error_inside_error_run_is_still_recorded():
    """연쇄 구간이라도 '새 개념 오류'면 독립 오류다."""
    text = "2(x-3)=10\n2x-3=10\n3(x-1)=13\n3x-1=13\n3x=14"
    r = run("A3c", text)
    assert r.errors, "연쇄 구간의 새 개념 오류를 놓쳤다"
    assert r.errors[0].error_type == "concept_error"


def test_long_error_run_suppresses_arithmetic_drift():
    r = run("A3d", "2(x-3)=10\n2x-3=10\n2x=14\n2x=15\n2x=16")
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [3, 4, 5]


def test_first_error_is_never_suppressed():
    """첫 오류의 기준은 유효한 단계/문제이므로 절대 억제되지 않는다."""
    for text in (
        "2(x-3)=10\n2x-3=10",
        "2(x-3)=10\n2x-3=10\n2x=14",
        "2(x-3)=10\n2x-3=10\n2x=14\n2x=15",
    ):
        r = run(f"A3e-{len(text)}", text)
        assert r.errors and r.errors[0].step_no == 2
        assert 2 not in r.suppressed_downstream_steps


# ===========================================================================
# D. step_no 정확성
# ===========================================================================

def test_step_no_matches_actual_failing_step():
    r = run("A4", "2(x-3)=10\n2x-6=10\n2x=12\nx=5")
    texts = ["2(x-3)=10", "2x-6=10", "2x=12", "x=5"]
    for e in r.errors:
        assert texts[e.step_no - 1] == e.evidence_latex
        assert e.step_no == r.errors[0].step_no or e.step_no > r.errors[0].step_no


def test_step_numbers_are_one_based_and_ascending():
    r = run("A4b", "5x=10\n5x=11\n5x=12")
    steps = [e.step_no for e in r.errors]
    assert min(steps) >= 1
    assert steps == sorted(steps)


# ===========================================================================
# E/F. ref_step_no / ref_solution_step_no 의미 유지
# ===========================================================================

def test_ref_step_no_is_verifier_reference():
    r = run("A5", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    assert r.errors[0].ref_step_no == 1
    assert r.errors[1].ref_step_no == 3, "각 오류는 자신의 기준 단계를 가진다"


def test_ref_step_none_when_problem_is_baseline():
    r = run("A5b", "2(x-3)=11\nx=6")
    assert r.errors[0].step_no == 1
    assert r.errors[0].ref_step_no is None


def test_ref_solution_step_no_uses_alignment():
    sol = CorrectSolution(
        solution_id="SOL-M",
        problem_id="P-M",
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=10"),
            SolutionStep(step_no=2, latex_text="2x-6=10"),
            SolutionStep(step_no=3, latex_text="2x=16"),
            SolutionStep(step_no=4, latex_text="x=8"),
        ],
        answer_latex="x=8",
    )
    r = run("A5c", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14", solution=sol)
    assert r.alignment is not None
    assert r.errors, "오류가 하나도 없다"
    assert r.errors[0].ref_solution_step_no == 1, "기준 학생 1단계 → 정답 1단계"
    # 각 오류의 ref_solution_step_no 는 정답 단계 번호이거나 미정(None)이다
    for e in r.errors:
        assert e.ref_solution_step_no is None or isinstance(e.ref_solution_step_no, int)


def test_ref_solution_step_none_without_solution():
    r = run("A5d", "2(x-3)=10\n2x-3=10")
    assert all(e.ref_solution_step_no is None for e in r.errors)


def test_ref_solution_step_none_when_alignment_missing_for_reference():
    sol = CorrectSolution(
        solution_id="SOL-N",
        problem_id="P-M",
        steps=[SolutionStep(step_no=1, latex_text="2(x-3)=10")],
        answer_latex="x=8",
    )
    r = run("A5e", "2(x-3)=10\n2x-3=10", solution=sol)
    assert r.errors[0].ref_solution_step_no == 1

    r2 = run("A5f", "5x=25\n5x=30", solution=sol)
    assert r2.errors[0].ref_solution_step_no is None


# ===========================================================================
# G. 다중 오류 + 다중 Skill
# ===========================================================================

def test_multiple_errors_can_have_different_skills():
    r = run("A6", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    skills = {s for e in r.errors for s in e.skill_ids}
    assert "distribution" in skills
    assert "arithmetic" in skills, "두 번째 오류가 다른 skill 로 기록되었다"


def test_two_concept_errors_of_different_skills():
    text = "2(x-3)=10\n2x-3=10\n2x=13\nx=7"
    r = run("A6b", text)
    assert len(r.errors) >= 1
    assert all(e.error_type in ("concept_error", "calculation", "comprehension") for e in r.errors)


# ===========================================================================
# H. KnowledgeObservation 중복 방지
# ===========================================================================

def test_observation_dedup_per_skill():
    r = run("A7", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    ids = [o.skill_id for o in r.observations]
    assert len(ids) == len(set(ids)), f"같은 skill 이 중복 관측되었다: {ids}"


def test_bkt_updated_once_per_skill_even_with_many_errors():
    r = run("A7b", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14", engine="bkt")
    from app.pipeline.knowledge import BKTEngine

    base = BKTEngine()
    for skill in ("distribution", "polynomial_multiplication", "arithmetic"):
        expected = base.update(skill, skill, "incorrect")
        actual = r.mastery[skill]
        assert actual == pytest.approx(expected, abs=1e-12), f"{skill} 가 2회 이상 갱신되었다"


def test_observation_error_type_is_per_skill_source():
    r = run("A7c", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    by_skill = {o.skill_id: (o.error_type, o.error_id) for o in r.observations}
    assert by_skill["distribution"] == ("concept_error", "A7c-E2")
    assert by_skill["arithmetic"] == ("calculation", "A7c-E4")


def test_observation_error_id_points_to_matching_error():
    r = run("A7d", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    ids = {e.error_id for e in r.errors}
    for o in r.observations:
        assert o.error_id in ids


# ===========================================================================
# I. 관측되지 않은 skill 은 갱신하지 않는다
# ===========================================================================

def test_skill_without_error_not_updated():
    p = Problem(problem_id="P-I", problem_latex="2(x-3)=10", skills=["linear_equation", "transposition"])
    a = StudentAttempt(
        attempt_id="A8", student_id="S1", problem_id="P-I", solution_text="2(x-3)=10\n2x-3=10"
    )
    r = analyze_attempt(p, a)
    rule = get_engine("rule")
    assert all(skill != "transposition" for _, skill in rule._state)
    assert "transposition" not in r.states
    assert "transposition" not in {o.skill_id for o in r.observations}


def test_multiple_error_skills_all_observed():
    run("A8b", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    rule = get_engine("rule")
    touched = {skill for _, skill in rule._state}
    assert {"distribution", "arithmetic"} <= touched


# ===========================================================================
# J/K. 기존 동작 regression
# ===========================================================================

def test_api_response_fields_and_meaning_unchanged():
    reset_knowledge()
    r = run("A9", "2(x-3)=10\n2x-3=10")
    api = r.api_response
    assert set(api) == {
        "correct", "error_step", "error_type", "skill", "state", "mastery",
        "misconception_id", "confidence", "steps_latex", "valid", "unrecognized",
        "skills", "skill_ids", "error_subtype", "misconception_ids",
        "analysis_status",
    }
    assert api["correct"] is False
    assert api["error_step"] == 2
    assert api["error_type"] == "concept_error"
    assert api["skill"] == "distribution"
    assert api["misconception_id"] == "2.1"
    assert api["error_subtype"] == "distribution_omit"
    assert api["misconception_ids"] == ["2.1"]


def test_api_response_reflects_first_error_only():
    """기존 계약은 '첫 오류'를 대표로 한다 — 다중 오류여도 동일."""
    r = run("A9b", "2(x-3)=10\n2x-3=10\n2x=13\n2x=14")
    assert r.api_response["error_step"] == r.errors[0].step_no == 2
    assert r.api_response["error_type"] == r.errors[0].error_type
    assert r.api_response["skill"] == r.errors[0].skill_ids[0]
    assert len(r.errors) == 2


def test_analyze_unchanged():
    reset_knowledge()
    out = analyze("2(x-3)=10", "2(x-3)=10\n2x-3=10", student_id="reg", problem_skill="linear_equation")
    assert out["correct"] is False
    assert out["error_step"] == 2
    assert out["error_type"] == "concept_error"
    assert out["skill"] == "distribution"
    assert out["misconception_id"] == "2.1"
    assert out["valid"] == [True, False]


def test_correct_attempt_still_works():
    r = run("A9c", "2(x-3)=10\n2x-6=10\n2x=16\nx=8")
    assert r.correct is True
    assert r.errors == []
    assert r.suppressed_downstream_steps == []
    assert all(o.outcome == "correct" for o in r.observations)


def test_attempt_analysis_exposes_both_lists():
    r = run("A9d", "2(x-3)=10\n2x-3=10\n2x=14")
    assert r.errors and r.suppressed_downstream_steps
    dumped = r.model_dump()
    assert "suppressed_downstream_steps" in dumped
    assert dumped["suppressed_downstream_steps"] == [3]
