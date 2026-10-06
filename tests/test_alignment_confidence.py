"""Phase 7-2-2 회귀 — 정렬 신뢰도 강화(acceptance 게이트).

원칙: '정렬하지 않는 것이 잘못 정렬하는 것보다 낫다.'
ref_solution_step_no 가 잘못 연결되면 ErrorRecord 가 잘못된 정답 단계에 묶이므로,
불확실한 대응은 None(미정)으로 남긴다.

구조: _match_kind()(match 판정) → 가중 LCS(후보) → _accept_pairs()(채택)
      → accepted pair 만 aligned_step_no 에 반영
"""
from __future__ import annotations

import pytest

from app.pipeline.session import (
    CorrectSolution,
    Problem,
    SolutionStep,
    StudentAttempt,
    align_attempt,
    analyze_attempt,
)

SOLUTION_TEXTS = ["2(x-3)=6", "2x-6=6", "2x=12", "x=6"]
SOLUTION = CorrectSolution(
    solution_id="SOL-C",
    problem_id="P-C",
    steps=[SolutionStep(step_no=i + 1, latex_text=t) for i, t in enumerate(SOLUTION_TEXTS)],
    answer_latex="x=6",
)
PROBLEM = Problem(
    problem_id="P-C", problem_latex="2(x-3)=6", skills=["linear_equation", "distribution"]
)


def align(attempt_id: str, texts: list[str]):
    a = StudentAttempt(
        attempt_id=attempt_id, student_id="S1", problem_id="P-C", solution_text="\n".join(texts)
    )
    return align_attempt(PROBLEM, a, SOLUTION)


def pmap(alignment) -> dict[int, int]:
    return {p.attempt_step_no: p.solution_step_no for p in alignment.pairs}


@pytest.fixture(autouse=True)
def _fresh():
    from app.pipeline.analyzer import reset_knowledge

    reset_knowledge()
    yield
    reset_knowledge()


# ===========================================================================
# 좌우반전은 normalized_text 로 확정 정렬된다 (Phase 7-2-3)
#
# 아래 테스트들은 Phase 7-2-2 시점에는 '6=x' 가 모호로 거부되어 None 이었다.
# 좌우반전 정규화(Phase 7-2-3) 도입으로 정답 x=6 에 '확실하게' 연결되므로
# 기대값을 갱신했다. 정렬이 포기된 것이 아니라 오연결이 바로잡힌 것이다.
# ===========================================================================

def test_answer_written_early_is_not_pinned_to_wrong_solution_step():
    """학생이 답을 3단계에 적었을 때 정답 3단계(2x=12)가 아니라 4단계(x=6)에 연결된다.

    Phase 7-2-2: (1,1) (2,2), 3단계는 모호 → None
    Phase 7-2-3: (1,1) (2,2) (3,4) — '6=x' ↔ 'x=6' 는 좌우반전이라 확정 가능
    """
    al = align("C1", ["2(x-3)=6", "2x-6=6", "6=x"])
    assert pmap(al) == {1: 1, 2: 2, 3: 4}
    assert al.ambiguous_attempt_steps == []
    assert al.pairs[2].match_kind == "normalized_text"


def test_two_step_attempt_answer_pinned_to_solution_step_four():
    al = align("C2", ["2(x-3)=6", "6=x"])
    assert pmap(al) == {1: 1, 2: 4}
    assert al.ambiguous_attempt_steps == []


def test_swapped_step_has_aligned_step_no():
    a = StudentAttempt(
        attempt_id="C2b", student_id="S1", problem_id="P-C",
        solution_text="2(x-3)=6\n6=x",
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    assert [s.aligned_step_no for s in r.attempt.steps] == [1, 4]


# ===========================================================================
# 게이트 커버리지 유지 — 여전히 모호한 경우 (좌우반전이 아니므로 판별 불가)
# ===========================================================================

def test_non_swap_ambiguous_step_still_none():
    """'x=3+3' 는 정답 'x=6' 의 좌우반전도 아니므로 여전히 모호 → None."""
    al = align("G1", ["2(x-3)=6", "2x-6=6", "x=3+3"])
    assert pmap(al) == {1: 1, 2: 2}
    assert al.ambiguous_attempt_steps == [3]
    assert al.pairs[0].match_kind == "identical_text"


def test_duplicate_canonical_solution_steps_still_ambiguous():
    """정답에 'x=6' 과 '6=x' 가 모두 있으면 어느 ���단자인지 확정할 수 없다 → None."""
    dup = CorrectSolution(
        solution_id="SOL-DUP",
        problem_id="P-C",
        steps=[SolutionStep(step_no=1, latex_text="x=6"), SolutionStep(step_no=2, latex_text="6=x")],
        answer_latex="x=6",
    )
    a = StudentAttempt(
        attempt_id="G2", student_id="S1", problem_id="P-C", solution_text="6=x"
    )
    al = align_attempt(PROBLEM, a, dup)
    assert al.pairs == []
    assert al.ambiguous_attempt_steps == [1]


def test_solution_step_for_returns_none_for_ambiguous():
    al = align("M4", ["2(x-3)=6", "2x-6=6", "x=3+3"])
    assert al.solution_step_for(3) is None
    assert al.solution_step_for(1) == 1


# ===========================================================================
# 모호 판정 규칙 자체
# ===========================================================================

def test_unambiguous_math_equivalent_is_accepted():
    """유일한 후보일 때는 math_equivalent 도 채택한다 (게이트가 과도하지 않도록)."""
    al = align("C3", ["2(x-3)=6", "2x-6=6", "2x=6*2", "x=6"])
    assert 3 not in al.ambiguous_attempt_steps
    assert al.pairs, "유일한 후보인데도 정렬하지 않았다"


def test_identical_text_never_rejected_by_gate():
    """문자 일치는 근거가 있으므로 모호해도 채택한다."""
    al = align("C4", ["2(x-3)=6", "2x-6=6", "2x=12", "x=6"])
    assert pmap(al) == {1: 1, 2: 2, 3: 3, 4: 4}
    assert al.ambiguous_attempt_steps == []


def test_strict_one_to_one_stage_keeps_math_equivalent():
    """단계 수가 같으면 구조적 근거로 채택 게이트를 적용하지 않는다 (기존 동작 유지)."""
    al = align("C5", ["2(x-3)=6", "2x-6=6", "2x=12", "6=x"])
    assert pmap(al) == {1: 1, 2: 2, 3: 3, 4: 4}
    assert al.ambiguous_attempt_steps == []


# ===========================================================================
# 회귀 — 게이트가 기존 정렬을 깨지 않았는지
# ===========================================================================

@pytest.mark.parametrize(
    "texts,expected",
    [
        (SOLUTION_TEXTS, {1: 1, 2: 2, 3: 3, 4: 4}),                      # 전부 동일
        (["2(x-3)=6", "2x-6=6", "2x=12", "x=6"], {1: 1, 2: 2, 3: 3, 4: 4}),
        (["2(x-3)=6", "2x-6=6+0", "2x=12", "x=6"], {1: 1, 2: 2, 3: 3, 4: 4}),  # 중간만 다른표현
        (["2(x-3)=6", "2x-6=6", "2x=12", "6=x"], {1: 1, 2: 2, 3: 3, 4: 4}),    # 마지막 좌우반전
        (["2(x-3)=6", "2x-6=6", "0.5x=3", "x=6"], {1: 1, 2: 2, 3: 3, 4: 4}),  # 소수 표현
        (["2(x-3)=6", "2x-6=6", "x=6"], {1: 1, 2: 2, 3: 4}),              # 3단계 생략
        (["2(x-3)=6", "2x-6=6", "2x=12"], {1: 1, 2: 2, 3: 3}),           # 4단계 생략
        (["2(x-3)=6", "2x-6=6"], {1: 1, 2: 2}),                          # 2단계만
        (["2(x-3)=6", "2x-6=6", "2x=6+6", "2x=12", "x=6"], {1: 1, 2: 2, 4: 3, 5: 4}),  # 삽입
    ],
)
def test_existing_alignment_preserved(texts, expected):
    al = align("R", texts)
    assert pmap(al) == expected


def test_inserted_step_still_detected():
    al = align("R2", ["2(x-3)=6", "2x-6=6", "2x=6+6", "2x=12", "x=6"])
    assert al.unaligned_attempt_steps == [3]
    assert al.ambiguous_attempt_steps == [], "삽입 단계를 모호로 오인했다"


def test_skipped_step_still_detected():
    al = align("R3", ["2(x-3)=6", "2x-6=6", "x=6"])
    assert al.unaligned_solution_steps == [3]
    assert pmap(al)[3] == 4


# ===========================================================================
# 모델 / 보존성
# ===========================================================================

def test_ambiguous_field_defaults_empty():
    al = align("M1", SOLUTION_TEXTS)
    assert al.ambiguous_attempt_steps == []
    dumped = al.model_dump()
    assert dumped["ambiguous_attempt_steps"] == []


def test_ambiguous_steps_are_subset_of_unaligned():
    al = align("M2", ["2(x-3)=6", "6=x"])
    assert set(al.ambiguous_attempt_steps) <= set(al.unaligned_attempt_steps)


def test_ambiguous_solution_step_not_consumed():
    """거부된 쌍이 정답 단계를 점유하면 안 된다 (이후 정렬이 틀려짐)."""
    al = align("M3", ["2(x-3)=6", "6=x"])
    assert 2 not in pmap(al).values(), "거부된 정답 단계를 소비했다"
    assert 2 in al.unaligned_solution_steps


def test_alignment_is_deterministic():
    a = align("D1", ["2(x-3)=6", "2x-6=6", "6=x"])
    b = align("D1", ["2(x-3)=6", "2x-6=6", "6=x"])
    assert pmap(a) == pmap(b)
    assert a.ambiguous_attempt_steps == b.ambiguous_attempt_steps


# ===========================================================================
# ErrorRecord / api_response 영향
# ===========================================================================

def test_error_ref_solution_step_uses_normalized_alignment():
    """모호하지 않은 좌우반전 기준 단계는 정답 단계로 연결된다.

    2(x-3)=6 → 6=x(정답 x=6 에 확정 대응) → x=5(오류)
    Phase 7-2-2 에서는 기준 단계가 모호해 None 이었으나, 좌우반전 정규화로
    정답 4단계(x=6)에 연결된다.
    """
    a = StudentAttempt(
        attempt_id="E1", student_id="S1", problem_id="P-C",
        solution_text="2(x-3)=6\n6=x\nx=5",
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    e = r.errors[0]
    assert e.step_no == 3
    assert e.ref_step_no == 2
    assert e.ref_solution_step_no == 4, "정답 x=6(4단계)에 연결되어야 한다"
    assert r.alignment is not None
    assert 2 not in r.alignment.ambiguous_attempt_steps


def test_api_response_unchanged_by_gate():
    from app.schemas import AnalyzeSolutionResponse

    a = StudentAttempt(
        attempt_id="E2", student_id="S1", problem_id="P-C", solution_text="2(x-3)=6\nx=6"
    )
    r = analyze_attempt(PROBLEM, a, solution=SOLUTION)
    parsed = AnalyzeSolutionResponse(**r.api_response)
    assert parsed.correct is True
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)


def test_analyze_untouched():
    from app.pipeline.analyzer import analyze, reset_knowledge

    reset_knowledge()
    out = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="reg", problem_skill="linear_equation")
    assert out["error_step"] == 2
    assert out["skill"] == "distribution"
    assert out["misconception_id"] == "2.1"
    assert out["valid"] == [True, False]
