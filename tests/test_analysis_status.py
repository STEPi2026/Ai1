"""Phase 8-2 회귀 — analysis_status (앱 r49).

상태 정책:
  ANALYZED       : 분석 가능한 모든 단계가 판정됨 (correct 와는 별개)
  REVIEW_REQUIRED: 일부만 판정되고 unrecognized/판정 보류가 남아 사람 확인 필요
  UNKNOWN        : 신뢰 가능한 단계 판정이 하나도 없음 (기존 E_UNRECOGNIZED/E_UNVERIFIABLE)

원칙:
  - additive 필드로만 추가, 기존 필드·의미 불변
  - correct 및 Knowledge State 전이 불변
  - 기존 에러 코드/HTTP 상태와 detail.code/detail.message 계약 유지
  - 요청 형식 오류·입력 초과를 UNKNOWN 으로 섞지 않음
  - verifier/parser 결과만으로 결정, 원인·정답을 추측하지 않음
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import (
    _ANALYSIS_STATUS_BY_ERROR,
    analyze,
    analysis_status_of,
    get_engine,
    reset_knowledge,
)
from app.pipeline.session import Problem, StudentAttempt, analyze_attempt
from app.schemas import AnalyzeSolutionResponse

client = TestClient(app)

VALID = ("ANALYZED", "REVIEW_REQUIRED", "UNKNOWN")


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


def post(**kw):
    return client.post("/analyze-solution", json=kw)


# ===========================================================================
# 1. 정상 풀이와 검증된 오답 = ANALYZED
# ===========================================================================

def test_correct_solution_is_analyzed():
    r = post(problem_latex="3x+5=20", solution_text="3x+5=20\n3x=15\nx=5", student_id="s1")
    b = r.json()
    assert r.status_code == 200
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is True
    assert b["unrecognized"] == []
    assert b["valid"] == [True, True, True]


def test_verified_error_is_analyzed():
    r = post(problem_latex="2(x-3)=6", solution_text="2(x-3)=6\n2x-3=6", student_id="s2")
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is False
    assert b["valid"] == [True, False]


def test_answer_only_still_analyzed():
    """S7 정책(과정 없는 정답 제출)은 판정이 끝났으므로 ANALYZED 다."""
    r = post(problem_latex="2x=6", solution_text="x=3", student_id="s3")
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is True


# ===========================================================================
# 2. 일부 인식 실패 / 판정 보류 = REVIEW_REQUIRED
# ===========================================================================

def test_unrecognized_line_is_review_required():
    r = post(problem_latex="2x=6", solution_text="2x=6\n???인식불가???", student_id="s4")
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["unrecognized"] == [2]
    assert b["valid"] == [True, None]


def test_expression_only_line_is_review_required():
    r = post(problem_latex="2x=6", solution_text="2x-6\nx=3", student_id="s5")
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["unrecognized"] == [1]
    assert b["valid"] == [None, True]


def test_error_after_unrecognized_is_review_required():
    r = post(problem_latex="2x=6", solution_text="2x=6\n???\nx=5", student_id="s6")
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["correct"] is False  # 오류는 여전히 검출된다
    assert b["error_step"] == 3
    assert b["unrecognized"] == [2]


def test_single_line_unrecognized_first_step():
    r = post(problem_latex="2x=6", solution_text="???\nx=3", student_id="s7")
    assert r.json()["analysis_status"] == "REVIEW_REQUIRED"


# ===========================================================================
# 3. UNKNOWN — 기존 에러 코드/HTTP 상태 유지
# ===========================================================================

def test_fully_unrecognized_is_unknown_422():
    r = post(problem_latex="2x=6", solution_text="보통 풀이", student_id="s8")
    d = r.json()["detail"]
    assert r.status_code == 422
    assert d["code"] == "E_UNRECOGNIZED"
    assert d["analysis_status"] == "UNKNOWN"
    assert d["message"], "기존 detail.message 계약이 보존되어야 한다"


def test_unverifiable_is_unknown_422():
    r = post(problem_latex="x^99=1", solution_text="x=1", student_id="s9")
    d = r.json()["detail"]
    assert r.status_code == 422
    assert d["code"] == "E_UNVERIFIABLE"
    assert d["analysis_status"] == "UNKNOWN"
    assert d["message"]


def test_error_detail_keeps_code_and_message_contract():
    """기존 detail 은 code/message 키를 그대로 갖는다 (추가만)."""
    r = post(problem_latex="2x=6", solution_text="보통 풀이", student_id="s10")
    d = r.json()["detail"]
    assert isinstance(d, dict)
    assert set(d) >= {"code", "message"}


def test_unknown_mapping_is_limited_to_analysis_outcomes():
    """요청/입력 오류는 UNKNOWN 으로 섞지 않는다."""
    assert _ANALYSIS_STATUS_BY_ERROR == {
        "E_UNRECOGNIZED": "UNKNOWN",
        "E_UNVERIFIABLE": "UNKNOWN",
    }
    for code in (
        "E_EMPTY_SOLUTION", "E_BAD_PROBLEM", "E_INPUT_TOO_LARGE",
        "E_UNKNOWN_SKILL", "E_SKILL_NOT_TRACKABLE", "E_INVALID_STUDENT_ID",
    ):
        assert code not in _ANALYSIS_STATUS_BY_ERROR


@pytest.mark.parametrize(
    "payload,http",
    [
        (dict(problem_latex="2x=6", solution_text="", student_id="e1"), 422),
        (dict(problem_latex="", solution_text="x=1", student_id="e2"), 422),
        (dict(problem_latex="2x=6", solution_text="x=3", problem_skill="factoring", student_id="e3"), 422),
        (dict(problem_latex="2x=6", solution_text="x=3", student_id=""), 422),
        (dict(problem_latex="2x=6", solution_text="x=3", bogus=1, student_id="e5"), 422),
    ],
)
def test_request_errors_have_no_analysis_status(payload, http):
    r = post(**payload)
    assert r.status_code == http
    d = r.json()["detail"]
    if isinstance(d, dict):
        assert "analysis_status" not in d, f"{d.get('code')} 에 UNKNOWN 이 섞였다"


def test_input_too_large_413_without_status():
    r = post(problem_latex="x+" * 3000 + "=0", solution_text="x=0", student_id="e6")
    assert r.status_code == 413
    assert "analysis_status" not in r.json()["detail"]


# ===========================================================================
# 4. 부분 검증에서 correct / Knowledge State 의미 불변
# ===========================================================================

def test_correct_unchanged_under_review_required():
    reset_knowledge()
    out = analyze("2x=6", "2x=6\n???인식불가???", student_id="s11")
    assert out["analysis_status"] == "REVIEW_REQUIRED"
    assert out["correct"] is True, "인식 실패는 오답이 아니다 (ADR-03)"
    assert ("s11", "linear_equation") in get_engine("rule")._state


def test_error_still_detected_under_review_required():
    reset_knowledge()
    out = analyze("2x=6", "2x=6\n???\nx=5", student_id="s12")
    assert out["analysis_status"] == "REVIEW_REQUIRED"
    assert out["correct"] is False
    assert out["error_step"] == 3
    assert ("s12", "arithmetic") in get_engine("rule")._state


def test_knowledge_state_transition_unchanged():
    """status 추가가 state/mastery 를 바꾸지 않는다."""
    reset_knowledge()
    a = analyze("2x=6", "2x=6\n???\nx=5", student_id="s13")
    assert a["analysis_status"] == "REVIEW_REQUIRED"
    assert a["state"] == "learning"  # calculation 오류 → learning
    assert a["mastery"] == 0.6

    # REVIEW_REQUIRED 여도 개념 오류는 needs_practice 로 전이된다
    reset_knowledge()
    b = analyze("2(x-3)=6", "2(x-3)=6\n???\n2x-3=6", student_id="s14")
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["error_type"] == "concept_error"
    assert b["state"] == "needs_practice"


def test_status_does_not_depend_on_correct_value():
    reset_knowledge()
    ok = analyze("2x=6", "2x=6\nx=3", student_id="s15")
    bad = analyze("2x=6", "2x=6\nx=4", student_id="s15")
    assert ok["analysis_status"] == bad["analysis_status"] == "ANALYZED"
    assert ok["correct"] is True and bad["correct"] is False


# ===========================================================================
# 5. 기존 응답 필드 / API 하위호환
# ===========================================================================

def test_all_pre_existing_fields_unchanged():
    r = post(problem_latex="2(x-3)=6", solution_text="2(x-3)=6\n2x-3=6", student_id="s16")
    b = r.json()
    for key in (
        "correct", "error_step", "error_type", "skill", "state", "mastery",
        "misconception_id", "confidence", "steps_latex", "valid", "unrecognized",
    ):
        assert key in b, key
    assert b["correct"] is False
    assert b["error_step"] == 2
    assert b["error_type"] == "concept_error"
    assert b["skill"] == "distribution"
    assert b["misconception_id"] == "2.1"


def test_response_field_count():
    assert len(AnalyzeSolutionResponse.model_fields) == 16


def test_legacy_payload_without_status_still_validates():
    """구버전 payload(신규 필드 없음)도 그대로 통과한다 (하위호환)."""
    legacy = {
        "correct": True, "error_step": None, "error_type": None, "skill": "linear_equation",
        "state": "learning", "mastery": 0.6, "misconception_id": None, "confidence": None,
        "steps_latex": ["2x=6"], "valid": [True], "unrecognized": [],
    }
    parsed = AnalyzeSolutionResponse(**legacy)
    assert parsed.correct is True
    assert parsed.analysis_status is None


def test_status_is_optional_and_validated():
    fields = AnalyzeSolutionResponse.model_fields
    assert not fields["analysis_status"].is_required()
    with pytest.raises(Exception):
        AnalyzeSolutionResponse(
            correct=True, error_step=None, error_type=None, skill="x", state="learning",
            mastery=0.6, misconception_id=None, confidence=None, steps_latex=[], valid=[],
            unrecognized=[], analysis_status="NOT_A_STATUS",
        )


def test_status_value_domain():
    reset_knowledge()
    out = analyze("2x=6", "2x=6\nx=3", student_id="s17")
    assert out["analysis_status"] in VALID


# ===========================================================================
# 6. 판정 로직 순수성 / 라이브러리 경로
# ===========================================================================

def test_analysis_status_of_is_pure():
    from app.pipeline.verifier import verify

    v = verify("2x=6", ["2x=6", "x=3"])
    assert analysis_status_of(v) == "ANALYZED"
    v2 = verify("2x=6", ["2x=6", "???"])
    assert analysis_status_of(v2) == "REVIEW_REQUIRED"
    v3 = verify("2x=6", ["2x-6"])
    assert analysis_status_of(v3) == "REVIEW_REQUIRED"


def test_session_path_api_response_also_has_status():
    problem = Problem(
        problem_id="P-9", problem_latex="2x=6", skills=["linear_equation"]
    )
    a = StudentAttempt(
        attempt_id="A-9", student_id="S9", problem_id="P-9", solution_text="2x=6\nx=3"
    )
    r = analyze_attempt(problem, a)
    assert r.api_response["analysis_status"] == "ANALYZED"
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)
    AnalyzeSolutionResponse(**r.api_response)


def test_session_path_review_required():
    problem = Problem(
        problem_id="P-9", problem_latex="2x=6", skills=["linear_equation"]
    )
    a = StudentAttempt(
        attempt_id="A-9b", student_id="S9", problem_id="P-9", solution_text="2x=6\n???"
    )
    r = analyze_attempt(problem, a)
    assert r.api_response["analysis_status"] == "REVIEW_REQUIRED"
    assert r.api_response["correct"] is True


def test_analyze_signature_unchanged():
    """analyze() 인자·반환 계약은 그대로다 (키워드 추가 없음)."""
    import inspect

    params = list(inspect.signature(analyze).parameters)
    assert params == [
        "problem_latex", "solution_text", "student_id", "engine",
        "problem_skill", "segmenter",
    ]
