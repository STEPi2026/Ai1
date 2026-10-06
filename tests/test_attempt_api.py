"""Phase 8-3 회귀 — attempt 단위 분석 API.

경로: POST /api/v1/attempts/{attempt_id}/analyze

검증:
  - 정상 분석 저장 + analysis_id 반환
  - 오답 / REVIEW_REQUIRED 결과
  - UNKNOWN 422 의 code·status·message 보존, 요청 오류에는 status 없음
  - 없는 attempt/problem/정답 풀이 처리
  - 재분석 시 이력 보존 (analysis_id 갱신, 덮어쓰지 않음)
  - 명세서 필드 매핑 (step_list / is_correct·status / first_error_step_index / misconception_tag)
  - 기존 /analyze-solution 회귀
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import reset_knowledge
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import ErrorRecord, KnowledgeObservation
from app.taxonomy import is_valid_misconception_id

client = TestClient(app)
URL = "/api/v1/attempts/{attempt_id}/analyze"

SOLUTION = {
    "solution_id": "SOL-1",
    "steps": [
        {"latex_text": "2(x-3)=6"},
        {"latex_text": "2x-6=6"},
        {"latex_text": "2x=12"},
        {"latex_text": "x=6"},
    ],
    "answer_latex": "x=6",
}


def body(text: str, solution=True, **kw) -> dict:
    payload = {
        "problem_id": "P1",
        "problem_latex": "2(x-3)=6",
        "problem_skills": ["linear_equation", "distribution"],
        "student_id": "S1",
        "solution_text": text,
    }
    if solution:
        payload["correct_solution"] = SOLUTION
    payload.update(kw)
    return payload


def post(attempt_id: str, text: str, **kw):
    return client.post(URL.format(attempt_id=attempt_id), json=body(text, **kw))


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    reset_repository()
    yield
    reset_knowledge()
    reset_repository()


# ===========================================================================
# 정상 분석 저장 + analysis_id 반환
# ===========================================================================

def test_correct_attempt_stored_and_analysis_id_returned():
    r = post("A1", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_id"]
    assert b["attempt_id"] == "A1"
    assert b["problem_id"] == "P1"
    assert b["student_id"] == "S1"
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is True
    assert b["analysis_count"] == 1


def test_objects_are_persisted_in_repository():
    post("A1", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    repo = get_repository()
    assert repo.get_problem("P1").problem_latex == "2(x-3)=6"
    assert repo.get_attempt("A1").student_id == "S1"
    assert repo.get_correct_solution("SOL-1").answer_latex == "x=6"
    assert len(repo.list_analyses("A1")) == 1


def test_error_attempt_records_error_and_misconception():
    r = post("A2", "2(x-3)=6\n2x-3=6")
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert b["correct"] is False
    assert b["error_step"] == 2
    assert b["error_type"] == "concept_error"
    assert b["error_subtype"] == "distribution_omit"
    assert b["misconception_id"] == "2.1"
    assert b["misconception_tags"] == ["2.1"]
    assert b["skill"] == "distribution"
    assert b["state"] == "needs_practice"


def test_multi_error_exposed_in_errors_list():
    r = post("A3", "2(x-3)=6\n2x-6=7\n2x=13\nx=6")
    b = r.json()
    assert [e["step_no"] for e in b["errors"]] == [2, 4]
    assert set(b["errors"][0]) == set(ErrorRecord.model_fields)


# ===========================================================================
# REVIEW_REQUIRED
# ===========================================================================

def test_review_required_result():
    r = post("A4", "2(x-3)=6\n???\n2x-3=6")
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["correct"] is False
    assert b["first_error_step_index"] == 3
    assert b["unrecognized"] == [2]
    assert b["valid"] == [True, None, False]


def test_expression_only_step_is_unreviewable():
    r = post("A5", "2x-6\nx=3", solution=False, problem_id="P2", problem_latex="2x=6")
    b = r.json()
    assert b["analysis_status"] == "REVIEW_REQUIRED"
    assert b["step_list"][0]["status"] == "unreviewable"
    assert b["step_list"][0]["is_correct"] is None


# ===========================================================================
# UNKNOWN 422 — code/status/message 보존
# ===========================================================================

@pytest.mark.parametrize(
    "text,problem,code",
    [
        ("보통 풀이", "2x=6", "E_UNRECOGNIZED"),
        ("x=1", "x^99=1", "E_UNVERIFIABLE"),
    ],
)
def test_unknown_keeps_code_status_message(text, problem, code):
    r = client.post(
        URL.format(attempt_id="U1"),
        json={
            "problem_id": f"PU-{code}",
            "problem_latex": problem,
            "student_id": "S1",
            "solution_text": text,
        },
    )
    d = r.json()["detail"]
    assert r.status_code == 422
    assert d["code"] == code
    assert d["analysis_status"] == "UNKNOWN"
    assert d["message"]


def test_request_errors_have_no_analysis_status():
    r = client.post(
        URL.format(attempt_id="U2"),
        json={"problem_id": "P1", "problem_latex": "2x=6", "student_id": "S1",
              "solution_text": "", "correct_solution": SOLUTION},
    )
    d = r.json()["detail"]
    assert r.status_code == 422
    assert d["code"] == "E_EMPTY_SOLUTION"
    assert "analysis_status" not in d


def test_input_too_large_413_without_status():
    r = client.post(
        URL.format(attempt_id="U3"),
        json={"problem_id": "P1", "problem_latex": "x=" * 1200 + "0",
              "student_id": "S1", "solution_text": "x=0"},
    )
    assert r.status_code == 413
    assert "analysis_status" not in r.json()["detail"]


# ===========================================================================
# 없는 attempt / problem / 정답 풀이
# ===========================================================================

def test_unregistered_problem_without_latex_rejected():
    r = client.post(
        URL.format(attempt_id="N1"),
        json={"problem_id": "P-MISSING", "student_id": "S1", "solution_text": "2x=6\nx=3"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_REFERENCE_INTEGRITY"


def test_unregistered_problem_registered_on_first_sight():
    r = client.post(
        URL.format(attempt_id="N2"),
        json={"problem_id": "P-NEW", "problem_latex": "2x=6",
              "problem_skills": ["linear_equation"], "student_id": "S1",
              "solution_text": "2x=6\nx=3"},
    )
    assert r.status_code == 200
    assert get_repository().get_problem("P-NEW").problem_latex == "2x=6"


def test_missing_correct_solution_falls_back_to_no_alignment():
    r = post("N3", "2(x-3)=6\nx=6", solution=False)
    b = r.json()
    assert r.status_code == 200
    assert b["analysis_status"] == "ANALYZED"
    assert all(s["aligned_solution_step_no"] is None for s in b["step_list"])


def test_mismatched_correct_solution_content_does_not_error():
    """정답 풀이 내용이 문제와 맞지 않으면 정렬만 안 될 뿐 오류가 아니다.

    엔드포인트는 CorrectSolution.problem_id 를 요청의 problem_id 로 고정하므로
    '다른 problem_id 를 참조하는 정답 풀이'는 HTTP 로 만들 수 없다.
    """
    r = client.post(
        URL.format(attempt_id="N4"),
        json={
            "problem_id": "P-MISMATCH",
            "problem_latex": "3x=9",
            "problem_skills": ["linear_equation"],
            "student_id": "S1",
            "solution_text": "3x=9\nx=3",
            "correct_solution": {**SOLUTION, "solution_id": "SOL-MISMATCH"},
        },
    )
    assert r.status_code == 200
    b = r.json()
    assert b["analysis_status"] == "ANALYZED"
    assert all(s["aligned_solution_step_no"] is None for s in b["step_list"])


def test_problem_without_tracking_skill_rejected_at_analysis():
    """추적 가능 skill 이 없는 문제는 Knowledge State 를 갱신할 수 없어 422."""
    r = client.post(
        URL.format(attempt_id="N4b"),
        json={"problem_id": "P-NOSKILL", "problem_latex": "3x=9",
              "problem_skills": ["factoring"], "student_id": "S1",
              "solution_text": "3x=9\nx=3"},
    )
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "E_SKILL_NOT_TRACKABLE"
    assert "analysis_status" not in d, "요청 오류에 status 가 섞였다"


def test_empty_correct_solution_steps_rejected():
    r = client.post(
        URL.format(attempt_id="N5"),
        json={**body("2(x-3)=6\nx=6"),
              "correct_solution": {**SOLUTION, "steps": []}},
    )
    assert r.status_code == 422


def test_unknown_request_field_rejected():
    r = client.post(
        URL.format(attempt_id="N6"), json={**body("2x=6\nx=3"), "nope": 1}
    )
    assert r.status_code == 422


def test_attempt_conflict_not_overwritten():
    post("N7", "2(x-3)=6\nx=6")
    r = post("N7", "2(x-3)=6\n2x-6=6")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert get_repository().get_attempt("N7").solution_text == "2(x-3)=6\nx=6"


# ===========================================================================
# 멱등성: 같은 제출을 재전송해도 분석 이력이 쌓이지 않는다 (PHASE 8-4 정책 1)
# ===========================================================================

def test_idempotent_repeat_returns_same_analysis_id():
    text = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"
    first = post("R1", text).json()
    second = post("R1", text).json()
    assert first["analysis_id"] == second["analysis_id"]
    assert second["analysis_count"] == 1
    assert first == second
    history = get_repository().list_analyses("R1")
    assert [h.analysis_id for h in history] == [first["analysis_id"]]


def test_repeat_requests_do_not_append_history():
    text = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"
    ids = {post("R2", text).json()["analysis_id"] for _ in range(3)}
    assert len(ids) == 1
    assert len(get_repository().list_analyses("R2")) == 1
    assert post("R2", text).json()["analysis_count"] == 1


def test_different_attempts_separate_history():
    post("R3", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    post("R4", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    assert len(get_repository().list_analyses("R3")) == 1
    assert len(get_repository().list_analyses("R4")) == 1


# ===========================================================================
# 명세서 필드 매핑
# ===========================================================================

def test_step_list_shape():
    r = post("S1", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    steps = r.json()["step_list"]
    assert [s["step_no"] for s in steps] == [1, 2, 3, 4]
    for s in steps:
        assert set(s) == {
            "step_no", "latex_text", "is_correct", "status",
            "skill_ids", "aligned_solution_step_no", "unrecognized",
        }
    assert [s["status"] for s in steps] == ["correct"] * 4
    assert all(s["is_correct"] is True for s in steps)


def test_status_mapping_preserves_valid_semantics():
    r = post("S2", "2(x-3)=6\n???\n2x-3=6")
    steps = r.json()["step_list"]
    mapping = {(s["is_correct"], s["status"]) for s in steps}
    assert (True, "correct") in mapping
    assert (None, "unreviewable") in mapping
    assert (False, "error") in mapping


def test_first_error_step_index_is_one_based_same_as_error_step():
    r = post("S3", "2(x-3)=6\n2x-3=6")
    b = r.json()
    assert b["first_error_step_index"] == b["error_step"] == 2
    assert b["step_list"][b["first_error_step_index"] - 1]["status"] == "error"


def test_aligned_solution_step_no_exposed():
    r = post("S4", "2(x-3)=6\n2x-6=6\n2x=12\nx=6")
    assert [s["aligned_solution_step_no"] for s in r.json()["step_list"]] == [1, 2, 3, 4]


def test_misconception_tags_only_from_taxonomy():
    r = post("S5", "2(x-3)=6\n2x-3=6")
    tags = r.json()["misconception_tags"]
    assert tags == ["2.1"]
    assert all(is_valid_misconception_id(t) for t in tags), "taxonomy 밖 태그가 생성되었다"


def test_no_misconception_tag_for_calculation_error():
    r = post("S6", "2x-6=7\n2x=14", solution=False, problem_id="P9", problem_latex="2x-6=7")
    b = r.json()
    assert b["error_type"] == "calculation"
    assert b["misconception_tags"] == []


def test_observations_shape():
    r = post("S7", "2(x-3)=6\n2x-3=6")
    obs = r.json()["observations"]
    assert obs
    assert set(obs[0]) == set(KnowledgeObservation.model_fields)
    assert all(o["outcome"] in ("correct", "incorrect") for o in obs)


def test_response_field_set_is_stable():
    r = post("S8", "2(x-3)=6\n2x-3=6")
    assert set(r.json()) == {
        "analysis_id", "attempt_id", "problem_id", "student_id", "analysis_status",
        "analysis_count", "attempted_at", "correct", "error_step",
        "first_error_step_index", "error_type", "error_subtype", "skill", "state",
        "mastery", "misconception_id", "confidence", "steps_latex", "valid",
        "unrecognized", "step_list", "errors", "observations", "misconception_tags",
        "states", "mastery_by_skill",
    }


def test_knowledge_state_observable_per_skill():
    r = post("S9", "2(x-3)=6\n2x-3=6")
    b = r.json()
    assert "distribution" in b["states"]
    assert b["mastery_by_skill"]["distribution"] == b["mastery"]


# ===========================================================================
# 기존 /analyze-solution 회귀
# ===========================================================================

def test_analyze_solution_endpoint_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6",
              "student_id": "legacy", "problem_skill": "linear_equation"},
    )
    b = r.json()
    assert r.status_code == 200
    assert b["correct"] is False
    assert b["error_step"] == 2
    assert b["error_type"] == "concept_error"
    assert b["skill"] == "distribution"
    assert b["misconception_id"] == "2.1"
    assert b["analysis_status"] == "ANALYZED"
    assert "analysis_id" not in b, "기존 응답에 attempt 전용 필드가 섞였다"
    assert "step_list" not in b
    assert len(b) == 16


def test_analyze_solution_health_unchanged():
    assert client.get("/health").json() == {"status": "ok"}


def test_attempt_endpoint_does_not_leak_into_repository_from_analyze_solution():
    client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3", "student_id": "s"},
    )
    assert get_repository().list_problems() == []
    assert get_repository().list_attempts() == []
