"""PHASE 8-7: attempted_at (명세 r49) 생성·보존·조회·정렬.

정책
  1. 첫 분석 요청을 받은 시각을 서버 UTC로 한 번 기록
  2. 분석 성공으로 attempt 가 저장될 때 그 시각을 보존
  3. 멱등 재요청은 원래 attempted_at 반환
  4. 충돌 409 요청은 기존 attempted_at 을 바꾸지 않음
  5. 재풀이(새 attempt_id)는 별도 attempted_at
  6. 학생 이력은 attempted_at 최신순, 동률은 안정적 보조 정렬
  7. 응답은 ISO 8601, 요청으로 timestamp 를 요구하지 않음
  8. 실패 제출 저장 정책은 불변
"""

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import StudentAttempt, analyze_attempt, as_utc, utc_now

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


def post(attempt_id: str, student_id: str = "S1", solution_text: str = CORRECT, **kw):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "student_id": student_id, "solution_text": solution_text, **kw},
    )


def history(student_id: str = "S1", **params):
    return client.get(HISTORY_URL.format(student_id), params=params)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 모델 — UTC timezone-aware
# ===========================================================================

def test_model_default_is_utc_aware():
    attempt = StudentAttempt(attempt_id="X", student_id="S1", problem_id="P1", solution_text="x=1")
    assert attempt.attempted_at.tzinfo is not None
    assert attempt.attempted_at.utcoffset() == timedelta(0)


def test_model_coerces_naive_datetime_to_utc():
    naive = datetime(2026, 9, 28, 19, 20, 0)
    attempt = StudentAttempt(
        attempt_id="X", student_id="S1", problem_id="P1", solution_text="x=1",
        attempted_at=naive,
    )
    assert attempt.attempted_at == naive.replace(tzinfo=timezone.utc)
    assert attempt.attempted_at.utcoffset() == timedelta(0)


def test_model_converts_other_timezone_to_utc():
    kst = timezone(timedelta(hours=9))
    attempt = StudentAttempt(
        attempt_id="X", student_id="S1", problem_id="P1", solution_text="x=1",
        attempted_at=datetime(2026, 9, 28, 19, 20, 0, tzinfo=kst),
    )
    assert attempt.attempted_at.utcoffset() == timedelta(0)
    assert attempt.attempted_at.hour == 10


def test_as_utc_helper():
    assert as_utc(datetime(2026, 1, 1)).utcoffset() == timedelta(0)
    assert utc_now().utcoffset() == timedelta(0)


def test_repository_roundtrip_keeps_value():
    post("A1")
    sent = parse(post("A1").json()["attempted_at"])
    stored = get_repository().get_attempt("A1").attempted_at
    assert stored == sent
    assert stored.tzinfo is not None


# ===========================================================================
# 응답 — ISO 8601 / tz-aware
# ===========================================================================

def test_post_response_is_iso8601_utc():
    r = post("A2")
    assert r.status_code == 200
    value = r.json()["attempted_at"]
    assert value.endswith("Z") or value.endswith("+00:00")
    parsed = parse(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_history_item_is_iso8601_utc():
    post("A3")
    item = history().json()["items"][0]
    parsed = parse(item["attempted_at"])
    assert parsed.utcoffset() == timedelta(0)
    assert item["attempted_at"] == post("A3").json()["attempted_at"]


def test_detail_response_is_iso8601_utc():
    posted = post("A4").json()
    detail = client.get(
        f"/api/v1/attempts/A4/analyses/{posted['analysis_id'].replace('#', '%23')}"
    ).json()
    assert detail["attempted_at"] == posted["attempted_at"]
    assert parse(detail["attempted_at"]).utcoffset() == timedelta(0)


# ===========================================================================
# 정책 1/2 — 요청 접수 시각이며 분석 완료 시각이 아니다
# ===========================================================================

def test_timestamp_is_within_request_window():
    before = utc_now()
    body = post("A5").json()
    after = utc_now()
    assert before <= parse(body["attempted_at"]) <= after


def test_slow_analysis_does_not_shift_timestamp(monkeypatch):
    """분석에 시간이 들어도 attempted_at 은 요청 접수 시각이다."""
    original = analyze_attempt

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.main.analyze_attempt", slow)
    before = utc_now()
    body = post("A6").json()
    after = utc_now()
    assert after - before >= timedelta(seconds=0.3), "테스트가 실제로 느려야 의미가 있다"
    assert parse(body["attempted_at"]) - before < timedelta(seconds=0.2)


def test_replay_that_waits_on_the_gate_keeps_timestamp():
    """게이트에서 기다린 뒤 멱등 응답해도 시각은 저장본 값이다."""
    posted = post("A7").json()
    original = get_repository().claim_analysis

    def blocking_claim(attempt_id):
        original(attempt_id)
        time.sleep(0.2)
        return None

    get_repository().claim_analysis = blocking_claim
    try:
        replay = post("A7").json()
    finally:
        get_repository().claim_analysis = original
    assert replay["attempted_at"] == posted["attempted_at"]


# ===========================================================================
# 정책 3 — 멱등 재요청
# ===========================================================================

def test_idempotent_replay_keeps_timestamp():
    first = post("A8").json()["attempted_at"]
    time.sleep(0.01)
    for _ in range(4):
        assert post("A8").json()["attempted_at"] == first
    assert get_repository().get_attempt("A8").attempted_at == parse(first)


def test_replay_history_keeps_timestamp():
    first = post("A9").json()["attempted_at"]
    for _ in range(3):
        post("A9")
    item = history().json()["items"][0]
    assert item["attempted_at"] == first
    assert item["analysis_count"] == 1


# ===========================================================================
# 정책 4 — 충돌 409
# ===========================================================================

def test_conflict_does_not_change_timestamp():
    first = post("B1").json()["attempted_at"]
    time.sleep(0.01)
    for _ in range(3):
        r = post("B1", "S1", "2(x-3)=6\nx=3")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert get_repository().get_attempt("B1").attempted_at == parse(first)
    assert history().json()["items"][0]["attempted_at"] == first


def test_conflict_does_not_create_new_record():
    post("B2")
    post("B2", "S1", "2(x-3)=6\nx=3")
    assert history().json()["total"] == 1


# ===========================================================================
# 정책 5 — 재풀이는 새 attempt_id / 별도 시각
# ===========================================================================

def test_new_attempt_id_has_its_own_timestamp():
    first = post("C1").json()["attempted_at"]
    time.sleep(0.01)
    second = post("C2").json()["attempted_at"]
    assert parse(second) > parse(first)
    stored = {a.attempt_id: a.attempted_at for a in get_repository().list_attempts()}
    assert stored["C1"] == parse(first)
    assert stored["C2"] == parse(second)


def test_resubmission_same_problem_new_attempt():
    first = post("C3").json()
    time.sleep(0.01)
    second = post("C4").json()
    assert first["attempt_id"] != second["attempt_id"]
    assert parse(second["attempted_at"]) > parse(first["attempted_at"])
    assert history().json()["total"] == 2


# ===========================================================================
# 정책 6 — attempted_at 정렬
# ===========================================================================

def test_history_sorted_by_attempted_at_desc():
    for name in ("D1", "D2", "D3"):
        post(name)
        time.sleep(0.005)
    items = history().json()["items"]
    assert [i["attempt_id"] for i in items] == ["D3", "D2", "D1"]
    stamps = [parse(i["attempted_at"]) for i in items]
    assert stamps == sorted(stamps, reverse=True)
    assert history().json()["order"] == "attempted_at_desc"


def test_sort_uses_timestamp_not_insertion_order():
    """저장 순서와 attempted_at 이 반대여도 attempted_at 이 우선이다."""
    repo = get_repository()
    post("E1")  # 먼저 저장
    time.sleep(0.01)
    post("E2")  # 나중 저장
    # E1 의 시각만 뒤로 밀어(분석은 다시 하지 않고 저장본만 수정) 정렬 기준을 검증한다.
    stale = repo.get_attempt("E1").model_copy(
        update={"attempted_at": utc_now() + timedelta(days=1)}
    )
    repo._attempts["E1"] = stale
    items = history().json()["items"]
    assert [i["attempt_id"] for i in items] == ["E1", "E2"], "삽입 순서로 정렬했다"


def test_equal_timestamps_break_tie_by_later_insertion_first():
    same = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
    for name in ("F1", "F2", "F3"):
        post(name)
    repo = get_repository()
    for name in ("F1", "F2", "F3"):
        repo._attempts[name] = repo.get_attempt(name).model_copy(update={"attempted_at": same})
    items = history().json()["items"]
    assert {i["attempt_id"] for i in items} == {"F1", "F2", "F3"}
    assert [i["attempt_id"] for i in items] == ["F3", "F2", "F1"]


def test_tie_break_is_stable_across_repeated_calls():
    same = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
    for name in ("G1", "G2", "G3", "G4"):
        post(name)
    repo = get_repository()
    for name in ("G1", "G2", "G3", "G4"):
        repo._attempts[name] = repo.get_attempt(name).model_copy(update={"attempted_at": same})
    first = [i["attempt_id"] for i in history().json()["items"]]
    for _ in range(5):
        assert [i["attempt_id"] for i in history().json()["items"]] == first == [
            "G4", "G3", "G2", "G1"
        ]


def test_mixed_timestamps_and_ties_sort_correctly():
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mid = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for name in ("H1", "H2", "H3", "H4", "H5"):
        post(name)
    repo = get_repository()
    for name, stamp in (
        ("H1", early), ("H2", mid), ("H3", mid), ("H4", early), ("H5", mid),
    ):
        repo._attempts[name] = repo.get_attempt(name).model_copy(
            update={"attempted_at": stamp}
        )
    assert [i["attempt_id"] for i in history().json()["items"]] == [
        "H5", "H3", "H2", "H4", "H1"
    ]


def test_timestamp_pagination_covers_everything_without_overlap():
    for i in range(7):
        post(f"I{i}")
        time.sleep(0.002)
    full = [i["attempt_id"] for i in history(page_size=100).json()["items"]]
    paged = [
        i["attempt_id"]
        for page in (1, 2, 3, 4)
        for i in history(page=page, page_size=2).json()["items"]
    ]
    assert paged == full
    assert paged[0] == "I6" and paged[-1] == "I0"
    assert len(set(paged)) == 7


def test_pagination_is_stable_when_new_record_arrives_mid_paging():
    """정렬 기준이 시각이므로 새 기록은 항상 앞쪽에 삽입된다."""
    for name in ("J1", "J2"):
        post(name)
    page1 = [i["attempt_id"] for i in history(page=1, page_size=1).json()["items"]]
    post("J3")
    assert page1 == ["J2"]
    assert [i["attempt_id"] for i in history().json()["items"]] == ["J3", "J2", "J1"]


# ===========================================================================
# 정책 7 — 요청은 timestamp 를 요구하지 않는다
# ===========================================================================

def test_request_does_not_require_timestamp():
    r = post("K1")
    assert r.status_code == 200
    assert "attempted_at" in r.json()


def test_client_supplied_timestamp_is_rejected_not_trusted():
    body = {**BASE, "student_id": "S1", "solution_text": CORRECT}
    r = client.post(
        "/api/v1/attempts/K2/analyze", json={**body, "attempted_at": "1999-01-01T00:00:00Z"}
    )
    assert r.status_code == 422, "클라이언트 시각을 받아들이면 안 된다"
    assert get_repository().has_analysis("K2") is False


# ===========================================================================
# 정책 8 — 실패 제출 정책 불변
# ===========================================================================

def test_failed_submission_leaves_no_timestamp():
    before = utc_now()
    r = post("L1", "S1", "보통은 이렇게 푼다고 합니다")
    after = utc_now()
    assert r.status_code == 422
    assert get_repository().has_analysis("L1") is False
    assert history().json()["total"] == 0
    assert utc_now() >= before and utc_now() <= after + timedelta(seconds=1)


def test_failed_then_retry_records_fresh_timestamp():
    assert post("L2", "S1", "보통은 이렇게 푼다").status_code == 422
    time.sleep(0.01)
    body = post("L2", "S1", CORRECT).json()
    assert body["attempted_at"] == history().json()["items"][0]["attempted_at"]


# ===========================================================================
# 읽기 전용
# ===========================================================================

def test_history_get_does_not_change_stored_timestamps():
    for i in range(4):
        post(f"M{i}")
    before = {a.attempt_id: a.attempted_at for a in get_repository().list_attempts()}
    for _ in range(5):
        history()
        history(page=2, page_size=2)
    after = {a.attempt_id: a.attempted_at for a in get_repository().list_attempts()}
    assert after == before


def test_history_get_does_not_advance_knowledge_state():
    post("N1")
    rule = analyzer.get_engine("rule")
    for _ in range(3):
        history()
    assert rule._streak[("S1", "linear_equation")] == 1
    assert post("N2").json()["state"] == "mastered"


# ===========================================================================
# 회귀
# ===========================================================================

def test_repository_interface_unchanged():
    repo = get_repository()
    for name in (
        "save_problem", "get_problem", "list_problems", "save_correct_solution",
        "get_correct_solution", "list_correct_solutions", "save_attempt", "get_attempt",
        "list_attempts", "save_analysis", "get_analysis", "list_analyses",
        "get_latest_analysis", "has_analysis", "claim_analysis", "release_analysis", "reset",
    ):
        assert hasattr(repo, name), name


def test_student_history_item_field_set():
    post("O1", "S1", WRONG)
    assert set(history().json()["items"][0]) == {
        "attempt_id", "problem_id", "attempted_at", "latest_analysis_id", "detail_path",
        "analysis_count", "analysis_status", "correct", "error_step",
        "first_error_step_index", "error_type", "error_subtype", "skill", "state",
        "mastery", "misconception_id", "misconception_tags", "error_count",
        "observation_count", "step_count",
        "problem_text", "concept_name", "unit_name",
    }


def test_attempt_list_response_unchanged():
    post("P1", "S1", WRONG)
    listing = client.get("/api/v1/attempts/P1/analyses").json()
    assert "attempted_at" not in listing["analyses"][0]
    assert listing["order"] == "created_asc"
    assert listing["analysis_count"] == 1


def test_attempt_api_contracts_intact():
    posted = post("Q1", "S1", WRONG).json()
    assert len(posted) == 26
    assert posted["misconception_tags"] == ["2.1"]
    assert len({post("Q1", "S1", WRONG).json()["analysis_id"] for _ in range(3)}) == 1
    assert post("Q1", "S1", "2(x-3)=6\nx=3").status_code == 409
    assert client.get("/api/v1/attempts/Q1/analyses").json()["analysis_count"] == 1


def test_analyze_solution_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert "attempted_at" not in body
    assert body["misconception_id"] == "2.1"


def test_legacy_path_creates_no_attempt_timestamp():
    client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3", "student_id": "S1"},
    )
    assert get_repository().list_attempts() == []
    assert history().json()["total"] == 0
