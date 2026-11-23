"""PHASE — final_answer_present 응답 필드 (additive, 응답 전용).

계약(AI②와 합의):
  - true  : 마지막 단계가 문제가 요구한 최종 답을 모두 포함
  - false : 최종 답이 아니라고 확정(중간식·미완성·일부 해만 제시·항등/모순)
  - null  : 판단 불가(대상 변수 불명·target_form 없음·인식 불확실·
            REVIEW_REQUIRED/UNKNOWN). 기본값.
  - correct 와 독립이다. 최종 답을 냈지만 틀렸으면 true + correct=false.

판정은 verifier 가 단계 비교에 이미 쓴 _SolveBudget 을 그대로 재사용한다 —
밖에서 새 solve() 를 호출하면 S2 예산 집계가 깨진다.

모든 테스트는 local(fake 분석)이며 네트워크·Vision 호출은 없다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import reset_knowledge
from app.pipeline.repository import reset_repository
from app.pipeline.verifier import verify
from app.schemas import AnalyzeAttemptResponse

client = TestClient(app)
URL = "/api/v1/attempts/{attempt_id}/analyze"

SOLUTION = {
    "solution_id": "SOL-1",
    "steps": [{"latex_text": "2(x-3)=6"}, {"latex_text": "2x-6=6"}],
    "answer_latex": "2x=6",
}


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
        "problem_latex": "2x=6",
        "problem_skills": ["linear_equation"],
        "student_id": "S1",
        "solution_text": text,
    }
    payload.update(kw)
    return payload


def post(attempt_id: str, text: str, **kw):
    return client.post(URL.format(attempt_id=attempt_id), json=body(text, **kw))


# ===========================================================================
# equation 모드
# ===========================================================================

def test_complete_solution_last_line_is_value_statement():
    """1. correct=true + 마지막 줄 x=3(완결) → true."""
    r = post("F1", "2x=6\nx=3")
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is True
    assert b["final_answer_present"] is True


def test_unsolved_last_line_is_false_even_when_correct():
    """2. correct=true + 마지막 줄 2x=6(미정리) → false.

    이 케이스가 필드의 존재 이유다 — '오류 없음'과 '최종 답 제시'는 다르다.
    """
    r = post("F2", "2x=6")
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is True
    assert b["final_answer_present"] is False


def test_wrong_value_still_presents_final_answer():
    """3. 틀린 최종 답 x=999 → final_answer_present=true, correct=false."""
    r = post("F3", "2x=6\nx=999")
    assert r.status_code == 200
    b = r.json()
    assert b["correct"] is False
    assert b["final_answer_present"] is True


def test_quadratic_with_one_of_two_solutions_is_false():
    """4. 이차방정식 해 2개 중 1개만 제시(x=3만) → false (개수 판정)."""
    r = client.post(
        URL.format(attempt_id="F4"),
        json=body(
            "x^2=9\nx=3",
            problem_latex="x^2=9",
            problem_skills=["quadratic_equation"],
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    # x^2=9 의 해는 {-3, 3} — x=3 하나만으로는 동치가 깨진다.
    assert b["correct"] is False
    # 값의 정오와 무관하게 '제시 개수(1) != 요구 개수(2)'로 false.
    assert b["final_answer_present"] is False


def test_unrecognized_last_line_is_null():
    """5. 마지막 줄 unrecognized → null (판단 불가)."""
    r = post("F5", "2x=6\n????")
    assert r.status_code == 200
    b = r.json()
    assert b["final_answer_present"] is None


def test_review_required_is_null():
    """6. analysis_status=REVIEW_REQUIRED → null."""
    r = post("F6", "2x=6\n???\nx=3")
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["final_answer_present"] is None


def test_multivariable_problem_is_null():
    """7. 다변수 문제(자유변수 2개) → null."""
    r = client.post(
        URL.format(attempt_id="F7"),
        json=body("x+y=6\nx+y=6", problem_latex="x+y=6"),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["final_answer_present"] is None


# ===========================================================================
# expression 모드
# ===========================================================================

def test_expression_without_target_form_is_null():
    """8. expression + target_form=None → null."""
    r = client.post(
        URL.format(attempt_id="F8"),
        json=body(
            "(x+1)(x+2)\nx^2+3x+2",
            problem_latex="(x+1)(x+2)",
            problem_skills=["distribution"],
            problem_type="expression",
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["final_answer_present"] is None


def test_expression_form_mismatch_is_false():
    """9. expression + target_form='factored' 에 전개식 → false."""
    r = client.post(
        URL.format(attempt_id="F9"),
        json=body(
            "x^2+3x+2",
            problem_latex="(x+1)(x+2)",
            problem_skills=["distribution"],
            problem_type="expression",
            target_form="factored",
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["final_answer_present"] is False


def test_expression_form_match_is_true():
    """target_form='factored' 에 인수분해형 → true (보조 케이스)."""
    r = client.post(
        URL.format(attempt_id="F10"),
        json=body(
            "(x+1)(x+2)",
            problem_latex="x^2+3x+2",
            problem_skills=["factoring_common"],
            problem_type="expression",
            target_form="factored",
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["final_answer_present"] is True


# ===========================================================================
# 직렬화 / UNKNOWN 경로 / verifier 단위
# ===========================================================================

def test_field_is_serialized_in_response_schema():
    """10. POST 응답에 필드가 실제로 직렬화된다 (스키마 등록 확인)."""
    assert "final_answer_present" in AnalyzeAttemptResponse.model_fields
    r = post("F11", "2x=6\nx=3")
    assert r.status_code == 200
    assert "final_answer_present" in r.json()
    assert r.json()["final_answer_present"] is True
    # 멱등 재요청도 같은 값을 반환한다
    r2 = post("F11", "2x=6\nx=3")
    assert r2.json()["final_answer_present"] is True


def test_unknown_422_has_no_field():
    """11. UNKNOWN(422) 경로에는 필드가 아예 없다."""
    r = post("F12", "보통은 이렇게 푼다고 합니다")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["analysis_status"] == "UNKNOWN"
    assert "final_answer_present" not in detail
    assert "final_answer_present" not in r.text


def test_verifier_level_identity_and_contradiction_last_line():
    """항등식(3=3)·모순(0=5)·x=x 로 끝나면 False — verifier 단위."""
    v = verify("2x=6", ["2x=6", "3=3"])
    assert v.final_answer_present is False
    v2 = verify("2x=6", ["2x=6", "0=5"])
    assert v2.final_answer_present is False
    v3 = verify("2x=6", ["2x=6", "x=x"])
    assert v3.final_answer_present is False


def test_verifier_level_null_cases():
    """문제식 미인식·비인식 마지막 줄 — verifier 단위 None."""
    # 문제식이 등식이 아니면 대상 변수 불명
    v = verify("3", ["3", "3"])
    assert v.final_answer_present is None
    # 마지막 줄이 인식되지 않으면 단정하지 않는다
    v2 = verify("2x=6", ["2x=6", "????"])
    assert v2.final_answer_present is None
    # 빈 단계 목록
    v3 = verify("2x=6", [])
    assert v3.final_answer_present is None


def test_verifier_level_solved_form_variants():
    """x=꼴 변형 — 좌우 반전 상수, x^2=9 마지막 줄은 False."""
    v = verify("x=3", ["x=3", "3=x"])
    # 3=x 는 해 진술이지만 '변수 = 값' 계약은 변수가 한쪽에만 있으면 수용한다
    assert v.final_answer_present is True
    v2 = verify("x^2=9", ["x^2=9", "x^2=9"])
    assert v2.final_answer_present is False  # x^2=9 는 아직 x=꼴이 아니다


# ===========================================================================
# 다중 줄 해 표기 — 마지막 '답 블록'의 서로 다른 값 개수로 판정
# ===========================================================================

def test_multiline_solution_block_is_true():
    """해를 두 줄로 나눠 쓴 완전한 답안(x=3 / x=-3) → true.

    correct=false(단계 동치 판정의 기존 한계)와 독립적으로,
    '최종 답을 모두 제시했는가'는 true 여야 한다.
    """
    r = client.post(
        URL.format(attempt_id="F13"),
        json=body(
            "x^2=9\nx=3\nx=-3",
            problem_latex="x^2=9",
            problem_skills=["quadratic_equation"],
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["final_answer_present"] is True


def test_multiline_partial_block_is_false():
    """답 블록에 같은 해만 반복(x=3 / x=3) → false (서로 다른 값 1개 < 2개)."""
    r = client.post(
        URL.format(attempt_id="F14"),
        json=body(
            "x^2=9\nx=3\nx=3",
            problem_latex="x^2=9",
            problem_skills=["quadratic_equation"],
        ),
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["final_answer_present"] is False


def test_verifier_level_multiline_block_variants():
    """좌우 반전(3=x)이 섞여도 같은 답 블록으로 센다."""
    v = verify("x^2=9", ["x^2=9", "x=3", "3=x"])
    assert v.final_answer_present is False  # 3, 3 → 서로 다른 값 1개
    v2 = verify("x^2=9", ["x^2=9", "x=3", "-3=x"])
    assert v2.final_answer_present is True  # 3, -3 → 2개
