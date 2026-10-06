"""복잡도/크기 가드 — 검토 시 발견한 hang·지연 이슈 회귀 고정.

검토 시 발견: x^99=1 입력이 sympy.solve()에서 무기한 hang (20초+).
차수 가드로 판정 보류(E_UNVERIFIABLE)로 전환해야 한다.
"""
import time

import pytest

from app.pipeline.analyzer import AnalysisError, analyze, reset_knowledge
from app.pipeline.verifier import equivalent
from app.pipeline.parser import parse_line


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def test_high_degree_equation_does_not_hang():
    """x^99=1은 solve()가 걸리므로 판정 보류 → 422. (기존: 무기한 hang)"""
    t0 = time.time()
    with pytest.raises(AnalysisError) as ei:
        analyze("x^99=1", "x^99=1\nx=1", student_id="guard")
    assert ei.value.code == "E_UNVERIFIABLE"
    assert time.time() - t0 < 5, "5초 내 반환되어야 함"


def test_high_degree_pair_is_none_not_hang():
    a = parse_line("x^99=1")
    b = parse_line("x=1")
    t0 = time.time()
    assert equivalent(a, b) is None  # 추측 금지, 보류
    assert time.time() - t0 < 3


def test_quadratic_still_verifiable():
    """2차(기획서 범위 내)는 가드로 막히지 않아야 한다."""
    a = parse_line("x^2-5x+6=0")
    b = parse_line("x^2-5x+6=0")
    assert equivalent(a, b) is True


def test_linear_normal_case_untouched():
    assert equivalent(parse_line("2x=6"), parse_line("x=3")) is True
    assert equivalent(parse_line("2x=6"), parse_line("x=4")) is False


def test_problem_too_long_rejected():
    with pytest.raises(AnalysisError) as ei:
        analyze("x+" * 3000 + "=0", "x=0", student_id="guard")
    assert ei.value.code == "E_INPUT_TOO_LARGE"


def test_solution_too_long_rejected():
    with pytest.raises(AnalysisError) as ei:
        analyze("x=0", "\n".join(f"x={i}" for i in range(500)), student_id="guard")
    assert ei.value.code == "E_INPUT_TOO_LARGE"
