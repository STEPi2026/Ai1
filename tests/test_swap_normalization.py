"""Phase 7-2-3 회귀 — 좌우반전 정규화(normalized_text).

'등식의 좌변과 우변을 단순히 swap 한 표현'을 math_equivalent 보다 강한
'표현적 증거'(normalized_text)로 인식한다.

원칙: 신호 강도 순서
  identical_text (원본 완전 일치)
    > normalized_text (좌우 반전)
      > math_equivalent (해집합 동치)
        > None (판정 불가)

정규화 범위는 '좌우 반전' 까지로 한정한다:
  - '=' 이 정확히 하나인 등식만 (다중 등식 a=b=c 는 제외)
  - 공백 제거만 (×, -, ^, {} 등 다른 문자 보정은 하지 않는다)
  - 항 재배열·교환법칙·전개/인수분해 정규화는 하지 않는다
"""
from __future__ import annotations

import pytest

from app.pipeline.session import (
    CorrectSolution,
    Problem,
    SolutionStep,
    StudentAttempt,
    _match_kind,
    _match_weight,
    _swap_canonical,
    align_attempt,
    analyze_attempt,
)
from app.pipeline.verifier import _SolveBudget

SOLUTION_TEXTS = ["2(x-3)=6", "2x-6=6", "2x=12", "x=6"]
SOLUTION = CorrectSolution(
    solution_id="SOL-N",
    problem_id="P-N",
    steps=[SolutionStep(step_no=i + 1, latex_text=t) for i, t in enumerate(SOLUTION_TEXTS)],
    answer_latex="x=6",
)
PROBLEM = Problem(
    problem_id="P-N", problem_latex="2(x-3)=6", skills=["linear_equation", "distribution"]
)


def kind(attempt_text: str, solution_text: str):
    return _match_kind(attempt_text, solution_text, _SolveBudget(), {})


def align(attempt_id: str, texts: list[str], solution=SOLUTION):
    a = StudentAttempt(
        attempt_id=attempt_id, student_id="S1", problem_id="P-N", solution_text="\n".join(texts)
    )
    return align_attempt(PROBLEM, a, solution)


def pmap(alignment) -> dict[int, int]:
    return {p.attempt_step_no: p.solution_step_no for p in alignment.pairs}


@pytest.fixture(autouse=True)
def _fresh():
    from app.pipeline.analyzer import reset_knowledge

    reset_knowledge()
    yield
    reset_knowledge()


# ===========================================================================
# 정규화 함수 단위 (A/B/C/J)
# ===========================================================================

@pytest.mark.parametrize(
    "left,right",
    [
        ("x=6", "6=x"),            # A. 기본 좌우반전
        ("2x-6=6", "6=2x-6"),      # B. 복합식 좌우반전
        ("x = 6", "6=x"),          # C. 공백 차이 + 좌우반전
        ("6 = x", "x=6"),
        ("2x=12", "12=2x"),
        ("2(x-3)=6", "6=2(x-3)"),
        ("x+1=7", "7=x+1"),
    ],
)
def test_swap_canonical_marks_swapped_equations(left, right):
    assert kind(left, right) == "normalized_text"
    assert kind(right, left) == "normalized_text", "대칭이 아니면 안 된다"


@pytest.mark.parametrize("text", ["a=b=c", "c=b=a", "=6", "x=", "2x-6=6=12"])
def test_multi_equals_and_incomplete_not_normalized(text):
    """'=' 이 정확히 하나가 아니면 정규화 대상이 아니다 (J)."""
    assert _swap_canonical(text) is None
    assert kind(text, text) == "identical_text", "원본이 같으면 여전히 identical_text"


def test_swap_canonical_is_symmetric():
    assert _swap_canonical("2x-6=6") == _swap_canonical("6=2x-6")
    assert _swap_canonical("x=6") == _swap_canonical("6=x")


def test_whitespace_removed_on_both_sides():
    assert _swap_canonical("  2 x - 6 = 6  ") == _swap_canonical("6=2x-6")


def test_blank_side_excluded():
    assert _swap_canonical(" = 6") is None
    assert _swap_canonical("x= ") is None


# ===========================================================================
# D/E. 신호 강도 순서
# ===========================================================================

def test_identical_text_has_highest_priority():
    """원본이 완전 일치하면 normalized_text 보다 우선한다 (E)."""
    assert kind("x=6", "x=6") == "identical_text"
    assert kind("x=6", "6=x") == "normalized_text"


def test_weights_ordered():
    assert _match_weight("identical_text") == 2
    assert _match_weight("normalized_text") == 2, "표현 증거는 동치보다 강해야 한다"
    assert _match_weight("math_equivalent") == 1
    assert _match_weight(None) == 0


def test_normalized_is_stronger_than_math_equivalent():
    """K. 일반 동치(6+6=2x ↔ 2x=12)는 normalized_text 가 아니다."""
    assert kind("6+6=2x", "2x=12") == "math_equivalent"
    assert _match_weight("normalized_text") > _match_weight("math_equivalent")


@pytest.mark.parametrize(
    "a,b",
    [
        ("x=3+3", "x=6"),      # 합으로 표현 (좌우반전 아님)
        ("0.5x=3", "2x=12"),  # 소수 표현
    ],
)
def test_general_equivalence_stays_math_equivalent(a, b):
    assert kind(a, b) == "math_equivalent"


@pytest.mark.parametrize("a,b", [("2x=12", "x=12"), ("x=6", "x=7")])
def test_non_equivalent_stays_none(a, b):
    """애초에 동치가 아닌 쌍은 None 이다 (normalized_text 로 승격되지 않는다)."""
    assert kind(a, b) is None


# ===========================================================================
# F/G/H. 다중 단계 정렬
# ===========================================================================

def test_swapped_step_in_multi_step_alignment():
    al = align("F1", ["2(x-3)=6", "2x-6=6", "2x=12", "6=x"])
    assert pmap(al) == {1: 1, 2: 2, 3: 3, 4: 4}
    assert al.pairs[3].match_kind == "normalized_text"


def test_swapped_step_with_inserted_step():
    al = align("G1", ["2(x-3)=6", "2x-6=6", "2x=6+6", "12=2x", "6=x"])
    assert pmap(al) == {1: 1, 2: 2, 4: 3, 5: 4}
    assert al.unaligned_attempt_steps == [3]
    assert al.ambiguous_attempt_steps == []


def test_swapped_step_with_skipped_step():
    al = align("H1", ["2(x-3)=6", "6=x"])
    assert pmap(al) == {1: 1, 2: 4}
    al2 = align("H2", ["2(x-3)=6", "2x-6=6", "6=x"])
    assert pmap(al2) == {1: 1, 2: 2, 3: 4}


# ===========================================================================
# I. 좌우반전 + ambiguity 공존
# ===========================================================================

def test_normalized_text_not_rejected_by_plain_ambiguity():
    """정렬 성질의 후보가 여러 개여도 normalized_text 는 강하므로 채택한다."""
    al = align("I1", ["2(x-3)=6", "2x-6=6", "6=x"])
    assert pmap(al)[3] == 4
    assert al.ambiguous_attempt_steps == []


def test_duplicate_canonical_solution_steps_still_ambiguous():
    """동일 정규 표현이 정답에 둘 이상이면 여전히 None (I)."""
    dup = CorrectSolution(
        solution_id="SOL-D",
        problem_id="P-N",
        steps=[SolutionStep(step_no=1, latex_text="x=6"), SolutionStep(step_no=2, latex_text="6=x")],
        answer_latex="x=6",
    )
    al = align("I2", ["6=x"], solution=dup)
    assert al.pairs == []
    assert al.ambiguous_attempt_steps == [1]


def test_non_swap_ambiguity_still_rejected():
    al = align("I3", ["2(x-3)=6", "2x-6=6", "x=3+3"])
    assert pmap(al) == {1: 1, 2: 2}
    assert al.ambiguous_attempt_steps == [3]


# ===========================================================================
# 8번 지정 실전 케이스
# ===========================================================================

def test_designated_real_case():
    """정답 2(x-3)=6/2x-6=6/2x=12/x=6, 학생 2(x-3)=6/2x-6=6/6+6=2x/6=x → 1:1 2:2 3:3 4:4"""
    al = align("Z1", ["2(x-3)=6", "2x-6=6", "6+6=2x", "6=x"])
    assert pmap(al) == {1: 1, 2: 2, 3: 3, 4: 4}
    kinds = {p.attempt_step_no: p.match_kind for p in al.pairs}
    assert kinds[3] == "math_equivalent", "'6+6=2x' 는 단순 동치로 남아야 한다"
    assert kinds[4] == "normalized_text", "'6=x' 는 좌우반전으로 확정되어야 한다"
    assert al.ambiguous_attempt_steps == [], "앞 단계가 확실한데 뒤 단계까지 제거되었다"


def test_designated_real_case_aligned_step_nos():
    a = StudentAttempt(
        attempt_id="Z2", student_id="S1", problem_id="P-N",
        solution_text="2(x-3)=6\n2x-6=6\n6+6=2x\n6=x",
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 2, 3, 4]


# ===========================================================================
# M/N/O/P. ErrorRecord / 다중 오류 / analyze_attempt 회귀
# ===========================================================================

def test_ref_solution_step_no_regression():
    a = StudentAttempt(
        attempt_id="M1", student_id="S1", problem_id="P-N", solution_text="2(x-3)=6\n2x-3=10"
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    e = r.errors[0]
    assert e.step_no == 2
    assert e.ref_step_no == 1
    assert e.ref_solution_step_no == 1


def test_multi_error_regression():
    """Phase 7-2-1 다중 오류 구조가 그대로다 (독립 오류 2건)."""
    a = StudentAttempt(
        attempt_id="O1", student_id="S1", problem_id="P-N",
        solution_text="2(x-3)=6\n2x-6=7\n2x=13\nx=6",
    )
    r = analyze_attempt(PROBLEM, a)
    assert [e.step_no for e in r.errors] == [2, 4]
    assert r.suppressed_downstream_steps == []
    ids = [o.skill_id for o in r.observations]
    assert len(ids) == len(set(ids)), "observation 중복"


def test_downstream_suppression_regression():
    a = StudentAttempt(
        attempt_id="O2", student_id="S1", problem_id="P-N",
        solution_text="2(x-3)=6\n2x-3=6\n2x=14",
    )
    r = analyze_attempt(PROBLEM, a)
    assert [e.step_no for e in r.errors] == [2]
    assert r.errors[0].error_type == "concept_error"
    assert r.suppressed_downstream_steps == [3]


def test_analyze_attempt_regression_correct():
    a = StudentAttempt(
        attempt_id="P1", student_id="S1", problem_id="P-N",
        solution_text="2(x-3)=6\n2x-6=6\n2x=12\nx=6",
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert r.correct is True
    assert r.errors == []


def test_analyze_attempt_regression_api_contract():
    from app.schemas import AnalyzeSolutionResponse

    a = StudentAttempt(
        attempt_id="P2", student_id="S1", problem_id="P-N", solution_text="2(x-3)=6\n2x-3=6"
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    parsed = AnalyzeSolutionResponse(**r.api_response)
    assert parsed.skill == "distribution"
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)


# ===========================================================================
# Q. 기존 analyze() 회귀 (이 기능은 analyze() 를 건드리지 않는다)
# ===========================================================================

def test_analyze_untouched():
    from app.pipeline.analyzer import analyze, reset_knowledge

    reset_knowledge()
    out = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="reg", problem_skill="linear_equation")
    assert out["correct"] is False
    assert out["error_step"] == 2
    assert out["error_type"] == "concept_error"
    assert out["skill"] == "distribution"
    assert out["misconception_id"] == "2.1"
    assert out["valid"] == [True, False]


def test_new_public_field_not_added():
    """모델/API 에 새 공개 필드를 추가하지 않았다 (지시 9)."""
    from app.schemas import AnalyzeSolutionResponse

    assert "normalized_text" not in AnalyzeSolutionResponse.model_fields
    al = align("Q1", SOLUTION_TEXTS)
    assert set(al.model_dump()) == {
        "attempt_id", "problem_id", "solution_id", "method", "aligned", "pairs",
        "unaligned_attempt_steps", "unaligned_solution_steps", "ambiguous_attempt_steps",
    }
