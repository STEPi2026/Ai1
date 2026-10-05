"""PHASE 8-6: 학생 단위 학습 이력 조회 API (앱 r34).

  GET /api/v1/students/{student_id}/history?page=1&page_size=20

원칙
  - 요청한 학생의 기록만 반환 (다른 학생 격리)
  - 최신 먼저: 모델에 시각 필드가 없어 저장소 삽입 순서의 역순
  - items / page / page_size / total (+ total_pages) 포함
  - 요약만 노출하고 원본 결과는 상세 GET 으로 연결 (detail_path)
  - GET 은 분석 실행·저장·Knowledge State 갱신을 하지 않음
  - POST 저장 정책(성공 분석 뒤 attempt 등록)은 변경하지 않음
"""

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
    "correct_solution": SOLUTION,
}

HISTORY_URL = "/api/v1/students/{}/history"

ITEM_FIELDS = {
    "attempt_id", "problem_id", "attempted_at", "latest_analysis_id", "detail_path",
    "analysis_count", "analysis_status", "correct", "error_step",
    "first_error_step_index", "error_type", "error_subtype", "skill", "state",
    "mastery", "misconception_id", "misconception_tags", "error_count",
    "observation_count", "step_count",
    "problem_text", "concept_name", "unit_name",
}


def post(attempt_id: str, student_id: str = "S1", solution_text: str = CORRECT, **kw):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "student_id": student_id, "solution_text": solution_text, **kw},
    )


def history(student_id: str = "S1", **params):
    return client.get(HISTORY_URL.format(student_id), params=params)


def append_analysis(attempt_id: str, solution_text: str = CORRECT) -> str:
    """같은 attempt 에 이력을 추가한다 (POST 멱등이라 저장소를 직접 사용)."""
    repo = get_repository()
    stored = repo.get_attempt(attempt_id)
    analysis = analyze_attempt(
        repo.get_problem("P1"),
        StudentAttempt(
            attempt_id=attempt_id,
            student_id=stored.student_id,
            problem_id=stored.problem_id,
            solution_text=solution_text,
        ),
        solution=repo.get_correct_solution("SOL-1"),
    )
    return repo.save_analysis(analysis).analysis_id


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 학생별 격리
# ===========================================================================

def test_history_is_isolated_per_student():
    post("A1", "S1")
    post("A2", "S2")
    post("A3", "S1")
    post("A4", "S3")
    s1 = history("S1").json()
    s2 = history("S2").json()
    s3 = history("S3").json()
    assert [i["attempt_id"] for i in s1["items"]] == ["A3", "A1"]
    assert [i["attempt_id"] for i in s2["items"]] == ["A2"]
    assert [i["attempt_id"] for i in s3["items"]] == ["A4"]
    assert (s1["total"], s2["total"], s3["total"]) == (2, 1, 1)


def test_other_student_records_never_leak_into_page():
    for i in range(5):
        post(f"B{i}", "OTHER")
    post("OWN", "MINE")
    body = history("MINE").json()
    assert body["total"] == 1
    assert [i["attempt_id"] for i in body["items"]] == ["OWN"]
    assert "OTHER" not in {i["attempt_id"] for i in body["items"]}


def test_history_of_unknown_student_is_empty_not_error():
    r = history("NOBODY")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_pages"] == 0
    assert body["student_id"] == "NOBODY"


def test_student_id_is_trimmed_like_post():
    post("A1", "S1")
    assert history(" S1 ").json()["total"] == 1


# ===========================================================================
# 빈 목록 및 페이지
# ===========================================================================

def test_empty_history_shape():
    body = history("S1").json()
    assert set(body) == {
        "student_id", "items", "page", "page_size", "total", "total_pages", "order",
    }
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["order"] == "attempted_at_desc"


def test_pagination_defaults_to_page1_size20():
    for i in range(25):
        post(f"C{i}")
    body = history().json()
    assert body["page"] == 1 and body["page_size"] == 20
    assert len(body["items"]) == 20
    assert body["total"] == 25
    assert body["total_pages"] == 2


def test_second_page_returns_remainder():
    for i in range(25):
        post(f"D{i}")
    second = history(page=2).json()
    assert len(second["items"]) == 5
    assert second["total"] == 25
    assert [i["attempt_id"] for i in second["items"]] == ["D4", "D3", "D2", "D1", "D0"]


def test_pages_do_not_overlap_and_cover_everything():
    for i in range(7):
        post(f"E{i}")
    seen = [
        i["attempt_id"] for page in (1, 2, 3, 4)
        for i in history(page=page, page_size=2).json()["items"]
    ]
    assert seen == ["E6", "E5", "E4", "E3", "E2", "E1", "E0"]
    assert len(seen) == len(set(seen))


def test_page_beyond_range_is_empty_but_keeps_total():
    for i in range(3):
        post(f"F{i}")
    body = history(page=99).json()
    assert body["items"] == []
    assert body["total"] == 3
    assert body["page"] == 99
    assert body["total_pages"] == 1


def test_page_size_variants():
    for i in range(10):
        post(f"G{i}")
    for size, expected_len, expected_pages in ((1, 1, 10), (3, 3, 4), (10, 10, 1), (100, 10, 1)):
        body = history(page_size=size).json()
        assert body["page_size"] == size
        assert len(body["items"]) == expected_len
        assert body["total_pages"] == expected_pages


# ===========================================================================
# 페이지 경계 / 잘못된 입력
# ===========================================================================

@pytest.mark.parametrize("params", [
    {"page": 0},
    {"page": -1},
    {"page": 0, "page_size": 10},
    {"page_size": 0},
    {"page_size": -5},
    {"page_size": 101},
    {"page_size": 1000},
    {"page": "abc"},
    {"page": 1.5},
    {"page_size": "many"},
])
def test_invalid_paging_is_422(params):
    post("A1")
    r = history(**params)
    assert r.status_code == 422


@pytest.mark.parametrize("params", [{"page": 1}, {"page_size": 1}, {"page": 1, "page_size": 100}])
def test_paging_boundaries_are_accepted(params):
    post("A1")
    assert history(**params).status_code == 200


@pytest.mark.parametrize("student_id", ["S1%00", "S1%01x", " "])
def test_invalid_student_id_is_422(student_id):
    r = history(student_id)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_STUDENT_ID"


def test_invalid_paging_returns_422_even_for_unknown_student():
    assert history("NOBODY", page=0).status_code == 422


# ===========================================================================
# 최신순 / 안정성
# ===========================================================================

def test_newest_attempt_comes_first():
    post("H1")
    post("H2")
    post("H3")
    items = history().json()["items"]
    assert [i["attempt_id"] for i in items] == ["H3", "H2", "H1"]


def test_order_is_stable_across_repeated_calls():
    for i in range(5):
        post(f"I{i}")
    first = [i["attempt_id"] for i in history().json()["items"]]
    for _ in range(5):
        assert [i["attempt_id"] for i in history().json()["items"]] == first


def test_order_is_stable_with_extra_analysis_on_same_attempt():
    """분석 이력이 늘어도 attempt 순서는 바뀌지 않는다 (삽입 순서 기준)."""
    post("J1")
    post("J2")
    append_analysis("J1", WRONG)
    assert [i["attempt_id"] for i in history().json()["items"]] == ["J2", "J1"]


def test_order_across_pagination_matches_full_list():
    for i in range(9):
        post(f"K{i}")
    full = [i["attempt_id"] for i in history(page_size=100).json()["items"]]
    paged = [
        i["attempt_id"]
        for page in (1, 2, 3)
        for i in history(page=page, page_size=4).json()["items"]
    ]
    assert paged == full


# ===========================================================================
# 최신 AnalysisRecord 연결
# ===========================================================================

def test_item_links_latest_analysis_id():
    posted = post("L1", "S1", WRONG).json()
    item = history().json()["items"][0]
    assert item["attempt_id"] == "L1"
    assert item["problem_id"] == "P1"
    assert item["latest_analysis_id"] == posted["analysis_id"]
    assert item["analysis_count"] == 1
    assert set(item) == ITEM_FIELDS


def test_item_summary_matches_stored_snapshot():
    posted = post("L2", "S1", WRONG).json()
    item = history().json()["items"][0]
    for key in (
        "analysis_status", "correct", "error_step", "first_error_step_index",
        "error_type", "error_subtype", "skill", "state", "mastery", "misconception_id",
    ):
        assert item[key] == posted[key], key
    assert item["misconception_tags"] == ["2.1"]
    assert item["error_count"] == 1
    assert item["observation_count"] == 2
    assert item["step_count"] == 4


def test_item_uses_latest_analysis_after_history_grows():
    post("L3", "S1", CORRECT)
    append_analysis("L3", WRONG)
    append_analysis("L3", CORRECT)
    item = history().json()["items"][0]
    assert item["analysis_count"] == 3
    assert item["latest_analysis_id"] == get_repository().get_latest_analysis("L3").analysis_id
    assert item["misconception_id"] is None, "최신 분석 기준이어야 한다"
    assert item["correct"] is True


def test_item_without_analysis_has_null_summary():
    """분석 없는 attempt (저장소를 직접 쓴 경우)도 목록에 포함된다."""
    post("L4", "S1")
    repo = get_repository()
    stored = repo.get_attempt("L4")
    repo.save_attempt(stored.model_copy(update={"attempt_id": "L4-bare"}))
    item = {i["attempt_id"]: i for i in history().json()["items"]}["L4-bare"]
    assert item["latest_analysis_id"] is None
    assert item["detail_path"] is None
    assert item["analysis_count"] == 0
    assert item["correct"] is None and item["analysis_status"] is None
    assert item["error_count"] == 0 and item["step_count"] == 4


def test_detail_path_is_url_encoded_and_works():
    item = history().json()["items"][0] if False else None
    post("L5", "S1")
    item = history().json()["items"][0]
    assert "#" in item["latest_analysis_id"]
    assert "%23" in item["detail_path"]
    assert "#" not in item["detail_path"]
    assert item["detail_path"] == (
        f"/api/v1/attempts/L5/analyses/{item['latest_analysis_id'].replace('#', '%23')}"
    )
    r = client.get(item["detail_path"])
    assert r.status_code == 200
    assert r.json()["analysis_id"] == item["latest_analysis_id"]


def test_detail_path_of_every_item_is_reachable():
    for i, text in enumerate([CORRECT, WRONG, CORRECT]):
        post(f"M{i}", "S1", text)
    for item in history().json()["items"]:
        r = client.get(item["detail_path"])
        assert r.status_code == 200, item["attempt_id"]
        assert r.json()["analysis_id"] == item["latest_analysis_id"]


def test_history_does_not_expose_raw_result():
    post("N1", "S1", WRONG)
    item = history().json()["items"][0]
    assert "raw_result" not in item
    assert "errors" not in item
    assert "step_list" not in item
    assert "observations" not in item
    assert "solution_text" not in item


# ===========================================================================
# 읽기 전용
# ===========================================================================

def _snapshot() -> dict:
    rule = analyzer.get_engine("rule")
    bkt = analyzer.get_engine("bkt")
    repo = get_repository()
    return {
        "streak": dict(rule._streak),
        "state": dict(rule._state),
        "p": dict(bkt._p),
        "analyses": sorted(repo._analyses),
        "attempts": sorted(repo._attempts),
        "seq": repo._seq,
    }


def test_history_does_not_change_state_or_storage():
    for i, text in enumerate([CORRECT, WRONG, CORRECT]):
        post(f"O{i}", "S1", text)
    post("O9", "S2", WRONG)
    append_analysis("O0", WRONG)
    before = _snapshot()
    for _ in range(5):
        assert history().status_code == 200
        assert history(page=2, page_size=1).status_code == 200
        assert history(page=99).status_code == 200
        assert history("NOBODY").status_code == 200
    assert _snapshot() == before


def test_history_does_not_call_analyze_attempt_or_save(monkeypatch):
    post("P1", "S1", WRONG)

    def boom(*args, **kwargs):
        raise AssertionError("조회 중 분석/저장이 실행되었다")

    monkeypatch.setattr(main_module, "analyze_attempt", boom)
    monkeypatch.setattr(
        type(get_repository()), "save_analysis", lambda *a, **k: boom()
    )
    assert history().status_code == 200
    assert history(page=1, page_size=100).status_code == 200


def test_history_does_not_take_the_analysis_gate():
    post("Q1", "S1")
    repo = get_repository()
    repo.claim_analysis("Q1")
    try:
        assert history().status_code == 200
    finally:
        repo.release_analysis("Q1")


def test_history_does_not_reset_or_advance_knowledge_state():
    first = post("R1", "S1").json()
    assert first["state"] == "learning"
    for _ in range(5):
        history()
    assert analyzer.get_engine("rule")._streak[("S1", "linear_equation")] == 1
    second = post("R2", "S1").json()
    assert second["state"] == "mastered", "조회가 관측을 소모했다"


# ===========================================================================
# 저장 정책 (POST) 회귀 — 이번 단계에서 변경하지 않음
# ===========================================================================

def test_failed_submission_is_not_in_history():
    """분석에 실패한 제출은 저장되지 않으므로 이력에도 나타나지 않는다 (Phase 8-4 정책)."""
    assert post("S1", "S1", "보통은 이렇게 푼다고 합니다").status_code == 422
    body = history().json()
    assert body["total"] == 0
    assert body["items"] == []


def test_unreviewable_submission_is_in_history():
    r = post("T1", "S1", "2(x-3)=6\n???\nx=6")
    assert r.status_code == 200
    item = history().json()["items"][0]
    assert item["attempt_id"] == "T1"
    assert item["analysis_status"] == r.json()["analysis_status"]
    assert item["analysis_count"] == 1


# ===========================================================================
# 회귀 — attempt 분석 / 상세 이력 / /analyze-solution
# ===========================================================================

def test_post_idempotency_intact():
    assert len({post("U1").json()["analysis_id"] for _ in range(4)}) == 1
    assert history().json()["total"] == 1
    assert history().json()["items"][0]["analysis_count"] == 1


def test_post_conflict_still_409():
    post("V1", "S1")
    assert post("V1", "S1", "2(x-3)=6\nx=3").status_code == 409
    assert history().json()["total"] == 1


def test_attempt_history_endpoints_intact():
    posted = post("W1", "S1", WRONG).json()
    listing = client.get("/api/v1/attempts/W1/analyses").json()
    assert listing["analysis_count"] == 1
    detail = client.get(
        f"/api/v1/attempts/W1/analyses/{posted['analysis_id'].replace('#', '%23')}"
    ).json()
    assert detail["raw_result"]["misconception_ids"] == ["2.1"]
    assert client.get("/api/v1/attempts/NOPE/analyses").status_code == 404


def test_analyze_solution_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 16
    assert body["misconception_id"] == "2.1"
    assert body["analysis_status"] == "ANALYZED"


def test_analyze_solution_creates_no_attempt_record():
    client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3", "student_id": "S1"},
    )
    assert history().json()["total"] == 0


def test_route_registered():
    assert "/api/v1/students/{student_id}/history" in {r.path for r in app.routes}
    assert client.get("/health").json() == {"status": "ok"}
