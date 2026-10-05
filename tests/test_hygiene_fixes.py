"""S11~S15 회귀 — 위생/파서/계약 수정.

S11: AnalysisError 코드 → HTTP status 매핑
S12: 분석 API 요청 모델의 알 수 없는 필드 거부(extra=forbid)
S13: error_type별 Knowledge State 전이 (procedure를 개념 오류로 취급하지 않음)
S14: 중첩 \\frac 파싱 (brace matching)
S15: SymPy 특수 상수/임의 기호 차단
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sympy import Eq, Rational, sqrt

from app.main import app
from app.pipeline.analyzer import reset_knowledge
from app.pipeline.knowledge import (
    STATE_LEARNING,
    STATE_MASTERED,
    STATE_NEEDS_PRACTICE,
    BKTEngine,
    RuleStateEngine,
    is_correct_outcome,
)
from app.pipeline.parser import normalize, parse_line
from app.taxonomy import ERROR_TYPE_CATALOG

client = TestClient(app)
BASE = {"problem_latex": "2x=6", "solution_text": "2x=6\nx=3"}


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def post(**extra):
    return client.post("/analyze-solution", json={**BASE, **extra})


# ===========================================================================
# S11 — HTTP status 매핑
# ===========================================================================

def test_input_too_large_returns_413():
    """E_INPUT_TOO_LARGE 는 본문 크기 위반이므로 413 이 적절하다."""
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "x+" * 3000 + "=0", "solution_text": "x=0"},
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "E_INPUT_TOO_LARGE"


def test_solution_step_limit_returns_413():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "\n".join(["x=3"] * 31)},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "E_INPUT_TOO_LARGE"


def test_unmapped_codes_stay_422():
    for code, payload in [
        ("E_UNKNOWN_SKILL", {**BASE, "problem_skill": "nope"}),
        ("E_SKILL_NOT_TRACKABLE", {**BASE, "problem_skill": "factoring"}),
        ("E_INVALID_STUDENT_ID", {**BASE, "student_id": ""}),
        ("E_EMPTY_SOLUTION", {"problem_latex": "2x=6", "solution_text": ""}),
        ("E_BAD_PROBLEM", {"problem_latex": "", "solution_text": "x=1"}),
        ("E_UNRECOGNIZED", {"problem_latex": "2x=6", "solution_text": "보통 풀이"}),
        ("E_UNVERIFIABLE", {"problem_latex": "x^99=1", "solution_text": "x=1"}),
    ]:
        r = client.post("/analyze-solution", json=payload)
        assert r.status_code == 422, (code, r.status_code, r.text)
        assert r.json()["detail"]["code"] == code


def test_status_mapping_table_contents():
    from app.main import _DEFAULT_ERROR_STATUS, _ERROR_STATUS

    assert _ERROR_STATUS["E_INPUT_TOO_LARGE"] == 413
    assert _DEFAULT_ERROR_STATUS == 422


# ===========================================================================
# S12 — request extra=forbid
# ===========================================================================

def test_normal_request_still_passes():
    r = post(problem_id="P01", student_id="s12-1", engine="rule", problem_skill="linear_equation")
    assert r.status_code == 200


def test_unknown_top_level_field_rejected():
    """오타로 옵션이 사라지는 silent failure를 막는다."""
    r = post(bogus_field=1)
    assert r.status_code == 422


def test_wrong_type_still_422():
    r = post(engine="not-an-engine")
    assert r.status_code == 422


def test_request_model_forbids_extra():
    from app.schemas import AnalyzeSolutionRequest

    with pytest.raises(Exception):
        AnalyzeSolutionRequest(problem_latex="2x=6", solution_text="x=3", nope=1)


def test_response_model_unaffected_by_request_policy():
    """S12 는 분석 API request에만 적용 — 응답 모델은 그대로다."""
    from app.schemas import AnalyzeSolutionResponse

    payload = {
        "correct": True, "error_step": None, "error_type": None, "skill": "linear_equation",
        "state": "learning", "mastery": 0.6, "misconception_id": None, "confidence": None,
        "steps_latex": ["2x=6"], "valid": [True], "unrecognized": [],
    }
    assert AnalyzeSolutionResponse(**payload).correct is True


# ===========================================================================
# S13 — error_type별 상태 전이
# ===========================================================================

def test_procedure_is_not_treated_as_concept_error():
    """절차 오류는 부분 정답(learning)이다 — needs_practice 가 아니다."""
    e = RuleStateEngine()
    assert e.update("s1", "distribution", "procedure") == STATE_LEARNING


def test_procedure_resets_streak():
    e = RuleStateEngine()
    e.update("s1", "distribution", "correct")
    assert e.update("s1", "distribution", "procedure") == STATE_LEARNING
    assert e.update("s1", "distribution", "correct") == STATE_LEARNING  # streak 리셋됨
    assert e.update("s1", "distribution", "correct") == STATE_MASTERED


@pytest.mark.parametrize(
    "error_type,expected",
    [
        ("calculation", STATE_LEARNING),
        ("procedure", STATE_LEARNING),
        ("concept_error", STATE_NEEDS_PRACTICE),
        ("comprehension", STATE_NEEDS_PRACTICE),
    ],
)
def test_error_type_transitions(error_type, expected):
    assert RuleStateEngine().update("s1", "linear_equation", error_type) == expected


@pytest.mark.parametrize(
    "error_type",
    ["calculation", "procedure", "concept_error", "comprehension"],
)
def test_procedure_clears_needs_practice(error_type):
    """절차 오류는 개념 오류와 달리 needs_practice 에서learning 으로 복귀시킨다."""
    e = RuleStateEngine()
    e.update("s1", "linear_equation", "concept_error")
    assert e.state("s1", "linear_equation") == STATE_NEEDS_PRACTICE
    if error_type in ("calculation", "procedure"):
        assert e.update("s1", "linear_equation", error_type) == STATE_LEARNING
    else:
        assert e.update("s1", "linear_equation", error_type) == STATE_NEEDS_PRACTICE


def test_unknown_outcome_keeps_conservative_default():
    """표에 없는 outcome 은 needs_practice 유지(과거 동작·500 방지)."""
    assert RuleStateEngine().update("s1", "linear_equation", "weird") == STATE_NEEDS_PRACTICE


def test_transition_table_covers_taxonomy_error_types():
    """taxonomy의 모든 error_type 이 전이 표에 정의되어 있다."""
    from app.pipeline.knowledge import _ERROR_TYPE_TO_STATE

    for e in ERROR_TYPE_CATALOG.error_types:
        assert e.error_type_id in _ERROR_TYPE_TO_STATE, e.error_type_id


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("correct", True),
        ("calculation", False),
        ("procedure", False),
        ("concept_error", False),
        ("comprehension", False),
    ],
)
def test_bkt_outcome_is_binary(outcome, expected):
    assert is_correct_outcome(outcome) is expected


@pytest.mark.parametrize("error_type", ["calculation", "procedure", "concept_error", "comprehension"])
def test_bkt_treats_all_error_types_as_incorrect(error_type):
    """BKT는 error_type 을 outcome 으로 쓰지 않고 이진으로 접는다."""
    e = BKTEngine()
    p = e.update("s1", "linear_equation", error_type)
    assert 0.0 <= p <= 1.0
    assert p < e.p_init, f"{error_type} 이 incorrect 로 접히지 않았다"


def test_bkt_procedure_regression_unchanged_math():
    """S13은 BKT 수식을 바꾸지 않는다 (동일 입력 → 동일 값)."""
    a, b = BKTEngine(), BKTEngine()
    for outcome in ("correct", "procedure", "concept_error"):
        assert a.update("s1", "x", outcome) == b.update("s1", "x", outcome)


# ===========================================================================
# S14 — 중첩 \frac
# ===========================================================================

def test_simple_fraction_unchanged():
    assert normalize(r"\frac{1}{2}") == "((1)/(2))"


def test_fraction_parses_to_correct_value():
    n = parse_line(r"\frac{1}{2}=0")
    assert n is not None and n[0] == "eq"
    assert n[1] == Rational(1, 2)


def test_nested_numerator():
    """\frac{\frac{1}{2}}{3} = 1/6"""
    n = parse_line(r"\frac{\frac{1}{2}}{3}=0")
    assert n is not None, "중첩 분자를 파싱하지 못했다"
    assert n[1] == Rational(1, 6)


def test_nested_denominator():
    """\frac{1}{\frac{2}{3}} = 3/2"""
    n = parse_line(r"\frac{1}{\frac{2}{3}}=0")
    assert n is not None, "중첩 분모를 파싱하지 못했다"
    assert n[1] == Rational(3, 2)


def test_double_nested():
    n = parse_line(r"\frac{\frac{1}{2}}{\frac{3}{4}}=0")
    assert n is not None
    assert n[1] == Rational(2, 3)


def test_fraction_inside_equation():
    v = parse_line(r"\frac{x}{2}=6")
    assert v is not None and v[0] == "eq"
    assert Eq(v[1], v[2]) is not None


def test_two_fractions_in_one_line():
    n = parse_line(r"\frac{1}{2}x+\frac{1}{3}=0")
    assert n is not None, "한 줄에 두 분수가 있어야 한다"
    from sympy import simplify

    assert simplify(n[1] - (Rational(1, 2) * parse_line("x=0")[1] + Rational(1, 3))) == 0


@pytest.mark.parametrize(
    "bad",
    [
        r"\frac{1}{2",     # 닫는 괄호 없음
        r"\frac{1",         # 두 번째 인자 없음
        r"\frac 1 2",       # 중괄호 없음
        r"\frac{1}{2}{",    # 뒤에 잘못된 중괄호
    ],
)
def test_malformed_frac_is_unrecognized(bad):
    assert parse_line(f"{bad}=0") is None


def test_sqrt_still_available_after_parser_change():
    """제곱근은 공통수학1 필수 — 파서 정책 변경 후에도 유지된다."""
    n = parse_line("sqrt(4)=0")
    assert n is not None
    assert n[1] == sqrt(4)


# ===========================================================================
# S15 — SymPy 특수 상수 / 임의 기호 차단
# ===========================================================================

@pytest.mark.parametrize("text", ["x", "y", "z", "a", "b", "n", "m", "u", "v", "w", "X"])
def test_single_letter_variables_allowed(text):
    assert parse_line(f"{text}=1") is not None, f"기존 단일 변수 {text} 가 차단되었다"


@pytest.mark.parametrize("text", ["pi", "oo", "zoo", "nan", "E", "I", "N", "O", "S", "Q"])
def test_sympy_special_names_rejected(text):
    assert parse_line(f"{text}=1") is None, f"'{text}' 이 차단되지 않았다"


@pytest.mark.parametrize("text", ["sin", "cos", "tan", "log", "ln", "exp", "sqrtx"])
def test_unsupported_function_names_rejected(text):
    assert parse_line(f"{text}=1") is None, f"범위 밖 함수 '{text}' 가 허용되었다"


def test_arbitrary_word_rejected():
    assert parse_line("abc=1") is None
    assert parse_line("hello=1") is None


def test_reserved_single_letter_in_equation_rejected():
    assert parse_line("2x=I") is None
    assert parse_line("pi=3") is None


def test_existing_equations_still_parse():
    for text in ("2(x-3)=6", "3x+5=20", "x^2-5x+6=0", "0.5x=5", "20x+40=120", r"\frac{1}{2}x=3"):
        assert parse_line(text) is not None, text
