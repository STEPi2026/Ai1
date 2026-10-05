"""S1 회귀 — 다변수 등식 동치 판정.

기존 구현은 다변수에서 잔차 '차이'를 비교해 0이 아닌 상수배를 구분하지 못했다.
  x+y=3 ↔ 2x+2y=6  → False (오탐)
해결: 잔차가 0이 아닌 상수배면 solve() 없이 True 확정(fast path),
      나머지 다변수는 시스템 해 집합 비교. 미해석·조건부 결과는 False로
      단정하지 않고 None(검증 불가)으로 남긴다 (ADR-03).

1변수 기존 판정(x^2=0 ↔ x=0 동치, x^2=16 ↮ x=4 비동치)은 보존되어야 한다.
"""
from __future__ import annotations

import pytest

import app.pipeline.verifier as V
from app.pipeline.analyzer import analyze, reset_knowledge
from app.pipeline.parser import parse_line
from app.pipeline.verifier import equivalent, verify


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def eq(text: str):
    return parse_line(text)


# ---------------------------------------------------------------------------
# S1.1 상수배 동치식 인정 (핵심 요구사항)
# ---------------------------------------------------------------------------

def test_constant_multiple_two_variable_is_equivalent():
    assert equivalent(eq("x+y=3"), eq("2x+2y=6")) is True


def test_constant_multiple_with_negative_coefficient():
    assert equivalent(eq("x-y=1"), eq("2x-2y=2")) is True


def test_constant_multiple_three_step_chain():
    """x+y=7 -> 2x+2y=14 -> x=7 : 각 단계 처리 검증.

    2단계(상수배)는 True. 3단계(x=7)는 2변수 등식 2x+2y=14의 해집합이
    무한(매개변수 해)이므로 결정적으로 비교할 수 없다 → 오류(False)로
    단정하지 않고 검증 불가(None)로 남긴다.
    """
    v = verify("x+y=7", ["x+y=7", "2x+2y=14", "x=7"])
    assert v.valid[0] is True   # 문제식 전사
    assert v.valid[1] is True   # 양변 2배 — 기존엔 오탐이던 케이스
    assert v.valid[2] is None   # 검증 불가 (False가 아님)
    assert v.first_error_index is None  # 오류로 단정하지 않음


def test_api_no_longer_flags_valid_double_step():
    """API 레벨: 올바른 2배 단계를 오류로 보지 않는다 (기존 error_step=2)."""
    r = analyze("x+y=7", "x+y=7\n2x+2y=14\nx=3", student_id="s1-mv")
    assert r["correct"] is True
    assert r["error_step"] is None


# ---------------------------------------------------------------------------
# S1.2 실제 비동치식은 여전히 오류로 판정
# ---------------------------------------------------------------------------

def test_decisive_multivariate_non_equivalent_is_false():
    """양쪽 모두 구체적(concrete) 해 집합인 경우 비동치를 확정한다."""
    assert equivalent(eq("x*y=0"), eq("(x-1)*(y-1)=0")) is False


def test_verify_detects_multivariate_error():
    v = verify("x*y=0", ["x*y=0", "(x-1)*(y-1)=0"])
    assert v.valid == [True, False]
    assert v.first_error_index == 1


def test_univariate_non_equivalent_still_false():
    assert equivalent(eq("2x=6"), eq("x=4")) is False


# ---------------------------------------------------------------------------
# S1.3 조건부/미해석 결과는 False로 단정하지 않는다
# ---------------------------------------------------------------------------

def test_parametric_solution_difference_is_none_not_false():
    """매개변수 해의 파라미터 표현이 달라 비교 불가 → None (ADR-03)."""
    assert equivalent(eq("x+y=3"), eq("2x+3y=6")) is None


def test_budget_exhaustion_returns_none_not_false():
    budget = V._SolveBudget(limit=0)
    assert equivalent(eq("2x=6"), eq("x=4"), budget) is None


# ---------------------------------------------------------------------------
# S1.4 1변수 기존 동작 보존
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("2x=6", "x=3", True),        # 나눗셈 — 동치
        ("2x=6", "x=4", False),       # 나눗셈 오류 — 비동치
        ("x^2=0", "x=0", True),       # 해 누락 판정 보존(동일 해집합)
        ("x^2=16", "x=4", False),     # README:115 해 누락 — 비동치 유지
        ("x^2-5x+6=0", "x^2-5x+6=0", True),
        ("x^2-5x+6=0", "x^2-5x+7=0", False),
    ],
)
def test_univariate_behavior_preserved(left, right, expected):
    assert equivalent(eq(left), eq(right)) is expected


def test_high_degree_still_deferred():
    assert equivalent(eq("x^99=1"), eq("x=1")) is None


# ---------------------------------------------------------------------------
# S1.5 solve() 호출 최소화
# ---------------------------------------------------------------------------

def test_fast_path_avoids_solve_calls(monkeypatch):
    """잔차 비比例为 상수배로 판정 가능 → solve()를 호출하지 않는다."""
    calls: list[int] = []
    original = V.solve

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(V, "solve", counting)

    assert equivalent(eq("2x=6"), eq("x=3")) is True
    assert len(calls) == 0, "1변수 상수배는 solve 없이 판정되어야 한다"

    assert equivalent(eq("x+y=3"), eq("2x+2y=6")) is True
    assert len(calls) == 0, "다변수 상수배도 solve 없이 판정되어야 한다"


def test_solve_called_only_when_fast_path_insufficient(monkeypatch):
    """비례 판정으로 결론이 안 서는 경우에만 solve()로 넘어간다."""
    calls: list[int] = []
    original = V.solve

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(V, "solve", counting)

    assert equivalent(eq("2x=6"), eq("x=4")) is False
    assert len(calls) == 2, "비동치 판정에만 solve 2회 (양쪽 해집합)"
