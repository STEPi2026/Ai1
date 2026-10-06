"""PHASE 8-5: attempt 분석 이력 조회 API (앱 r61/r62).

  GET /api/v1/attempts/{attempt_id}/analyses
  GET /api/v1/attempts/{attempt_id}/analyses/{analysis_id}

원칙
  - 저장소 조회 메서드만 사용 (analyze_attempt 호출 없음, Knowledge State 무변경)
  - 목록 정렬은 저장소 생성 순서(오래된→최신) 오름차순, reverse=true 로 뒤집기
  - 상세는 analysis_id 가 경로의 attempt_id 에 속하는지 검증
  - 응답은 저장 시점 스냅샷 (raw_result)
  - attempt 미존재(404) / 이력 0건(200 빈 목록) / analysis 미존재(404) 를 구분
"""

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import StudentAttempt, analyze_attempt

client = TestClient(app)

CORRECT = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"
WRONG = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"

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

BASE = {
    "problem_id": "P1",
    "problem_latex": "2(x-3)=6",
    "problem_skills": ["linear_equation", "distribution"],
    "student_id": "S1",
    "correct_solution": SOLUTION,
}

LIST_URL = "/api/v1/attempts/{}/analyses"
DETAIL_URL = "/api/v1/attempts/{}/analyses/{}"


def encode(analysis_id: str) -> str:
    """analysis_id 의 '#' 은 URL fragment 구분자이므로 퍼센트 인코딩이 필요하다."""
    return quote(analysis_id, safe="")


def post(attempt_id: str, solution_text: str = CORRECT, **overrides):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "solution_text": solution_text, **overrides},
    )


def get_list(attempt_id: str, **params):
    return client.get(LIST_URL.format(attempt_id), params=params)


def get_detail(attempt_id: str, analysis_id: str):
    return client.get(DETAIL_URL.format(attempt_id, encode(analysis_id)))


def append_analysis(attempt_id: str, solution_text: str = CORRECT) -> str:
    """같은 attempt 에 분석 이력을 추가한다 (저장소 직접 사용, 정책 4 의 재분석).

    POST 는 멱등이라 새 이력을 만들지 않으므로 이 경로는 저장소를 직접 쓴다.
    analysis_id 의 숫자 부분은 저장소 전역 시퀀스이므로 값 을 가정하지 않는다.
    """
    repo = get_repository()
    attempt = StudentAttempt(
        attempt_id=attempt_id,
        student_id="S1",
        problem_id="P1",
        solution_text=solution_text,
    )
    analysis = analyze_attempt(
        repo.get_problem("P1"), attempt, solution=repo.get_correct_solution("SOL-1")
    )
    return repo.save_analysis(analysis).analysis_id


def latest_id(attempt_id: str) -> str:
    record = get_repository().get_latest_analysis(attempt_id)
    assert record is not None
    return record.analysis_id


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 목록 조회 — 0건 / 1건 / 복수 건
# ===========================================================================

def test_list_returns_404_when_attempt_missing():
    r = get_list("NOPE")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "E_ATTEMPT_NOT_FOUND"


def test_list_of_unanalyzed_attempt_is_empty_not_404():
    """attempt 는 존재하지만 이력이 없으면 200 + 빈 목록 (0건과 미존재 구분)."""
    post("A0")
    repo = get_repository()
    repo.save_attempt(repo.get_attempt("A0").model_copy(update={"attempt_id": "A0-empty"}))
    r = get_list("A0-empty")
    assert r.status_code == 200
    body = r.json()
    assert body["analysis_count"] == 0
    assert body["analyses"] == []
    assert body["latest_analysis_id"] is None
    assert get_list("NOPE").status_code == 404


def test_list_with_single_analysis():
    r = post("A1").json()["analysis_id"]
    body = get_list("A1").json()
    assert body["attempt_id"] == "A1"
    assert body["analysis_count"] == 1
    assert body["latest_analysis_id"] == r
    assert [a["analysis_id"] for a in body["analyses"]] == [r]


def test_list_with_multiple_analyses():
    first = post("A2").json()["analysis_id"]
    second = append_analysis("A2", WRONG)
    third = append_analysis("A2")
    body = get_list("A2").json()
    assert body["analysis_count"] == 3
    assert [a["analysis_id"] for a in body["analyses"]] == [first, second, third]
    assert body["latest_analysis_id"] == third


def test_list_item_summary_fields():
    post("A3", WRONG)
    item = get_list("A3").json()["analyses"][0]
    assert set(item) == {
        "analysis_id", "attempt_id", "problem_id", "student_id", "analysis_status",
        "correct", "error_type", "error_subtype", "skill", "state", "mastery",
        "misconception_id", "first_error_step_index", "confidence",
        "error_count", "observation_count",
    }
    assert item["misconception_id"] == "2.1"
    assert item["error_type"] == "concept_error"
    assert item["first_error_step_index"] == 2
    assert item["error_count"] == 1
    assert item["observation_count"] == 2
    assert item["analysis_status"] == "ANALYZED"


def test_list_summary_matches_post_response():
    posted = post("A4", WRONG).json()
    item = get_list("A4").json()["analyses"][0]
    for key in (
        "analysis_status", "correct", "error_type", "error_subtype", "skill",
        "state", "mastery", "misconception_id", "first_error_step_index",
        "confidence", "attempt_id", "problem_id", "student_id",
    ):
        assert item[key] == posted[key], key


# ===========================================================================
# 목록 정렬 순서
# ===========================================================================

def test_list_order_is_creation_order_ascending():
    ids = [post("A5").json()["analysis_id"]]
    ids.append(append_analysis("A5", WRONG))
    ids.append(append_analysis("A5"))
    body = get_list("A5").json()
    assert body["order"] == "created_asc"
    assert [a["analysis_id"] for a in body["analyses"]] == ids
    assert body["analyses"][0]["analysis_id"] == ids[0], "가장 오래된 분석이 먼저"
    assert body["analyses"][-1]["analysis_id"] == body["latest_analysis_id"]


def test_list_reverse_returns_newest_first():
    first = post("A6").json()["analysis_id"]
    second = append_analysis("A6", WRONG)
    body = get_list("A6", reverse="true").json()
    assert [a["analysis_id"] for a in body["analyses"]] == [second, first]
    assert body["latest_analysis_id"] == second, "reverse 여도 latest 는 최신 ID"
    assert body["analysis_count"] == 2


def test_list_order_is_stable_across_repeated_reads():
    post("A7")
    append_analysis("A7", WRONG)
    first = [a["analysis_id"] for a in get_list("A7").json()["analyses"]]
    for _ in range(3):
        assert [a["analysis_id"] for a in get_list("A7").json()["analyses"]] == first


# ===========================================================================
# 상세 조회
# ===========================================================================

def test_detail_returns_stored_snapshot():
    posted = post("A8").json()
    body = get_detail("A8", posted["analysis_id"]).json()
    assert body["analysis_id"] == posted["analysis_id"]
    assert body["analysis_count"] == 1
    # raw_result 는 저장 시점의 응답 원본 전체이며, GET 은 여기에 파생 필드를 더한다.
    assert set(body["raw_result"]) <= set(posted) | {"skills", "skill_ids", "misconception_ids"}
    for key, value in body["raw_result"].items():
        if key in posted:
            assert posted[key] == value, key
    assert body["raw_result"]["skills"] == ["linear_equation"]
    for key in ("analysis_status", "correct", "state", "mastery", "error_step"):
        assert body[key] == posted[key], key


def test_detail_includes_full_error_and_observation_payload():
    posted = post("A9", WRONG).json()
    body = get_detail("A9", posted["analysis_id"]).json()
    assert body["misconception_tags"] == ["2.1"]
    assert len(body["step_list"]) == 4
    assert body["errors"][0]["error_type"] == "concept_error"
    assert body["errors"][0]["misconception_ids"] == ["2.1"]
    assert {o["skill_id"] for o in body["observations"]} == {
        "distribution",
        "polynomial_multiplication",
    }
    assert set(body["states"]) == {"distribution", "polynomial_multiplication"}
    assert set(body["mastery_by_skill"]) == {"distribution", "polynomial_multiplication"}


def test_detail_keeps_snapshot_of_that_moment_not_latest_state():
    """저장 당시 스냅샷이므로 이후 상태가 변해도 값이 바뀌지 않는다."""
    posted = post("A10").json()
    before = get_detail("A10", posted["analysis_id"]).json()
    append_analysis("A10", WRONG)  # 다른 제출로 상태가 변한다
    after = get_detail("A10", posted["analysis_id"]).json()
    assert after["state"] == before["state"], "저장 스냅샷이 최신 상태로 덮어써졌다"
    assert after["raw_result"] == before["raw_result"]
    assert after["analysis_count"] == 2, "analysis_count 는 attempt 현재 이력 수"
    assert get_detail("A10", posted["analysis_id"]).json()["analysis_id"] == posted["analysis_id"]


def test_detail_of_middle_record_in_history():
    first = post("A11").json()["analysis_id"]
    second = append_analysis("A11", WRONG)
    third = append_analysis("A11")
    body = get_detail("A11", second).json()
    assert body["analysis_id"] == second
    assert body["misconception_id"] == "2.1"
    assert get_detail("A11", first).json()["misconception_id"] is None
    assert get_detail("A11", third).json()["misconception_id"] is None


def test_detail_requires_url_encoded_analysis_id():
    analysis_id = post("A12").json()["analysis_id"]
    assert "#" in analysis_id
    assert get_detail("A12", analysis_id).status_code == 200
    raw = client.get(f"/api/v1/attempts/A12/analyses/{analysis_id}")
    assert raw.status_code == 404, "'#' 은 fragment 이라 인코딩 없이 경로로 전달되지 않는다"


# ===========================================================================
# 존재하지 않는 attempt / analysis
# ===========================================================================

def test_detail_404_when_attempt_missing():
    post("A13")
    r = get_detail("NOPE", latest_id("A13"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "E_ATTEMPT_NOT_FOUND"


def test_detail_404_when_analysis_missing():
    post("A14")
    r = get_detail("A14", "A14#999999")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "E_ANALYSIS_NOT_FOUND"
    assert "message" in r.json()["detail"]


def test_detail_404_codes_differ_between_attempt_and_analysis():
    post("A15")
    assert get_detail("A15", "ZZZ").json()["detail"]["code"] == "E_ANALYSIS_NOT_FOUND"
    assert get_detail("ZZZ", latest_id("A15")).json()["detail"]["code"] == (
        "E_ATTEMPT_NOT_FOUND"
    )


# ===========================================================================
# 다른 attempt 의 analysis_id 거부
# ===========================================================================

def test_detail_rejects_analysis_id_of_another_attempt():
    own = post("A16").json()["analysis_id"]
    other = post("A17").json()["analysis_id"]
    assert other != own
    r = get_detail("A16", other)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "E_ANALYSIS_NOT_FOUND"
    assert get_detail("A16", own).status_code == 200
    assert get_detail("A17", other).status_code == 200


def test_detail_rejection_does_not_leak_other_attempt_existence():
    post("A18")
    post("A19")
    other = latest_id("A19")
    existing = get_detail("A18", other)
    absent = get_detail("A18", "ZZZ#999999")
    assert existing.status_code == absent.status_code == 404
    assert existing.json()["detail"]["code"] == absent.json()["detail"]["code"] == (
        "E_ANALYSIS_NOT_FOUND"
    )
    # 메시지는 '요청한 attempt 의 이력에 없다' 는 동일 문구이며, 타 attempt 의
    # 존재 여부를 드러내지 않는다 (요청한 id 만 그대로 되돌린다).
    template = "attempt 'A18' 에 analysis '{}' 이(가) 없습니다."
    assert existing.json()["detail"]["message"] == template.format(other)
    assert absent.json()["detail"]["message"] == template.format("ZZZ#999999")


# ===========================================================================
# 읽기 전용 — Knowledge State / 이력 무변경
# ===========================================================================

def _snapshot() -> dict:
    rule = analyzer.get_engine("rule")
    bkt = analyzer.get_engine("bkt")
    return {
        "streak": dict(rule._streak),
        "state": dict(rule._state),
        "p": dict(bkt._p),
        "analyses": sorted(get_repository()._analyses),
    }


def test_reads_do_not_change_knowledge_state_or_history():
    post("A19", WRONG)
    post("A20", CORRECT, engine="bkt")
    before = _snapshot()
    for _ in range(3):
        assert get_list("A19").status_code == 200
        assert get_list("A20").status_code == 200
        assert get_detail("A19", latest_id("A19")).status_code == 200
        assert get_list("NOPE").status_code == 404
        assert get_detail("A19", "A19#999999").status_code == 404
    assert _snapshot() == before


def test_reads_do_not_call_analyze_attempt(monkeypatch):
    analysis_id = post("A21", WRONG).json()["analysis_id"]

    def boom(*args, **kwargs):
        raise AssertionError("조회 중 analyze_attempt() 가 호출되었다")

    monkeypatch.setattr(main_module, "analyze_attempt", boom)
    assert get_list("A21").status_code == 200
    assert get_detail("A21", analysis_id).status_code == 200
    assert get_list("NOPE").status_code == 404
    assert get_detail("A21", "A21#999999").status_code == 404


def test_reads_do_not_take_the_analysis_gate():
    """조회가 POST 의 분석 구간 락을 잡지 않아 동시 분석을 막지 않는다."""
    post("A22")
    analysis_id = latest_id("A22")
    repo = get_repository()
    repo.claim_analysis("A22")
    try:
        assert get_list("A22").status_code == 200
        assert get_detail("A22", analysis_id).status_code == 200
    finally:
        repo.release_analysis("A22")


# ===========================================================================
# POST 멱등 정책 및 기존 API 회귀
# ===========================================================================

def test_post_idempotency_still_holds_after_adding_gets():
    ids = {post("A23").json()["analysis_id"] for _ in range(4)}
    assert len(ids) == 1
    body = get_list("A23").json()
    assert body["analysis_count"] == 1
    assert [a["analysis_id"] for a in body["analyses"]] == list(ids)


def test_post_does_not_mutate_history_counts():
    post("A24")
    append_analysis("A24", WRONG)
    before = get_list("A24").json()["analysis_count"]
    post("A24")
    post("A24", WRONG)
    assert get_list("A24").json()["analysis_count"] == before == 2


def test_conflicting_post_still_409_with_gets_present():
    post("A25")
    r = post("A25", "2(x-3)=6\nx=3")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert get_list("A25").json()["analysis_count"] == 1


def test_analyze_solution_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 16
    assert body["skill"] == "distribution"
    assert body["misconception_id"] == "2.1"
    assert body["analysis_status"] == "ANALYZED"


def test_gets_do_not_affect_analyze_solution_state():
    post("A26", CORRECT)
    payload = {
        "problem_latex": "2x=6",
        "solution_text": "2x=6\nx=3",
        "student_id": "S9",
    }
    analyzer.reset_knowledge()
    before = client.post("/analyze-solution", json=payload).json()
    for _ in range(3):
        get_list("A26")
        get_detail("A26", latest_id("A26"))
    analyzer.reset_knowledge()
    after = client.post("/analyze-solution", json=payload).json()
    assert after == before


def test_health_and_paths_registered():
    assert client.get("/health").json() == {"status": "ok"}
    paths = {r.path for r in app.routes}
    assert "/api/v1/attempts/{attempt_id}/analyses" in paths
    assert "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}" in paths
    assert "/api/v1/attempts/{attempt_id}/analyze" in paths
