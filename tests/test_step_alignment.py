"""Phase 7-1 회귀 — 정답 풀이 ↔ 학생 풀이 단계 정렬(alignment).

정렬 흐름:
    CorrectSolution.steps[] + StudentAttempt.steps[] → step alignment
    → StudentAttempt.steps[].aligned_step_no
    → ErrorRecord.ref_solution_step_no

불변식:
- `ref_step_no` 는 학생 단계 기준(verifier 출처)로 그대로 유지한다.
- 정답 단계 대응은 `ref_solution_step_no` 로 새 표현한다(기존 계약 불변).
- 안전하게 판단할 수 없는 단계는 정렬하지 않고 None(미정)으로 남긴다.
"""
from __future__ import annotations

import pytest

from app.pipeline.analyzer import analyze, reset_knowledge
from app.pipeline.session import (
    AlignedStep,
    CorrectSolution,
    ErrorRecord,
    Problem,
    StepAlignment,
    SolutionStep,
    StudentAttempt,
    align_attempt,
    analyze_attempt,
)

SOLUTION = CorrectSolution(
    solution_id="SOL1",
    problem_id="P-A",
    steps=[
        SolutionStep(step_no=1, latex_text="2(x-3)=6"),
        SolutionStep(step_no=2, latex_text="2x-6=6"),
        SolutionStep(step_no=3, latex_text="2x=12"),
        SolutionStep(step_no=4, latex_text="x=6"),
    ],
    answer_latex="x=6",
)

PROBLEM = Problem(problem_id="P-A", problem_latex="2(x-3)=6", skills=["linear_equation", "distribution"])


def make_attempt(attempt_id: str, solution_text: str) -> StudentAttempt:
    return StudentAttempt(
        attempt_id=attempt_id, student_id="S1", problem_id="P-A", solution_text=solution_text
    )


def pair_map(alignment: StepAlignment) -> dict[int, int]:
    """학생 단계 번호 → 정답 단계 번호."""
    return {p.attempt_step_no: p.solution_step_no for p in alignment.pairs}


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


# ===========================================================================
# 1. 동일한 단계 수의 정상 풀이 정렬 (1:1)
# ===========================================================================

def test_identical_step_counts_align_one_to_one():
    a = make_attempt("A1", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.aligned is True
    assert [(p.attempt_step_no, p.solution_step_no) for p in al.pairs] == [
        (1, 1), (2, 2), (3, 3), (4, 4)
    ]
    assert al.unaligned_attempt_steps == []
    assert al.unaligned_solution_steps == []
    assert all(p.match_kind == "identical_text" for p in al.pairs)


def test_alignment_is_applied_to_attempt_steps():
    a = make_attempt("A1b", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 2, 3, 4]


def test_original_attempt_is_not_mutated():
    a = make_attempt("A1c", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert all(s.aligned_step_no is None for s in a.steps), "원본 attempt 가 변경되었다"


# ===========================================================================
# 2. 학생 풀이에 중간 단계가 추가된 경우
# ===========================================================================

def test_extra_middle_step_is_unaligned_others_kept():
    a = make_attempt("A2", "2(x-3)=6\n2x-6=6\n2x=6+6\n2x=12\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.unaligned_attempt_steps == [3], "삽입된 단계가 미정으로 남아야 한다"
    assert al.unaligned_solution_steps == []
    assert [(p.attempt_step_no, p.solution_step_no) for p in al.pairs] == [
        (1, 1), (2, 2), (4, 3), (5, 4)
    ]


def test_extra_step_does_not_shift_later_alignment():
    a = make_attempt("A2b", "2(x-3)=6\n2x-6=6\n2x=6+6\n2x=12\nx=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert r.alignment is not None
    assert r.attempt.steps[2].aligned_step_no is None
    assert r.attempt.steps[3].aligned_step_no == 3


# ===========================================================================
# 3. 학생 풀이에서 단계가 생략된 경우
# ===========================================================================

def test_skipped_step_leaves_solution_step_unaligned():
    a = make_attempt("A3", "2(x-3)=6\n2x-6=6\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.unaligned_solution_steps == [3], "생략된 정답 단계가 미정으로 남아야 한다"
    assert [(p.attempt_step_no, p.solution_step_no) for p in al.pairs] == [
        (1, 1), (2, 2), (3, 4)
    ]


def test_skipped_final_step_still_aligns_earlier():
    a = make_attempt("A3b", "2(x-3)=6\n2x-6=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert pair_map(al) == {1: 1, 2: 2}
    assert al.unaligned_solution_steps == [3, 4]


# ===========================================================================
# 4. 표현이 달라도 동일한 변환인 경우
# ===========================================================================

def test_equivalent_different_representation_aligns():
    """12=2x 와 2x=12 는 같은 변환 — 정렬되어야 한다.

    Phase 7-2-3: '12=2x' 는 '2x=12' 의 좌우반전이라 math_equivalent 가 아니라
    normalized_text(강한 표현 신호)로 분류된다.
    """
    a = make_attempt("A4", "2(x-3)=6\n2x-6=6\n12=2x\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert (3, 3) in [(p.attempt_step_no, p.solution_step_no) for p in al.pairs]
    kind = next(p.match_kind for p in al.pairs if p.attempt_step_no == 3)
    assert kind == "normalized_text"


def test_equivalent_shortcut_step_aligns_to_any_equivalent_solution_step():
    """학생이 중간 단계를 건너뛰고 2x=12 대신 x=6 을 썼다면 정답의 동치 단계와 정렬된다."""
    a = make_attempt("A4b", "2(x-3)=6\n2x-6=6\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    pairs = [(p.attempt_step_no, p.solution_step_no) for p in al.pairs]
    assert (3, 4) in pairs, "x=6 은 정답의 x=6(4단계)와 동일 문자이므로 정렬되어야 한다"


# ===========================================================================
# 5. 정렬 불가능한 경우
# ===========================================================================

def test_unalignable_steps_stay_none():
    a = make_attempt("A5", "2(x-3)=6\n5x=25\n7=7\n9=9")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.unaligned_attempt_steps == [2, 3, 4]
    assert pair_map(al) == {1: 1}
    assert [s.aligned_step_no for s in apply(a, al).steps] == [1, None, None, None]


def test_fully_unalignable_attempt_reports_not_aligned():
    a = StudentAttempt(
        attempt_id="A5b",
        student_id="S1",
        problem_id="P-A",
        solution_text="11=11\n12=12",
        steps=[SolutionStep(step_no=1, latex_text="11=11"), SolutionStep(step_no=2, latex_text="12=12")],
    )
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.aligned is False
    assert al.pairs == []


def test_single_step_solution_picks_strongest_match():
    """정답 1단계뿐이면 문자 일치(강 신호)를 우선한다."""
    single = CorrectSolution(
        solution_id="SOL-S", problem_id="P-A",
        steps=[SolutionStep(step_no=1, latex_text="x=6")], answer_latex="x=6",
    )
    a = make_attempt("A5c", "2(x-3)=6\nx=6")
    al = align_attempt(PROBLEM, a, single)
    assert pair_map(al) == {2: 1}
    assert al.unaligned_attempt_steps == [1]


def test_math_equivalent_is_weaker_than_identical_text():
    """동치 단계가 삽입 단계를 흡수하지 않아야 한다 (가중치 검증)."""
    a = make_attempt("A5d", "2(x-3)=6\n2x-6=6\n2x=6+6\n2x=12\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.unaligned_attempt_steps == [3], "동치만으로 삽입 단계를 정렬했다"


# ===========================================================================
# 6. ref_step_no(학생) / ref_solution_step_no(정답) 분리
# ===========================================================================

def test_error_ref_step_no_still_student_side():
    """기존 의미 유지 — ref_step_no 는 학생 단계 기준, ref_solution_step_no 는 그 기준의 정답 단계."""
    a = make_attempt("A6", "2(x-3)=6\n2x-3=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    e = r.errors[0]
    assert e.step_no == 2
    assert e.ref_step_no == 1, "학생 1단계와 대조되었다"
    assert e.ref_solution_step_no == 1, "그 기준(학생 1단계)은 정답 1단계에 대응한다"


def test_error_step_itself_may_be_unaligned():
    """틀린 단계는 정답 단계와 동치일 수 없으므로 미정렬일 수 있다."""
    a = make_attempt("A6e", "2(x-3)=6\n2x-3=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert r.attempt.steps[1].aligned_step_no is None
    assert r.alignment is not None
    assert r.alignment.solution_step_for(2) is None


def test_correct_step_alignment_available_for_all_steps():
    a = make_attempt("A6f", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 2, 3, 4]


def test_ref_solution_step_none_when_reference_unaligned():
    """기준(대조) 학생 단계가 미정렬이면 ref_solution_step_no 는 None.

    1변수 일차방정식에서는 '유효한 단계'가 해집합이 문제와 같아 정답 단계와 항상
    동치로 매칭되므로, 이 상태는 정렬 결과 직접 주입으로 검증한다.
    (통합 수준의 None 케이스는 test_error_step_itself_may_be_unaligned,
     test_ref_step_none_when_problem_is_baseline, test_ref_solution_step_none_without_solution 이 담당)
    """
    a = make_attempt("A6b", "2(x-3)=6\n2x-3=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert al.solution_step_for(1) == 1
    assert al.solution_step_for(2) is None, "오류 단계는 정렬되지 않는다"
    from app.pipeline.session import apply_alignment

    assert apply_alignment(a, al).steps[1].aligned_step_no is None


def test_ref_solution_step_none_without_solution():
    a = make_attempt("A6c", "2(x-3)=6\n2x-3=6")
    r = analyze_attempt(PROBLEM, a)  # solution 미제공
    assert r.errors[0].ref_solution_step_no is None
    assert r.alignment is None


def test_ref_step_none_when_problem_is_baseline():
    a = StudentAttempt(
        attempt_id="A6d", student_id="S1", problem_id="P-A", solution_text="2(x-3)=7\nx=6"
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert r.errors[0].ref_step_no is None, "문제식이 기준이면 None"
    assert r.errors[0].ref_solution_step_no is None, "정렬된 학생 단계가 없다"


# ===========================================================================
# 7. 기존 ErrorRecord 동작 regression
# ===========================================================================

def test_error_record_defaults_are_backward_compatible():
    e = ErrorRecord(
        error_id="E1", attempt_id="A1", step_no=2, ref_step_no=1,
        error_type="concept_error", error_subtype="distribution_omit",
        confidence=0.9, evidence_latex="2x-3=6", description_ko="d",
    )
    assert e.ref_solution_step_no is None
    assert e.skill_ids == [] and e.misconception_ids == []


def test_error_fields_unchanged_after_alignment():
    a = make_attempt("A7", "2(x-3)=6\n2x-3=6")
    with_sol = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    a2 = make_attempt("A7b", "2(x-3)=6\n2x-3=6")
    without_sol = analyze_attempt(PROBLEM, a2)
    for key in ("error_type", "error_subtype", "misconception_id", "confidence", "skill", "error_step"):
        assert with_sol.api_response[key] == without_sol.api_response[key], key


# ===========================================================================
# 8. 기존 analyze() regression
# ===========================================================================

def test_analyze_still_unchanged_with_solution_flow_present():
    reset_knowledge()
    out = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="reg", problem_skill="linear_equation")
    assert out["correct"] is False
    assert out["error_step"] == 2
    assert out["error_type"] == "concept_error"
    assert out["skill"] == "distribution"
    assert out["misconception_id"] == "2.1"
    assert out["valid"] == [True, False]
    assert set(out) == {
        "correct", "error_step", "error_type", "skill", "state", "mastery",
        "misconception_id", "confidence", "steps_latex", "valid", "unrecognized",
        "skills", "skill_ids", "error_subtype", "misconception_ids",
        "analysis_status",
    }


def test_api_response_contract_still_validates():
    from app.schemas import AnalyzeSolutionResponse

    a = make_attempt("A8", "2(x-3)=6\n2x-3=6")
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    parsed = AnalyzeSolutionResponse(**r.api_response)
    assert parsed.skill == "distribution"
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)


# ===========================================================================
# 모델 / 알고리즘 세부
# ===========================================================================

def test_alignment_model_fields():
    a = make_attempt("A9", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert isinstance(al, StepAlignment)
    assert al.attempt_id == "A9"
    assert al.problem_id == "P-A"
    assert al.method == "lcs-equivalence"
    assert all(isinstance(p, AlignedStep) for p in al.pairs)


def test_alignment_pair_numbers_are_one_based():
    a = make_attempt("A10", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    assert min(p.attempt_step_no for p in al.pairs) == 1
    assert min(p.solution_step_no for p in al.pairs) == 1


def test_alignment_preserves_order():
    """정렬은 순서를 뒤집지 않는다 (LCS 성질)."""
    a = make_attempt("A11", "2(x-3)=6\n2x=12\n2x-6=6\nx=6")
    al = align_attempt(PROBLEM, a, SOLUTION)
    att = [p.attempt_step_no for p in al.pairs]
    sol = [p.solution_step_no for p in al.pairs]
    assert att == sorted(att)
    assert sol == sorted(sol)


def test_alignment_is_idempotent():
    a = make_attempt("A12", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    al1 = align_attempt(PROBLEM, a, SOLUTION)
    al2 = align_attempt(PROBLEM, a, SOLUTION)
    assert al1 == al2


def test_mismatched_solution_problem_rejected():
    other = CorrectSolution(
        solution_id="SOL-X", problem_id="OTHER",
        steps=[SolutionStep(step_no=1, latex_text="x=1")], answer_latex="x=1",
    )
    a = make_attempt("A13", "2(x-3)=6\nx=6")
    with pytest.raises(ValueError):
        align_attempt(PROBLEM, a, other)


def test_solution_must_be_provided():
    a = make_attempt("A14", "2(x-3)=6\nx=6")
    with pytest.raises(ValueError):
        align_attempt(PROBLEM, a, None)


def apply(attempt: StudentAttempt, alignment: StepAlignment) -> StudentAttempt:
    from app.pipeline.session import apply_alignment

    return apply_alignment(attempt, alignment)
