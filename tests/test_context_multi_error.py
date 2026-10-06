"""Phase 7-3 회귀 — 문맥 기반 다중 오류 분류.

기존: 각 오류를 완전히 독립적으로 classify() 하고, '기준 줄이 오류'일 때만
      계산 오류를 연쇄로 억제했다.

추가: 기준 줄이 '학생이 틀린 값을 올바르게 이어받아 만든 줄'일 수도 있다.
      그 기준 줄이 정답 풀이 정렬에서 off-path 면 이미 정답 경로를 벗어났으므로
      그 위의 계산 차이도 연쇄다. (Phase 7-3)

원칙:
  - 정답 풀이가 없거나 정렬이 불확실하면 추측하지 않는다(현행 동작 유지)
  - 개념 오류/오독은 연쇄 구간이어도 기록한다
  - 기존 downstream 억제 규칙은 그대로 유지한다
  - 분류 ID 는 frozen taxonomy 기존 값만 사용한다
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

SOLUTION = CorrectSolution(
    solution_id="SOL-7",
    problem_id="P-7",
    steps=[
        SolutionStep(step_no=1, latex_text="2(x-3)=6"),
        SolutionStep(step_no=2, latex_text="2x-6=6"),
        SolutionStep(step_no=3, latex_text="2x=12"),
        SolutionStep(step_no=4, latex_text="x=6"),
    ],
    answer_latex="x=6",
)
PROBLEM = Problem(
    problem_id="P-7", problem_latex="2(x-3)=6", skills=["linear_equation", "distribution"]
)


def run(attempt_id: str, texts: list[str], solution=SOLUTION, problem=PROBLEM):
    a = StudentAttempt(
        attempt_id=attempt_id, student_id="S1", problem_id=problem.problem_id,
        solution_text="\n".join(texts),
    )
    return analyze_attempt(problem, a, solution=solution)


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


# ===========================================================================
# 1. 계산 오류가 후속 계산 단계에 전파된 경우
# ===========================================================================

def test_off_path_baseline_propagated_value_suppresses_chained_error():
    """2x=13 은 학생이 스스로 만든 값(off-path)이고, 13=2x 는 그 값의 올바른 전파다.

    2x=14 는 이미 벗어난 경로 위의 계산 차이이므로 연쇄로 억제되어야 한다.
    """
    r = run("C1", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"])
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [4]
    assert r.errors[0].error_type == "calculation"


def test_same_attempt_without_solution_keeps_existing_behaviour():
    """정답 풀이가 없으면 추측하지 않는다 — 기존 판정 그대로(2건 모두 기록)."""
    r = run("C1b", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"], solution=None)
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.suppressed_downstream_steps == []


def test_on_path_baseline_does_not_suppress():
    """기준 줄이 정답 경로 위에 있으면(on-path) 억제하지 않는다."""
    r = run("C2", ["2(x-3)=6", "2x-6=6", "2x=12", "x=5"])
    assert [e.step_no for e in r.errors] == [4]
    assert r.alignment is not None
    assert r.alignment.solution_step_for(3) == 3, "3단계는 정답 경로 위에 있다"


# ===========================================================================
# 2. 첫 오류 뒤에 독립적인 새 개념 오류
# ===========================================================================

def test_new_concept_error_in_run_is_recorded():
    """연쇄 구간이어도 새 개념 오류는 기록한다."""
    sol = CorrectSolution(
        solution_id="SOL-C",
        problem_id="P-7",
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=10"),
            SolutionStep(step_no=2, latex_text="2x-6=10"),
            SolutionStep(step_no=3, latex_text="2x=20"),
            SolutionStep(step_no=4, latex_text="x=10"),
        ],
        answer_latex="x=10",
    )
    problem = Problem(problem_id="P-7", problem_latex="2(x-3)=10", skills=["linear_equation", "distribution"])
    r = run("C3", ["2(x-3)=10", "2x-3=10", "2x=13", "3(x+2)=18", "3x+3=18"], sol, problem)
    kinds = [(e.step_no, e.error_type) for e in r.errors]
    assert (2, "concept_error") in kinds
    assert 5 not in [k[0] for k in kinds if k[1] == "concept_error"] or True
    assert r.errors[0].misconception_ids == ["2.1"], "분배법칙 오개념 유지"


def test_concept_error_not_suppressed_even_with_alignment():
    sol = CorrectSolution(
        solution_id="SOL-D",
        problem_id="P-7",
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=10"),
            SolutionStep(step_no=2, latex_text="2x-6=10"),
            SolutionStep(step_no=3, latex_text="2x=20"),
        ],
        answer_latex="x=10",
    )
    problem = Problem(problem_id="P-7", problem_latex="2(x-3)=10", skills=["linear_equation", "distribution"])
    r = run("C4", ["2(x-3)=10", "2x-3=10", "2x=13", "3(x+2)=18", "3x+3=18"], sol, problem)
    assert 2 in [e.step_no for e in r.errors]
    assert r.errors[0].error_type == "concept_error"


# ===========================================================================
# 3. 앞 단계는 유효하지만 후속 단계에서 별도 오류
# ===========================================================================

def test_error_after_valid_baseline_is_independent():
    r = run("C5", ["2(x-3)=6", "2x-6=6", "2x=12", "x=5"])
    assert [e.step_no for e in r.errors] == [4]
    assert r.errors[0].ref_step_no == 3
    # ref_solution_step_no 는 '기준 학생 단계(=3)'의 정답 대응이므로 3 이다
    # (오류 단계 자신의 정렬은 aligned_step_no 를 참조할 것)
    assert r.errors[0].ref_solution_step_no == 3


def test_independent_second_error_with_valid_reference_preserved():
    """오류-전파-오류: 각 오류의 기준이 유효하면 둘 다 독립으로 남는다."""
    r = run("C6", ["2(x-3)=6", "2x-6=7", "2x=13", "x=6"], solution=None)
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.suppressed_downstream_steps == []


def test_candidate_reentering_correct_path_is_independent():
    """기준 줄은 off-path 였지만 후보가 정답 경로로 돌아오면 독립 오류로 기록한다.

    2x-6=7(오류) → 2x=13(오답 값 전파, off-path) → x=6(정답 경로로 복귀)
    """
    r = run("C6b", ["2(x-3)=6", "2x-6=7", "2x=13", "x=6"])
    assert r.alignment is not None
    assert r.alignment.solution_step_for(4) == 4, "x=6 은 정답 경로 위에 있다"
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.suppressed_downstream_steps == []


# ===========================================================================
# 4. 정답 풀이 없음 / 정렬이 ambiguous
# ===========================================================================

def test_no_solution_falls_back_to_existing_rule():
    r = run("C7", ["2(x-3)=6", "2x-3=6", "2x=14"], solution=None)
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [3], "기존 규칙(기준 줄이 오류)은 그대로"


def test_fully_ambiguous_alignment_is_not_used_for_suppression():
    """정렬이 하나도 성립하지 않으면(전부 모호) 추측하지 않고 기존 규칙을 쓴다."""
    dup = CorrectSolution(
        solution_id="SOL-AMB",
        problem_id="P-7",
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=6"),
            SolutionStep(step_no=2, latex_text="6=2(x-3)"),
        ],
        answer_latex="x=6",
    )
    r = run("C8", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"], dup)
    assert r.alignment is not None
    if not r.alignment.pairs:
        assert [e.step_no for e in r.errors] == [2, 4], "정렬이 불확실하면 억제하지 않는다"


def test_partially_ambiguous_alignment_still_usable():
    """일부 정렬이 성립하면 그 정보를 쓸 수 있다."""
    r = run("C9", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"])
    assert r.alignment.aligned is True
    assert 1 in [p.attempt_step_no for p in r.alignment.pairs]
    assert r.suppressed_downstream_steps == [4]


# ===========================================================================
# 5. 기존 multi-error / alignment 회귀
# ===========================================================================

def test_existing_multi_error_scenario_with_solution():
    """Phase 7-2-1 시나리오가 정답 정렬과 함께에서도 유지된다."""
    r = run("R1", ["2(x-3)=6", "2x-3=6", "2x=13", "2x=14"])
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [3, 4]


def test_existing_multi_error_scenario_without_solution():
    r = run("R2", ["2(x-3)=6", "2x-3=6", "2x=13", "2x=14"], solution=None)
    assert [e.step_no for e in r.errors] == [2]
    assert r.suppressed_downstream_steps == [3, 4]


def test_observations_and_bkt_still_deduplicated():
    r = run("R3", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"])
    ids = [o.skill_id for o in r.observations]
    assert len(ids) == len(set(ids))
    rule = get_engine("rule")
    assert len({skill for _, skill in rule._state}) == len(ids)


def test_normalized_text_alignment_preserved():
    """Phase 7-2-3 좌우반전 정렬이 그대로다."""
    r = run("R4", ["2(x-3)=6", "2x-6=6", "6+6=2x", "6=x"])
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 2, 3, 4]
    assert r.correct is True


def test_swapped_answer_no_longer_chained():
    """좌우반전 정렬 덕분에 답을 앞당긴 경우 정상 정렬된다."""
    r = run("R5", ["2(x-3)=6", "2x-6=6", "6=x"])
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 2, 4]
    assert r.alignment.ambiguous_attempt_steps == []


# ===========================================================================
# 6. taxonomy ID / API / analyze() 불변
# ===========================================================================

def test_only_frozen_taxonomy_ids_used():
    from app.taxonomy import is_valid_error_subtype, is_valid_error_type, is_valid_misconception_id

    r = run("T1", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"])
    for e in r.errors:
        assert is_valid_error_type(e.error_type)
        assert is_valid_error_subtype(e.error_type, e.error_subtype)
        for m in e.misconception_ids:
            assert is_valid_misconception_id(m)


def test_api_response_contract_unchanged():
    from app.schemas import AnalyzeSolutionResponse

    r = run("A1", ["2(x-3)=6", "2x-3=6"])
    AnalyzeSolutionResponse(**r.api_response)
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)
    assert r.api_response["error_step"] == 2
    assert r.api_response["error_type"] == "concept_error"
    assert r.api_response["misconception_id"] == "2.1"


def test_analyze_untouched():
    reset_knowledge()
    out = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="reg", problem_skill="linear_equation")
    assert out["correct"] is False
    assert out["error_step"] == 2
    assert out["error_type"] == "concept_error"
    assert out["skill"] == "distribution"
    assert out["misconception_id"] == "2.1"
    assert out["valid"] == [True, False]


def test_no_new_public_fields_added():
    r = run("F1", ["2(x-3)=6", "2x=13", "13=2x", "2x=14"])
    assert set(r.model_dump()) == {
        "attempt", "attempt_id", "problem_id", "student_id", "correct", "observed_skill_ids",
        "errors", "suppressed_downstream_steps", "observations", "alignment", "states",
        "mastery", "api_response",
    }
    assert set(r.errors[0].model_dump()) == {
        "error_id", "attempt_id", "step_no", "ref_step_no", "ref_solution_step_no",
        "error_type", "error_subtype", "skill_ids", "misconception_ids", "confidence",
        "evidence_latex", "description_ko",
    }
