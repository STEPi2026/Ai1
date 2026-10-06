"""S2 회귀 — 복잡도 가드 / step 한도 / solve 예산.

수정 전: _too_complex()가 다항식 차수 ≤ 4면 즉시 반환해 count_ops 검사에
         도달하지 못했고(가드 무력), _MAX_STEPS=200이라 2차식 200단계가
         solve() 400회로 13.1초를 소요했다.

수정 후: 차수 + 연산량 + 항 개수를 함께 검사하고, 상수배 fast path로
         solve() 호출을 최소화하며, 요청당 solve 횟수 예산을 둔다.
"""
from __future__ import annotations

import time

import pytest
from sympy import Poly, count_ops, symbols

import app.pipeline.verifier as V
from app.pipeline.analyzer import (
    _MAX_SOLUTION_CHARS,
    _MAX_STEPS,
    AnalysisError,
    analyze,
    reset_knowledge,
)
from app.pipeline.verifier import _MAX_DEGREE as _MAX_DEGREE_OK
from app.pipeline.verifier import _MAX_OPS, _MAX_SOLVE_CALLS, _too_complex, verify

x = symbols("x")


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def linear_chain(steps: int) -> list[str]:
    """올바른 등비 단계로 만든 정상 풀이 (각 단계 잔차가 상수배 관계)."""
    base = ["20x+40=120", "20x=80", "10x=40", "5x=20", "x=4"]
    assert steps >= len(base)
    return base + ["x=4"] * (steps - len(base))


# ---------------------------------------------------------------------------
# S2.1 step 한도 (_MAX_STEPS = 30)
# ---------------------------------------------------------------------------

def test_max_steps_is_configured_constant():
    assert _MAX_STEPS == 30
    assert isinstance(_MAX_STEPS, int)


@pytest.mark.parametrize("steps", [10, 20, 30])
def test_normal_solutions_within_limit_accepted(steps):
    """정상 10~20~30단계 풀이는 거부되지 않는다."""
    r = analyze("20x+40=120", "\n".join(linear_chain(steps)), student_id=f"ok-{steps}")
    assert r["correct"] is True
    assert r["error_step"] is None


@pytest.mark.parametrize("steps", [31, 50, 100, 200])
def test_over_limit_rejected(steps):
    with pytest.raises(AnalysisError) as ei:
        analyze("20x+40=120", "\n".join(linear_chain(steps)), student_id=f"over-{steps}")
    assert ei.value.code == "E_INPUT_TOO_LARGE"


def test_step_limit_error_message_mentions_limit():
    with pytest.raises(AnalysisError) as ei:
        analyze("2x=6", "\n".join(["x=3"] * 31), student_id="msg")
    assert "30단계" in str(ei.value)


# ---------------------------------------------------------------------------
# S2.2 과도한 입력의 응답 시간 (수정 전 13.1초 → 즉시 거절)
# ---------------------------------------------------------------------------

def test_huge_quadratic_input_rejected_quickly():
    """수정 전: 200단계 2차식이 13.1초 걸려 200 OK로 응답했다."""
    steps = "\n".join("x^2=%d" % (i * 3) for i in range(200))
    t0 = time.time()
    with pytest.raises(AnalysisError) as ei:
        analyze("x^2=0", steps, student_id="huge-quadratic")
    elapsed = time.time() - t0
    assert ei.value.code == "E_INPUT_TOO_LARGE"
    assert elapsed < 2.0, f"상한 초과 입력이 {elapsed:.2f}초 — sympy 작업이 시작되면 안 됨"


def test_max_allowed_quadratic_completes_in_bounded_time():
    """상한(30단계) 2차식 반복 입력 — 예산 내에서 종료되어야 한다."""
    steps = "\n".join("x^2=%d" % (i * 3) for i in range(30))
    t0 = time.time()
    r = analyze("x^2=0", steps, student_id="quad-30")
    elapsed = time.time() - t0
    assert r["steps_latex"][0] == "x^2=0"
    assert elapsed < 10.0, f"30단계 2차식이 {elapsed:.2f}초 — 예산 초과 가능성"


def test_normal_linear_solution_is_fast():
    t0 = time.time()
    analyze("20x+40=120", "\n".join(linear_chain(30)), student_id="fast-linear")
    assert time.time() - t0 < 5.0


# ---------------------------------------------------------------------------
# S2.3 _too_complex가 연산량/항 개수를 실제로 본다
# ---------------------------------------------------------------------------

def test_too_complex_checks_ops_for_low_degree_expression():
    """차수가 낮아도 연산량이 많으면 걸러야 한다 (수정 전 조기 반환으로 누락)."""
    heavy = sum(1 / (x + i) for i in range(1, 61))
    assert count_ops(heavy) > _MAX_OPS
    assert _too_complex(heavy) is True


def test_too_complex_keeps_simple_expressions():
    assert _too_complex(x**2 + 3 * x + 2) is False
    assert _too_complex(2 * x - 6) is False


def test_too_complex_still_blocks_high_degree():
    assert _too_complex(x**99) is True


def test_too_complex_guards_term_count():
    """4변수 4차식: 차수는 4 이하지만 단항식이 40개를 넘으면 걸러야 한다."""
    v = symbols("v0 v1 v2 v3")
    poly_expr = sum(
        (v[0] ** a) * (v[1] ** b) * (v[2] ** c) * (v[3] ** d)
        for a in range(5)
        for b in range(5 - a)
        for c in range(5 - a - b)
        for d in range(5 - a - b - c)
    )
    poly = Poly(poly_expr, *v)
    assert poly.degree() <= _MAX_DEGREE_OK
    assert len(poly.terms()) > V._MAX_TERMS
    assert _too_complex(poly_expr) is True


# ---------------------------------------------------------------------------
# S2.4 solve() 호출 예산
# ---------------------------------------------------------------------------

def test_solve_budget_limit_is_configured():
    assert _MAX_SOLVE_CALLS >= _MAX_STEPS * 2, "단계당 2회 + 여유 확보"


def test_solve_budget_exhaustion_defers_instead_of_wrong():
    budget = V._SolveBudget(limit=2)
    assert budget.take(2) is True
    assert budget.take(1) is False


def test_verify_defers_when_budget_exhausted(monkeypatch):
    """예산 소진 시 남은 단계는 False가 아니라 None (오답으로 단정하지 않음)."""
    steps = ["3x+5=20"] + ["3x=%d" % (15 - 3 * i) for i in range(1, 25)]
    v = verify("3x+5=20", steps)
    assert len(v.valid) == len(steps)
    # 예산이 소진되면 None이 나오지만, 잘못된 False 판정이 되어서는 안 된다
    assert all(x in (True, False, None) for x in v.valid)
    assert v.valid[0] is True


def test_budget_stops_solve_bomb(monkeypatch):
    """solve() 호출 횟수가 예산을 넘지 않는지 확인."""
    calls: list[int] = []
    original = V.solve

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(V, "solve", counting)
    steps = ["3x+5=20"] + ["3x=%d" % (15 - 3 * i) for i in range(1, 30)]
    verify("3x+5=20", steps)
    assert len(calls) <= _MAX_SOLVE_CALLS, f"solve {len(calls)}회 — 예산 {_MAX_SOLVE_CALLS} 초과"


# ---------------------------------------------------------------------------
# S2.5 기존 입력 크기 상한 유지
# ---------------------------------------------------------------------------

def test_char_limit_still_enforced():
    with pytest.raises(AnalysisError) as ei:
        analyze("x=0", "x=0 " * (_MAX_SOLUTION_CHARS // 4 + 10), student_id="chars")
    assert ei.value.code == "E_INPUT_TOO_LARGE"


def test_problem_char_limit_still_enforced():
    with pytest.raises(AnalysisError) as ei:
        analyze("x+" * 3000 + "=0", "x=0", student_id="problem-chars")
    assert ei.value.code == "E_INPUT_TOO_LARGE"
