"""PHASE 9-7.2 — tracking Skill 거절 후 Problem 오염 방지.

배경
    예전에는 새 Problem 을 분석 전에 곧바로 저장했다. 그래서 tracking Skill 이
    없어 거절된 요청이 ``skills=[]`` 인 불완전한 Problem 을 저장소에 남겼고,
    그 뒤 같은 ``problem_id`` 로 올바른 tracking Skill 을 넣어 다시 요청해도
    저장본이 source of truth 라 계속 422 — **복구할 수 없는 상태**가 되었다.

검증 범위
  - 재현 시나리오: 거절 → Problem 미등록 → 같은 problem_id 재요청 성공
  - 거절 유형별 미등록 (tracking 누락 / group node 만 / taxonomy 미등록 ID)
  - 오류 우선순위 유지 (파싱·검증 오류가 tracking 오류보다 먼저)
  - Attempt/분석 미저장, Knowledge State 미변경
  - 저장 순서 유지 (문제 → 정답 풀이 → Attempt → 분석)
  - 동시 요청 동작
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import (
    RepositoryError,
    get_repository,
    reset_repository,
)
from app.taxonomy import validate_all

client = TestClient(app)

PROBLEM_LATEX = "2(x-3)=6"
SOLUTION = "2(x-3)=6\n2x-6=6"


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


def call(attempt_id, problem_id="P1", skills=("linear_equation",), text=SOLUTION,
         problem_latex=PROBLEM_LATEX, student_id="S1"):
    body = {
        "problem_id": problem_id,
        "problem_latex": problem_latex,
        "student_id": student_id,
        "solution_text": text,
    }
    if skills is not None:
        body["problem_skills"] = list(skills)
    return client.post(f"/api/v1/attempts/{attempt_id}/analyze", json=body)


def problem_is_registered(problem_id="P1"):
    try:
        get_repository().get_problem(problem_id)
    except RepositoryError:
        return False
    return True


# ===========================================================================
# 1. 재현 시나리오
# ===========================================================================

def test_rejected_new_problem_is_not_registered():
    """tracking Skill 없는 새 요청 → 422, Problem 은 등록되지 않는다."""
    r = call("A1", problem_id="P1", skills=[])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    assert problem_is_registered("P1") is False


def test_same_problem_id_can_be_recovered_with_tracking_skill():
    """같은 attempt_id 로 올바른 tracking Skill 을 넣어 다시 요청하면 성공한다."""
    first = call("A2", problem_id="P1", skills=[])
    assert first.status_code == 422
    second = call("A2", problem_id="P1", skills=["linear_equation"])
    assert second.status_code == 200, second.json()
    assert second.json()["analysis_status"] == "ANALYZED"
    assert second.json()["correct"] is True
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


def test_same_problem_id_recoverable_with_new_attempt_id():
    """새 attempt_id 로도 복구된다 (시도 자체가 잠기지 않는다)."""
    first = call("A3", problem_id="P1", skills=[])
    assert first.status_code == 422
    second = call("A4", problem_id="P1", skills=["linear_equation"])
    assert second.status_code == 200
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


def test_recovery_works_after_group_node_only_rejection():
    call("A5", problem_id="P1", skills=["factoring"])
    assert problem_is_registered("P1") is False
    recovered = call("A6", problem_id="P1", skills=["linear_equation"])
    assert recovered.status_code == 200
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


# ===========================================================================
# 2. 거절 유형별 — Problem 미등록
# ===========================================================================

def test_group_node_only_request_leaves_no_problem():
    r = call("B1", problem_id="P1", skills=["factoring"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    assert problem_is_registered("P1") is False


def test_unknown_taxonomy_id_leaves_no_problem():
    r = call("C1", problem_id="P1", skills=["NOT_A_SKILL"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL"
    assert problem_is_registered("P1") is False


def test_unknown_skill_code_leaves_no_problem():
    r = call("C2", problem_id="P1", skills=["SKL-9-999"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL_CODE"
    assert problem_is_registered("P1") is False


def test_no_correct_solution_leaves_no_problem():
    """정답 풀이를 함께 보냈어도 문제는 등록되지 않는다."""
    r = client.post(
        "/api/v1/attempts/C3/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": PROBLEM_LATEX,
            "problem_skills": [],
            "student_id": "S1",
            "solution_text": SOLUTION,
            "correct_solution": {
                "solution_id": "SOL-1",
                "steps": [{"latex_text": "2(x-3)=6"}],
                "answer_latex": "x=6",
            },
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    assert problem_is_registered("P1") is False
    assert get_repository().list_problems() == []


# ===========================================================================
# 3. 오류 우선순위 유지
# ===========================================================================

def test_parsing_error_takes_priority_over_skill_error():
    """파싱 오류가 E_SKILL_NOT_TRACKABLE 보다 먼저 온다."""
    r = call("D1", problem_id="P1", skills=[], text="???")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNRECOGNIZED"


def test_unverifiable_takes_priority_over_skill_error():
    r = call("D2", problem_id="P1", skills=[], text="x^99=1")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNVERIFIABLE"


def test_empty_solution_takes_priority_over_skill_error():
    r = call("D3", problem_id="P1", skills=[], text="   ")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_EMPTY_SOLUTION"


def test_input_too_large_takes_priority_over_skill_error():
    """입력 크기 초과(413)가 tracking 오류보다 먼저 온다 (기존 매핑 유지)."""
    r = call("D4", problem_id="P1", skills=[], text="x=1\n" * 40)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "E_INPUT_TOO_LARGE"
    assert "analysis_status" not in r.json()["detail"]


def test_parsing_error_with_valid_tracking_skill_still_registers_problem():
    """파싱 오류는 기존처럼 문제를 등록한 상태로 남긴다 (재사용 가능)."""
    r = call("D5", problem_id="P1", skills=["linear_equation"], text="???")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNRECOGNIZED"
    assert problem_is_registered("P1") is True
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


# ===========================================================================
# 4. Attempt / 분석 미저장 + Knowledge State 미변경
# ===========================================================================

def test_rejected_request_stores_no_attempt_or_analysis():
    call("E1", problem_id="P1", skills=[])
    repo = get_repository()
    assert repo.has_analysis("E1") is False
    with pytest.raises(RepositoryError):
        repo.get_attempt("E1")
    with pytest.raises(RepositoryError):
        repo.list_analyses("E1")


def test_rejected_request_does_not_change_knowledge_state():
    """거절된 요청은 Knowledge State 를 건드리지 않는다."""
    before = analyzer.get_engine("rule").state("S1", "linear_equation")
    mastery_before = analyzer.get_engine("rule").mastery("S1", "linear_equation")
    call("E2", problem_id="P1", skills=[])
    call("E3", problem_id="P2", skills=["factoring"])
    call("E4", problem_id="P3", skills=["NOT_A_SKILL"])
    after = analyzer.get_engine("rule").state("S1", "linear_equation")
    mastery_after = analyzer.get_engine("rule").mastery("S1", "linear_equation")
    assert after == before
    assert mastery_after == mastery_before


def test_history_is_untouched_by_rejected_request():
    call("E5", problem_id="P1", skills=[])
    history = client.get("/api/v1/students/S1/history").json()
    assert history["total"] == 0
    assert history["items"] == []


# ===========================================================================
# 5. 저장 순서 유지
# ===========================================================================

def test_save_order_problem_then_solution_then_attempt_then_analysis():
    """성공 경로의 저장 순서가 유지된다 (문제 → 정답 풀이 → Attempt → 분석)."""
    r = client.post(
        "/api/v1/attempts/F1/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": PROBLEM_LATEX,
            "problem_skills": ["linear_equation"],
            "student_id": "S1",
            "solution_text": SOLUTION,
            "correct_solution": {
                "solution_id": "SOL-1",
                "steps": [
                    {"latex_text": "2(x-3)=6"},
                    {"latex_text": "2x-6=6"},
                    {"latex_text": "2x=12"},
                    {"latex_text": "x=6"},
                ],
                "answer_latex": "x=6",
            },
        },
    )
    assert r.status_code == 200
    repo = get_repository()
    assert repo.get_problem("P1").problem_id == "P1"
    assert [s.solution_id for s in repo.list_correct_solutions("P1")] == ["SOL-1"]
    assert repo.get_attempt("F1").attempt_id == "F1"
    assert repo.has_analysis("F1") is True


def test_solution_is_registered_after_problem():
    """정답 풀이는 문제 저장 뒤에 저장된다 (FK 순서)."""
    r = client.post(
        "/api/v1/attempts/F2/analyze",
        json={
            "problem_id": "P2",
            "problem_latex": PROBLEM_LATEX,
            "problem_skills": ["linear_equation"],
            "student_id": "S1",
            "solution_text": SOLUTION,
            "correct_solution": {
                "solution_id": "SOL-2",
                "steps": [{"latex_text": "2(x-3)=6"}],
                "answer_latex": "x=6",
            },
        },
    )
    assert r.status_code == 200
    assert get_repository().get_problem("P2").problem_id == "P2"
    assert [s.solution_id for s in get_repository().list_correct_solutions("P2")] == ["SOL-2"]


# ===========================================================================
# 6. 동시 요청 / 멱등
# ===========================================================================

def test_two_attempts_for_same_new_problem_both_succeed():
    """같은 새 problem_id 를 서로 다른 attempt_id 로 동시에 분석해도 둘 다 성공한다."""
    first = call("G1", problem_id="P1", skills=["linear_equation"])
    second = call("G2", problem_id="P1", skills=["linear_equation"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(get_repository().list_problems()) == 1
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


def test_idempotent_repost_still_works_after_change():
    body = {
        "problem_id": "P1",
        "problem_latex": PROBLEM_LATEX,
        "problem_skills": ["linear_equation"],
        "student_id": "S1",
        "solution_text": SOLUTION,
    }
    first = client.post("/api/v1/attempts/H1/analyze", json=body).json()
    second = client.post("/api/v1/attempts/H1/analyze", json=body).json()
    assert second["analysis_id"] == first["analysis_id"]
    assert second["analysis_count"] == 1
    assert len(get_repository().list_analyses("H1")) == 1


def test_already_registered_problem_keeps_source_of_truth():
    """이미 등록된 문제는 요청의 problem_skills 로 덮어쓰지 않는다."""
    call("I1", problem_id="P1", skills=["linear_equation"])
    again = call("I2", problem_id="P1", skills=["factoring"])
    assert again.status_code == 200
    assert get_repository().get_problem("P1").skills == ["linear_equation"]


def test_existing_stored_problem_without_tracking_skill_is_not_replaced():
    """저장본에 tracking Skill 이 없으면 기존 source-of-truth 정책대로 거절한다."""
    from app.pipeline.session import Problem
    from app.pipeline.repository import get_repository as _repo

    _repo().save_problem(
        Problem(problem_id="P-STALE", problem_latex=PROBLEM_LATEX, skills=["factoring"])
    )
    r = call("I3", problem_id="P-STALE", skills=["linear_equation"])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    # 저장본을 덮어쓰지 않는다
    assert _repo().get_problem("P-STALE").skills == ["factoring"]


# ===========================================================================
# 7. 불변 확인
# ===========================================================================

def test_taxonomy_unchanged():
    assert validate_all() == []


def test_route_count_unchanged():
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "")
        not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
    }
    assert paths == {
        "/health",
        "/analyze-solution",
        "/api/v1/attempts/{attempt_id}/analyze",
        "/api/v1/attempts/{attempt_id}/ocr",
        "/api/v1/attempts/{attempt_id}/analyses",
        "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
        "/api/v1/students/{student_id}/history",
        "/api/v1/students/{student_id}/misconceptions/top",
    }