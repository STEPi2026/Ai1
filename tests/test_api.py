"""API 계약 테스트 — 최소 JSON 계약(Step 4) 검증."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import reset_knowledge

client = TestClient(app)


def setup_function():
    reset_knowledge()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_contract_minimal_fields():
    r = client.post(
        "/analyze-solution",
        json={
            "problem_id": "P01",
            "problem_latex": "2(x-3)=6",
            "solution_text": "2(x-3)=6\n2x-3=6",
            "student_id": "api-test-1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # 최소 계약 필드
    for key in ("correct", "error_step", "error_type", "skill", "state"):
        assert key in body
    assert body["correct"] is False
    assert body["error_step"] == 2
    assert body["error_type"] == "concept_error"
    assert body["skill"] == "distribution"
    assert body["state"] == "needs_practice"
    # 확장 필드
    assert body["misconception_id"] == "2.1"
    assert body["valid"][1] is False
    assert body["unrecognized"] == []


def test_analyze_correct_solution():
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "3x+5=20",
            "solution_text": "3x+5=20 → 3x=15 → x=5",
            "student_id": "api-test-2",
        },
    )
    body = r.json()
    assert body["correct"] is True
    assert body["error_step"] is None
    assert body["error_type"] is None
    assert body["state"] in ("mastered", "learning")


def test_analyze_bkt_engine():
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "5x=35",
            "solution_text": "5x=35\nx=7",
            "student_id": "api-test-3",
            "engine": "bkt",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is True
    assert 0.0 < body["mastery"] <= 1.0


def test_empty_solution_422():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_EMPTY_SOLUTION"


def test_unrecognized_line_not_error():
    """인식 실패 줄은 오답 처리하지 않는다 (ADR-03)."""
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "2x=6",
            "solution_text": "2x=6\n???인식불가???",
            "student_id": "api-test-4",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is True
    assert body["unrecognized"] == [2]


def test_bad_problem_422():
    """문제식 파싱 불가 시 correct를 단정하지 않는다 (검토 시 발견한 버그 고정)."""
    for bad in ("", "???", "보통 문제"):
        r = client.post(
            "/analyze-solution",
            json={"problem_latex": bad, "solution_text": "x=1"},
        )
        assert r.status_code == 422, bad
        assert r.json()["detail"]["code"] == "E_BAD_PROBLEM"


def test_unverifiable_solution_422():
    """어떤 줄도 동치 판정에 실패하면 correct를 단정하지 않는다.

    복잡도 가드(고차 방정식)에 걸려 모든 판정이 None인 경우가 대표적.
    """
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "x^99=1", "solution_text": "x=1"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNVERIFIABLE"


@pytest.mark.parametrize(
    "solution_text",
    [
        "보통 이렇게 품",  # 1단계
        "보통 풀이\n이렇게 씀",  # 여러 단계 모두 인식 실패
    ],
    ids=["single_line", "multi_line"],
)
def test_fully_unrecognized_solution_is_rejected(solution_text):
    """전량 인식실패는 UNVERIFIABLE이 아니라 UNRECOGNIZED (S16: 2개 중복 테스트 병합).

    기존 test_all_unrecognized_422(1줄)와
    test_all_lines_unrecognized_is_recognized_error(2줄) 은 단언이 같고
    입력 형태만 달랐으므로 하나로 합쳤다. 시나리오 수는 동일하다.
    """
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": solution_text},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNRECOGNIZED"


def test_error_after_unrecognized_line_detected():
    """인식 실패 뒤의 오류도 누락 없이 포착한다 (검토 시 발견한 버그 고정)."""
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\n???\nx=5", "student_id": "api-gap"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is False
    assert body["error_step"] == 3  # 인식실패(2줄) 건너뛰고 3줄에서 오류 검출
    assert body["unrecognized"] == [2]


def test_gap_bridging_keeps_correct_answer():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\n???\nx=3", "student_id": "api-gap2"},
    )
    body = r.json()
    assert body["correct"] is True
    assert body["unrecognized"] == [2]  # 다만 판정 불가 줄은 안내


def test_expression_line_reported_as_unrecognized():
    """식 단독 줄(= 없는)은 판정 불가 → unrecognized로 안내한다."""
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x-6\nx=3", "student_id": "api-expr"},
    )
    body = r.json()
    assert body["correct"] is True
    assert body["unrecognized"] == [1]
    assert body["valid"] == [None, True]  # 2줄은 문제식과 대조해 유효


def test_expression_problem_rejected():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x-6", "solution_text": "x=3"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_BAD_PROBLEM"
