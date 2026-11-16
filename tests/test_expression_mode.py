"""A6 P1 — 표현식 문제 판정 + 부등식 거절 계약 회귀.

검증 범위:
  - 부등호 토큰(경계 매칭 포함) → E_UNSUPPORTED_INEQUALITY, retryable=false
  - problem_type/target_form 조합 → E_INVALID_TARGET_FORM
  - expression 동치 판정 / target_form 형태 검사
  - expression 모드에서 등식 줄 → REVIEW_REQUIRED + correct=null
  - 기존 equation 경로 무변경
"""
from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import AnalysisError, analyze, reset_knowledge
from app.pipeline.forms import form_matches
from app.pipeline.inequality import has_inequality_operator
from app.pipeline.parser import parse_line
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import Problem, StudentAttempt, analyze_attempt

client = TestClient(app)
URL = "/api/v1/attempts/{attempt_id}/analyze"


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    reset_repository()
    yield
    reset_knowledge()
    reset_repository()


def body(text: str, **kw) -> dict:
    payload = {
        "problem_id": "P1",
        "problem_latex": "(x+1)(x+2)",
        "problem_skills": ["distribution"],
        "student_id": "S1",
        "solution_text": text,
        "problem_type": "expression",
    }
    payload.update(kw)
    return payload


def post(attempt_id: str, text: str, **kw):
    return client.post(URL.format(attempt_id=attempt_id), json=body(text, **kw))


# ===========================================================================
# 부등호 판정 (경계 매칭)
# ===========================================================================

@pytest.mark.parametrize(
    "latex",
    [
        "x^2-3x+2<0",
        "x>1",
        "x\\lt 3",
        "x\\gt 3",
        "x\\le 3",
        "x\\leq 3",
        "x\\ge 3",
        "x\\geq 3",
        "x\\ne 3",
        "x\\neq 3",
        "x≤3",
        "x≥3",
        "x≠3",
        "x≦3",
        "x≧3",
        "x\\leqslant 3",
        "x\\geqslant 3",
    ],
)
def test_inequality_tokens_detected(latex):
    assert has_inequality_operator(latex) is True


@pytest.mark.parametrize(
    "latex",
    [
        "\\left(x+1\\right)(x+2)",  # \left 는 \le 접두사가 아니다
        "A \\gets B",                # \gets 는 \ge 접두사가 아니다
        "A \\ltimes B",              # \ltimes 는 \lt 접두사가 아니다
        "2x+3=7",
        "x^2+3x+2",
    ],
)
def test_non_inequality_commands_not_detected(latex):
    assert has_inequality_operator(latex) is False


def test_inequality_rejected_with_dedicated_code():
    with pytest.raises(AnalysisError) as exc:
        analyze(problem_latex="x^2-3x+2<0", solution_text="x<1")
    assert exc.value.code == "E_UNSUPPORTED_INEQUALITY"


# ===========================================================================
# 함수 표기 (B4 확정 — P1 범위 밖)
# ===========================================================================

@pytest.mark.parametrize(
    "latex",
    ["f(x)=2x+1", "g(x)=x^2", "h(x)=1/x", "sin(x)=1", "log(x)=2", "f (x)"],
)
def test_function_notation_is_detected(latex):
    from app.pipeline.notation import has_function_notation

    assert has_function_notation(latex) is True


@pytest.mark.parametrize(
    "latex",
    [
        "a(b+c)",          # 변수 a 와 괄호의 곱 — 정상 계산
        "sqrt(x+1)",       # P1 허용 함수
        "2x+3=7",
        "(x+1)(x+2)",
        "x^2+3x+2",
    ],
)
def test_function_detection_does_not_catch_products(latex):
    from app.pipeline.notation import has_function_notation

    assert has_function_notation(latex) is False


def test_function_problem_is_rejected_without_store():
    res = post(
        "A1",
        "f(x)=2x+1\nf(2)=5",
        problem_latex="f(x)=2x+1",
        problem_type="equation",
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_UNSUPPORTED_FUNCTION"
    assert detail["retryable"] is False
    assert get_repository().has_analysis("A1") is False


def test_function_notation_in_solution_is_review_not_verdict():
    """학생 풀이 줄의 f(2) 는 f*2 로 오독되므로 판정을 보류한다."""
    res = post("A1", "(x+1)(x+2)\nf(2)", problem_latex="(x+1)(x+2)")
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "REVIEW_REQUIRED"
    assert data["correct"] is None
    assert data["errors"] == []
    assert data["observations"] == []


def test_variable_times_parenthesis_still_judged():
    """a(b+c) 는 함수가 아니라 곱이므로 P1 판정 대상이다."""
    res = post("A1", "a(b+c)\na*b+a*c", problem_latex="a(b+c)")
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "ANALYZED"
    assert data["correct"] is True


# ===========================================================================
# target_form 형태 판정
# ===========================================================================

@pytest.mark.parametrize(
    "latex, form, expected",
    [
        ("12", "integer", True),
        ("-3/4", "integer", True),
        ("2x", "integer", False),
        ("x^2+3x+2", "expanded", True),
        ("2x", "expanded", True),
        ("(x+1)(x+2)", "expanded", False),
        # SymPy 가 숫자×괄호를 자동 분배하므로 2(x+1) 은 2x+2 로 파싱된다.
        # 형태 판정은 파싱된 식의 의미 구조를 본다(문자열 모양이 아니다).
        ("2*(x+1)", "expanded", True),
        ("(x+1)(x+2)", "factored", True),
        ("x(x+2)", "factored", True),
        ("(x+1)^2", "factored", True),
        ("x^2+3x+2", "factored", False),
        ("2*(x+1)", "factored", False),
    ],
)
def test_target_form_matching(latex, form, expected):
    node = parse_line(latex)
    assert node is not None and node[0] == "expr"
    assert form_matches(node[1], form) is expected


# ===========================================================================
# 계약 오류
# ===========================================================================

def test_equation_with_target_form_is_invalid_contract():
    res = post(
        "A1",
        "2(x-3)=6\nx=6",
        problem_latex="2(x-3)=6",
        problem_type="equation",
        target_form="expanded",
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_INVALID_TARGET_FORM"
    assert detail["retryable"] is False


def test_unknown_target_form_is_invalid_contract():
    res = post("A1", "x^2+3x+2", target_form="simplified")
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_INVALID_TARGET_FORM"
    assert detail["retryable"] is False


def test_oversized_target_form_is_rejected_without_echo():
    """비정상적으로 긴 값은 스키마에서 막고, 직접 호출 경로도 메시지를 자른다."""
    huge = "x" * 500
    res = post("A1", "x^2+3x+2", target_form=huge)
    assert res.status_code == 422
    assert get_repository().has_analysis("A1") is False

    with pytest.raises(AnalysisError) as exc:
        analyze(
            problem_latex="(x+1)(x+2)",
            solution_text="x^2+3x+2",
            problem_type="expression",
            target_form=huge,
        )
    assert exc.value.code == "E_INVALID_TARGET_FORM"
    assert huge not in str(exc.value)
    assert len(str(exc.value)) < 200


def test_inequality_api_returns_non_retryable_and_stores_nothing():
    res = post(
        "A1",
        "x<1",
        problem_latex="x^2-3x+2<0",
        problem_type="equation",
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_UNSUPPORTED_INEQUALITY"
    assert detail["retryable"] is False
    assert get_repository().has_analysis("A1") is False


# ===========================================================================
# expression 모드 정상 판정
# ===========================================================================

def test_expression_equivalent_is_correct():
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2", target_form="expanded")
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "ANALYZED"
    assert data["correct"] is True
    assert data["error_step"] is None


def test_expression_wrong_line_is_error():
    res = post("A1", "(x+1)(x+2)\nx^2+3x+3", target_form="expanded")
    assert res.status_code == 200
    data = res.json()
    assert data["correct"] is False
    assert data["analysis_status"] == "ANALYZED"
    assert data["first_error_step_index"] == 2
    assert data["error_type"] == "calculation"


def test_expression_target_form_violation_is_procedure():
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2", target_form="factored")
    assert res.status_code == 200
    data = res.json()
    assert data["correct"] is False
    assert data["first_error_step_index"] == 2
    assert data["error_type"] == "procedure"
    assert data["error_subtype"] == "method_mismatch"
    assert data["misconception_tags"] == []


def test_expression_without_target_form_checks_equivalence_only():
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2")
    assert res.status_code == 200
    data = res.json()
    assert data["correct"] is True


# ===========================================================================
# 판정 보류 (REVIEW_REQUIRED / correct=null)
# ===========================================================================

def test_expression_eq_line_is_review_required():
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2\nx^2+3x+2=0")
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "REVIEW_REQUIRED"
    assert data["correct"] is None
    assert data["errors"] == []
    assert data["first_error_step_index"] is None
    # 판정 보류는 Knowledge State 를 갱신하지 않는다.
    assert data["observations"] == []
    assert data["states"] == {}


def test_review_required_is_visible_in_history_and_summary():
    assert post("A1", "(x+1)(x+2)\nx^2+3x+2=0").status_code == 200

    history = client.get("/api/v1/students/S1/history")
    assert history.status_code == 200
    item = history.json()["items"][0]
    assert item["analysis_status"] == "REVIEW_REQUIRED"
    assert item["correct"] is None

    listing = client.get("/api/v1/attempts/A1/analyses")
    assert listing.status_code == 200
    summary = listing.json()["analyses"][0]
    assert summary["analysis_status"] == "REVIEW_REQUIRED"
    assert summary["correct"] is None


# ===========================================================================
# 기존 equation 경로 회귀 + attempt 충돌
# ===========================================================================

def test_equation_default_unchanged():
    res = post(
        "A1",
        "2(x-3)=6\n2x-6=6\n2x=12\nx=6",
        problem_latex="2(x-3)=6",
        problem_type="equation",
    )
    assert res.status_code == 200
    data = res.json()
    assert data["correct"] is True
    assert data["analysis_status"] == "ANALYZED"


def test_same_attempt_id_with_different_problem_type_conflicts():
    assert post("A1", "(x+1)(x+2)\nx^2+3x+2").status_code == 200
    res = post(
        "A1",
        "2(x-3)=6\nx=6",
        problem_latex="2(x-3)=6",
        problem_type="equation",
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"


# ===========================================================================
# 기존 /analyze-solution 경로도 같은 P1 계약을 따른다
# ===========================================================================

def test_legacy_analyze_solution_supports_expression():
    res = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "(x+1)(x+2)",
            "solution_text": "(x+1)(x+2)\nx^2+3x+2",
            "problem_type": "expression",
            "target_form": "expanded",
            "problem_skill": "distribution",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["correct"] is True
    assert data["analysis_status"] == "ANALYZED"


def test_legacy_analyze_solution_rejects_inequality_with_retryable_false():
    res = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "x^2-3x+2<0",
            "solution_text": "x<1",
            "problem_skill": "distribution",
        },
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_UNSUPPORTED_INEQUALITY"
    assert detail["retryable"] is False


# ===========================================================================
# 파이프라인 견고성 — 연쇄 오류로 모든 ErrorRecord 가 억제된 경우
# ===========================================================================

def test_analysis_response_survives_when_all_errors_are_suppressed(monkeypatch):
    """관측이 0건이어도 응답 조립이 UnboundLocalError 로 깨지지 않는다.

    연쇄 오류 판정이 모든 후보를 suppressed 로 돌리면 errors/observations 가
    비는데, 그때도 correct=false 와 첫 오류 줄은 응답에 남아야 한다.
    """
    from app.pipeline import session as session_module

    monkeypatch.setattr(
        session_module, "_classify_error_steps", lambda *args, **kwargs: ([], [])
    )
    problem = Problem(problem_id="P1", problem_latex="2x=6", skills=["distribution"])
    attempt = StudentAttempt(
        attempt_id="A1",
        student_id="S1",
        problem_id="P1",
        solution_text="2x=7",
    )
    analysis = analyze_attempt(problem, attempt)

    assert analysis.correct is False
    assert analysis.errors == []
    assert analysis.observations == []
    assert analysis.api_response["error_step"] == 1
    assert analysis.api_response["analysis_status"] == "ANALYZED"


# ===========================================================================
# attempt 흐름 세부 — 저장된 오류/판정 보류 스냅샷
# ===========================================================================

def test_attempt_form_violation_is_stored_as_procedure_error():
    """형태 위반은 모든 줄이 동치(valid=true)여도 ErrorRecord 1건으로 남는다."""
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2", target_form="factored")
    assert res.status_code == 200
    data = res.json()

    assert data["valid"] == [True, True]
    assert data["correct"] is False
    assert len(data["errors"]) == 1
    error = data["errors"][0]
    assert error["step_no"] == 2
    assert error["ref_step_no"] == 1
    assert error["error_type"] == "procedure"
    assert error["error_subtype"] == "method_mismatch"
    assert error["misconception_ids"] == []
    assert error["evidence_latex"] == "x^2+3x+2"
    assert error["description_ko"]  # 한국어 설명이 비지 않는다
    # 형태 위반은 '관측된 오답'이므로 Knowledge State 는 갱신된다.
    assert data["observations"]
    assert data["states"]


def test_attempt_review_snapshot_is_stored_without_state_update():
    """REVIEW_REQUIRED 는 저장되지만 KnowledgeObservation/State 는 남기지 않는다."""
    res = post("A1", "(x+1)(x+2)\nx^2+3x+2=0")
    assert res.status_code == 200
    analysis_id = res.json()["analysis_id"]

    detail = client.get(
        f"/api/v1/attempts/A1/analyses/{quote(analysis_id, safe='')}"
    )
    assert detail.status_code == 200
    body_json = detail.json()
    assert body_json["analysis_status"] == "REVIEW_REQUIRED"
    assert body_json["correct"] is None
    assert body_json["raw_result"]["correct"] is None
    assert body_json["raw_result"]["analysis_status"] == "REVIEW_REQUIRED"
    assert body_json["errors"] == []
    assert body_json["observations"] == []
    assert body_json["states"] == {}


def test_same_expression_attempt_is_idempotent():
    """같은 본문 재전송은 409 가 아니라 기존 분석을 그대로 돌려준다."""
    payload = body("(x+1)(x+2)\nx^2+3x+2", target_form="expanded")
    first = client.post(URL.format(attempt_id="A1"), json=payload)
    second = client.post(URL.format(attempt_id="A1"), json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["analysis_id"] == second.json()["analysis_id"]
    assert first.json()["analysis_count"] == second.json()["analysis_count"] == 1


def test_unknown_problem_type_is_rejected_by_analyzer():
    with pytest.raises(AnalysisError) as exc:
        analyze(
            problem_latex="(x+1)(x+2)",
            solution_text="x^2+3x+2",
            problem_type="function",
        )
    assert exc.value.code == "E_INVALID_PROBLEM_TYPE"


def test_unknown_problem_type_is_rejected_by_api_validation():
    """스키마 Literal 이 먼저 막는다 (분석/저장 이전 단계)."""
    res = post("A1", "x^2+3x+2", problem_type="function")
    assert res.status_code == 422
    assert get_repository().has_analysis("A1") is False


def test_legacy_analyze_solution_returns_null_correct_on_review():
    """REVIEW_REQUIRED 는 레거시 단일 분석 API 에서도 correct=null 로 내려간다."""
    res = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "(x+1)(x+2)",
            "solution_text": "(x+1)(x+2)\nx^2+3x+2=0",
            "problem_type": "expression",
            "problem_skill": "distribution",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "REVIEW_REQUIRED"
    assert data["correct"] is None
    assert data["error_step"] is None
    assert data["misconception_ids"] == []


def test_inequality_line_in_solution_is_review_not_rejection():
    """부등호는 **문제식**에서만 422 로 거절한다.

    학생 풀이 줄에 부등호가 섞이면 파싱 불가 줄로 남겨 판정 보류로 처리한다.
    학생 입력 실수까지 422 로 막으면 정상 재시도가 불가능해진다.
    """
    res = post("A1", "(x+1)(x+2)\nx<1")
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_status"] == "REVIEW_REQUIRED"
    assert data["unrecognized"] == [2]
    assert data["valid"] == [True, None]


def test_inequality_only_solution_is_unrecognized_error():
    """판정 가능한 줄이 하나도 없으면 422 E_UNRECOGNIZED (UNKNOWN)."""
    res = post("A1", "x<1")
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "E_UNRECOGNIZED"
    assert detail["analysis_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "problem_latex, problem_type, target_form",
    [
        ("x^2-3x+2<0", "equation", None),
        ("x^2-3x+2<0", "expression", "expanded"),
        ("2x \\le 4", "equation", "expanded"),
        ("(x+1)(x+2)", "equation", "expanded"),
        ("(x+1)(x+2)", "expression", "simplified"),
        ("(x+1)(x+2)", "function", None),
        ("f(x)=2x+1", "equation", None),
        ("f(x)=2x+1", "expression", "expanded"),
    ],
)
def test_error_precedence_is_identical_in_both_entry_points(
    problem_latex, problem_type, target_form
):
    """analyze() 와 analyze_attempt() 가 같은 입력에 같은 코드를 낸다.

    경로마다 검증 순서가 달라지면 Backend 가 받는 코드가 엔드포인트에 따라
    달라진다. 우선순위(부등식 → 계약 → 파싱)를 두 경로에서 동일하게 고정한다.
    """
    problem = Problem(problem_id="P1", problem_latex=problem_latex, skills=["distribution"])
    # StudentAttempt 스키마도 Literal 이므로, 계약 밖 값은 attempt 에는 담지 않고
    # analyze_attempt() 인자로만 전달해 검증 경로를 비교한다.
    attempt_problem_type = (
        problem_type if problem_type in ("equation", "expression") else "equation"
    )
    attempt = StudentAttempt(
        attempt_id="A1",
        student_id="S1",
        problem_id="P1",
        solution_text="x+1",
        problem_type=attempt_problem_type,
        target_form=target_form,
    )

    with pytest.raises(AnalysisError) as direct:
        analyze(
            problem_latex=problem_latex,
            solution_text="x+1",
            problem_skill="distribution",
            problem_type=problem_type,
            target_form=target_form,
        )
    with pytest.raises(AnalysisError) as flow:
        analyze_attempt(
            problem, attempt, problem_type=problem_type, target_form=target_form
        )
    assert direct.value.code == flow.value.code
