"""PHASE 8-11: 학생 이력 / 오개념 TOP 기간 필터 (from, to).

계약
  - from / to 는 timezone offset 이 포함된 ISO 8601 datetime (선택)
  - 비교 전 UTC 로 정규화, 구간은 [from, to) — from 포함, to 제외
  - from >= to 면 422, offset 없으면 422
  - applied: history = 필터 → 정렬 → total → 페이지네이션
             TOP     = 필터 → attempt별 최신 분석 → distinct 집계 → 정렬 → limit
  - attempted_at 은 **서버가 첫 분석 요청을 받은 시각** (클라이언트 풀이 시각 아님)
  - 필터 미지정 시 기존 응답·정렬·멱등성 유지
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import ErrorRecord, StudentAttempt, analyze_attempt

client = TestClient(app)

HISTORY_URL = "/api/v1/students/{}/history"
TOP_URL = "/api/v1/students/{}/misconceptions/top"

M21 = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"
M31 = "2(x-3)=6\n2x-3=3\n2x-3=3\n2x-3=3"
CALC = "2(x-3)=6\n2x-6=7\n2x=13\nx=6.5"
OK = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"

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

KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))


def post(attempt_id: str, solution_text: str, student_id: str = "S1"):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "student_id": student_id, "solution_text": solution_text},
    )


def history(student_id: str = "S1", **params):
    return client.get(HISTORY_URL.format(student_id), params=params)


def top(student_id: str = "S1", **params):
    return client.get(TOP_URL.format(student_id), params=params)


def stamp(attempt_id: str) -> datetime:
    return get_repository().get_attempt(attempt_id).attempted_at


def ids(body: dict) -> list[str]:
    return [i["attempt_id"] for i in body["items"]]


def seed(attempt_ids: list[str], text: str = M21) -> None:
    for attempt_id in attempt_ids:
        assert post(attempt_id, text).status_code == 200


def add_analysis(attempt_id: str, text: str) -> str:
    repo = get_repository()
    analysis = analyze_attempt(
        repo.get_problem("P1"),
        StudentAttempt(
            attempt_id=attempt_id,
            student_id=repo.get_attempt(attempt_id).student_id,
            problem_id="P1",
            solution_text=text,
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
# 경계 — from 포함 / to 제외
# ===========================================================================

def test_from_is_inclusive():
    seed(["A1", "A2", "A3"])
    body = history(**{"from": stamp("A1").isoformat()}).json()
    assert body["total"] == 3
    assert "A1" in ids(body)


def test_to_is_exclusive():
    seed(["A1", "A2", "A3"])
    body = history(to=stamp("A1").isoformat()).json()
    assert body["total"] == 0
    assert ids(body) == []


def test_to_excludes_the_boundary_attempt_only():
    seed(["A1", "A2", "A3"])
    body = history(to=stamp("A2").isoformat()).json()
    assert ids(body) == ["A1"]


def test_half_open_interval():
    seed(["A1", "A2", "A3"])
    body = history(
        **{"from": stamp("A1").isoformat(), "to": stamp("A3").isoformat()}
    ).json()
    assert ids(body) == ["A2", "A1"]  # 정렬은 여전히 최신순
    assert body["total"] == 2


def test_exact_interval_containing_one_instant():
    seed(["A1", "A2", "A3"])
    start, end = stamp("A2"), stamp("A3")
    body = history(**{"from": start.isoformat(), "to": end.isoformat()}).json()
    assert ids(body) == ["A2"]


def test_empty_interval_is_empty_list_not_error():
    seed(["A1", "A2", "A3"])
    body = history(
        **{"from": stamp("A1").isoformat(), "to": stamp("A1").isoformat()}
    )
    # from == to 는 422 (아래 테스트). 여기서는 아주 좁은 구간으로 빈 결과 확인
    body = history(
        **{"from": (stamp("A1") + timedelta(microseconds=1)).isoformat(),
           "to": (stamp("A1") + timedelta(microseconds=2)).isoformat()}
    ).json()
    assert body["items"] == [] and body["total"] == 0


# ===========================================================================
# from / to 각각만
# ===========================================================================

def test_from_only():
    seed(["A1", "A2", "A3"])
    assert history(**{"from": stamp("A3").isoformat()}).json()["total"] == 1
    assert history(**{"from": stamp("A2").isoformat()}).json()["total"] == 2
    assert history(**{"from": (stamp("A3") + timedelta(seconds=1)).isoformat()}).json()["total"] == 0


def test_to_only():
    seed(["A1", "A2", "A3"])
    assert history(to=stamp("A3").isoformat()).json()["total"] == 2
    assert history(to=stamp("A1").isoformat()).json()["total"] == 0
    assert history(to=(stamp("A1") - timedelta(seconds=1)).isoformat()).json()["total"] == 0


def test_both_omitted_is_whole_period():
    seed(["A1", "A2", "A3"])
    body = history().json()
    assert body["total"] == 3
    assert ids(body) == ["A3", "A2", "A1"]


def test_future_from_returns_empty():
    seed(["A1", "A2"])
    far = (stamp("A2") + timedelta(days=365)).isoformat()
    assert history(**{"from": far}).json()["total"] == 0
    assert history(to=far).json()["total"] == 2


# ===========================================================================
# UTC offset 동등 시각
# ===========================================================================

def test_same_instant_in_different_offsets_is_equal():
    seed(["A1", "A2", "A3"])
    base = stamp("A1")
    for tz in (timezone.utc, KST, EST):
        value = base.astimezone(tz).isoformat()
        assert history(**{"from": value}).json()["total"] == 3, tz
        assert history(to=value).json()["total"] == 0, tz


def test_z_suffix_accepted():
    seed(["A1", "A2"])
    value = stamp("A1").isoformat().replace("+00:00", "Z")
    assert history(**{"from": value}).json()["total"] == 2
    assert history(to=value).json()["total"] == 0


def test_offset_boundary_math_is_correct():
    seed(["A1", "A2", "A3"])
    kst_start = stamp("A1").astimezone(KST)
    est_start = stamp("A1").astimezone(EST)
    # 두 표현은 같은 instant 이므로 결과가 같아야 한다
    assert history(**{"from": kst_start.isoformat()}).json()["total"] == (
        history(**{"from": est_start.isoformat()}).json()["total"]
    )


# ===========================================================================
# 422 — from >= to, offset 없음, 형식 오류
# ===========================================================================

@pytest.mark.parametrize("from_,to", [
    ("2026-09-28T10:00:00Z", "2026-09-28T09:00:00Z"),
    ("2026-09-28T10:00:00Z", "2026-09-28T10:00:00Z"),
    ("2026-09-28T10:00:00Z", "2026-09-28T10:00:00+00:00"),
    ("2026-09-28T19:00:00+09:00", "2026-09-28T10:00:00Z"),
])
def test_from_ge_to_is_422(from_, to):
    for url in (HISTORY_URL.format("S1"), TOP_URL.format("S1")):
        r = client.get(url, params={"from": from_, "to": to})
        assert r.status_code == 422, url
        assert r.json()["detail"]["code"] == "E_INVALID_TIME_RANGE", url


@pytest.mark.parametrize("key", ["from", "to"])
@pytest.mark.parametrize("value", [
    "2026-09-28T10:00:00",
    "2026-09-28",
    "2026-09-28 10:00:00",
    "not-a-date",
    "",
    "1756459200",
])
def test_invalid_instant_is_422(key, value):
    for url in (HISTORY_URL.format("S1"), TOP_URL.format("S1")):
        r = client.get(url, params={key: value})
        assert r.status_code == 422, (url, key, value)
        assert r.json()["detail"]["code"] == "E_INVALID_INSTANT", (url, key, value)


def test_range_validation_precedes_student_validation():
    r = client.get(HISTORY_URL.format("S1"), params={
        "from": "2026-09-28T10:00:00", "to": "2026-09-28T09:00:00Z"
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_INSTANT"


def test_student_id_validation_still_applies():
    """기간 필터와 student_id 검증이 함께 쓰여도 각자 422 코드를 낸다."""
    seed(["X1"])
    params = {"from": stamp("X1").isoformat()}
    r = client.get(HISTORY_URL.format("S1%01x"), params=params)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_STUDENT_ID"
    assert client.get(TOP_URL.format("S1%01x"), params=params).status_code == 422
    assert client.get(HISTORY_URL.format(" "), params=params).status_code == 422


# ===========================================================================
# history: 필터 후 정렬·total·페이지네이션
# ===========================================================================

def test_total_reflects_filtered_count():
    seed([f"B{i}" for i in range(7)])
    start, end = stamp("B1"), stamp("B4")
    body = history(
        page_size=100, **{"from": start.isoformat(), "to": end.isoformat()}
    ).json()
    assert body["total"] == 3
    assert body["total_pages"] == 1
    assert ids(body) == ["B3", "B2", "B1"]


def test_pagination_applies_after_filter():
    seed([f"C{i}" for i in range(7)])
    start, end = stamp("C0"), stamp("C4")
    pages = [
        ids(history(page=page, page_size=2, **{
            "from": start.isoformat(), "to": end.isoformat()
        }).json())
        for page in (1, 2, 3)
    ]
    assert pages == [["C3", "C2"], ["C1", "C0"], []]
    for body in (
        history(page=1, page_size=2, **{"from": start.isoformat(), "to": end.isoformat()}).json(),
        history(page=2, page_size=2, **{"from": start.isoformat(), "to": end.isoformat()}).json(),
    ):
        assert body["total"] == 4
        assert body["total_pages"] == 2


def test_page_beyond_filtered_range_keeps_filtered_total():
    seed([f"D{i}" for i in range(5)])
    body = history(page=9, **{"from": stamp("D0").isoformat()}).json()
    assert body["items"] == []
    assert body["total"] == 5
    assert body["page"] == 9


def test_sort_order_unchanged_inside_window():
    seed([f"E{i}" for i in range(5)])
    body = history(page_size=100, **{
        "from": stamp("E0").isoformat(), "to": stamp("E3").isoformat()
    }).json()
    assert ids(body) == ["E2", "E1", "E0"]


def test_display_metadata_preserved_inside_window():
    assert client.post(
        "/api/v1/attempts/F1/analyze",
        json={
            **BASE,
            "student_id": "S1",
            "solution_text": M21,
            "problem_question_text": "2(x-3)=6일 때 x의 값은?",
            "problem_concept_name": "분배법칙",
        },
    ).status_code == 200
    params = {"from": stamp("F1").isoformat()}
    body = history(**params).json()
    assert ids(body) == ["F1"]
    entry = body["items"][0]
    assert entry["concept_name"] == "분배법칙"
    assert entry["problem_text"] == "2(x-3)=6일 때 x의 값은?"
    assert entry["attempted_at"].endswith("Z") or entry["attempted_at"].endswith("+00:00")


# ===========================================================================
# TOP: 필터 후 distinct 집계·정렬
# ===========================================================================

def test_top_only_counts_attempts_in_range():
    for name in ("G1", "G2", "G3"):
        post(name, M21)
    for name in ("H1", "H2"):
        post(name, "2(x-3)=6\n2x-3=3\n2x=3")
    # H* 는 3.1, G* 는 2.1
    assert [(i["misconception_id"], i["attempt_count"]) for i in top().json()["items"]] == [
        ("2.1", 3), ("3.1", 2)
    ]
    body = top(**{"from": stamp("H1").isoformat()}).json()
    assert [(i["misconception_id"], i["attempt_count"]) for i in body["items"]] == [
        ("3.1", 2)
    ]


def test_top_distinct_count_within_window():
    for name in ("I1", "I2", "I3"):
        post(name, M21)
    post("I4", "2(x-3)=6\n2x-3=3\n2x=3")
    whole = top().json()
    windowed = top(**{"from": stamp("I1").isoformat()}).json()
    assert whole["distinct_misconception_count"] == 2
    assert windowed["distinct_misconception_count"] == 2
    windowed_all = top(**{"to": (stamp("I1") - timedelta(microseconds=1)).isoformat()}).json()
    assert windowed_all["distinct_misconception_count"] == 0
    assert windowed_all["items"] == []


def test_top_rank_respects_window_counts():
    for name in ("J1", "J2", "J3", "J4"):
        post(name, M21)
    for name in ("K1", "K2"):
        post(name, "2(x-3)=6\n2x-3=3\n2x=3")
    # from=J4 → J4 + K1 + K2 만 남음 (2.1:1건, 3.1:2건) → 3.1 이 1위
    body = top(**{"from": stamp("J4").isoformat()}).json()
    assert [(i["misconception_id"], i["attempt_count"], i["rank"]) for i in body["items"]] == [
        ("3.1", 2, 1), ("2.1", 1, 2)
    ]


def test_top_limit_applies_after_filter():
    ids_to_seed = ["1.1", "1.2", "2.1", "3.1"]
    for index, mid in enumerate(ids_to_seed):
        name = f"L{index}"
        post(name, OK)
        repo = get_repository()
        analysis = analyze_attempt(
            repo.get_problem("P1"),
            StudentAttempt(attempt_id=name, student_id="S1", problem_id="P1", solution_text=OK),
            solution=repo.get_correct_solution("SOL-1"),
        )
        repo.save_analysis(analysis.model_copy(update={"errors": [
            ErrorRecord(
                error_id=f"E{index}", attempt_id=name, step_no=1, error_type="concept_error",
                error_subtype="distribution_omit", skill_ids=["distribution"],
                misconception_ids=[mid], confidence=0.9, evidence_latex="2x-3=6",
                description_ko="fixture",
            )
        ]}))
    assert top().json()["distinct_misconception_count"] == 4
    assert len(top(**{"from": stamp("L2").isoformat()}).json()["items"]) == 2
    assert len(top(limit=1, **{"from": stamp("L0").isoformat()}).json()["items"]) == 1


def test_both_endpoints_agree_on_filter_membership():
    for name, text in (("M1", M21), ("M2", OK), ("M3", M21), ("M4", CALC)):
        post(name, text)
    start, end = stamp("M1"), stamp("M3")
    params = {"from": start.isoformat(), "to": end.isoformat()}
    # 구간 [M1, M3) 에는 M1(오답 2.1) 과 M2(정답) 만 들어 있다
    assert ids(history(**params).json()) == ["M2", "M1"]
    assert [(i["misconception_id"], i["attempt_count"]) for i in top(**params).json()["items"]] == [
        ("2.1", 1)
    ]
    # M3·M4 만 남는 구간 (M3=오답, M4=계산 오류)
    after = {"from": stamp("M3").isoformat()}
    assert ids(history(**after).json()) == ["M4", "M3"]
    assert [i["misconception_id"] for i in top(**after).json()["items"]] == ["2.1"]


def test_both_endpoints_share_boundary_semantics():
    for name in ("N1", "N2"):
        post(name, M21)
    for params in (
        {"from": stamp("N1").isoformat()},
        {"to": stamp("N1").isoformat()},
        {"from": stamp("N1").isoformat(), "to": stamp("N2").isoformat()},
    ):
        h_total = history(**params).json()["total"]
        t_ids = {i["misconception_id"] for i in top(**params).json()["items"]}
        if h_total == 0:
            assert t_ids == set()
        else:
            assert t_ids == {"2.1"}


# ===========================================================================
# 멱등 재요청과 기간 결과
# ===========================================================================

def test_idempotent_replay_does_not_duplicate_period_results():
    body1 = post("O1", M21).json()
    for _ in range(4):
        assert post("O1", M21).json()["analysis_id"] == body1["analysis_id"]
    start = stamp("O1").isoformat()
    hist = history(**{"from": start}).json()
    top_body = top(**{"from": start}).json()
    assert hist["total"] == 1
    assert hist["items"][0]["analysis_count"] == 1
    assert [(i["misconception_id"], i["attempt_count"]) for i in top_body["items"]] == [
        ("2.1", 1)
    ]
    assert len(get_repository().list_analyses("O1")) == 1


def test_latest_analysis_rule_holds_inside_window():
    post("P1", M21)
    add_analysis("P1", "2(x-3)=6\n2x-3=3\n2x=3")
    start = stamp("P1").isoformat()
    assert [i["misconception_id"] for i in top(**{"from": start}).json()["items"]] == ["3.1"]
    assert history(**{"from": start}).json()["items"][0]["analysis_count"] == 2


# ===========================================================================
# 읽기 전용
# ===========================================================================

def test_filtered_reads_do_not_change_state_or_storage():
    for index, text in enumerate([M21, M21, M31, CALC, OK]):
        post(f"Q{index}", text)
    rule = analyzer.get_engine("rule")
    bkt = analyzer.get_engine("bkt")
    repo = get_repository()
    before = (
        dict(rule._streak), dict(rule._state), dict(bkt._p),
        sorted(repo._analyses), sorted(repo._attempts), repo._seq,
    )
    params = {"from": stamp("Q0").isoformat(), "to": stamp("Q3").isoformat()}
    for _ in range(4):
        assert history(**params).status_code == 200
        assert top(**params).status_code == 200
        assert history().status_code == 200
        assert top().status_code == 200
    after = (
        dict(rule._streak), dict(rule._state), dict(bkt._p),
        sorted(repo._analyses), sorted(repo._attempts), repo._seq,
    )
    assert after == before


def test_filtered_reads_do_not_call_analyze_attempt(monkeypatch):
    from app import main as main_module

    post("R1", M21)

    def boom(*args, **kwargs):
        raise AssertionError("조회 중 분석이 실행되었다")

    monkeypatch.setattr(main_module, "analyze_attempt", boom)
    params = {"from": stamp("R1").isoformat()}
    assert history(**params).status_code == 200
    assert top(**params).status_code == 200


# ===========================================================================
# 회귀 — 필터 미지정 시 기존 동작
# ===========================================================================

def test_unfiltered_history_unchanged():
    for index in range(5):
        post(f"S{index}", M21 if index % 2 else OK)
    body = history().json()
    assert body["total"] == 5
    assert body["page"] == 1 and body["page_size"] == 20
    assert body["order"] == "attempted_at_desc"
    assert len(body["items"][0]) == 23
    assert ids(body) == ["S4", "S3", "S2", "S1", "S0"]


def test_unfiltered_top_unchanged():
    post("T1", M21)
    post("T2", M21)
    post("T3", "2(x-3)=6\n2x-3=3\n2x=3")
    body = top().json()
    assert set(body) == {
        "student_id", "limit", "order", "distinct_misconception_count", "items"
    }
    assert body["limit"] == 5
    assert body["order"] == "attempt_count_desc"
    assert [(i["misconception_id"], i["attempt_count"]) for i in body["items"]] == [
        ("2.1", 2), ("3.1", 1)
    ]


def test_paging_and_limit_validation_still_apply_with_filter():
    post("U1", M21)
    params = {"from": stamp("U1").isoformat()}
    assert history(page=0, **params).status_code == 422
    assert history(page_size=101, **params).status_code == 422
    assert top(limit=0, **params).status_code == 422
    assert top(limit=21, **params).status_code == 422


def test_attempt_and_detail_apis_have_no_time_filter():
    post("V1", M21)
    assert "from" not in client.get("/api/v1/attempts/V1/analyses").text
    body = client.get("/api/v1/attempts/V1/analyses").json()
    assert body["order"] == "created_asc"
    assert body["analysis_count"] == 1


def test_post_contracts_unchanged():
    assert len(post("W1", M21).json()) == 26
    assert len(client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    ).json()) == 16
    detail = client.get(
        "/api/v1/attempts/W1/analyses/"
        + post("W1", M21).json()["analysis_id"].replace("#", "%23")
    ).json()
    assert len(detail) == 27
    assert detail["attempted_at"] == post("W1", M21).json()["attempted_at"]


def test_routes_registered():
    paths = {r.path for r in app.routes}
    assert HISTORY_URL.format("{student_id}") in paths
    assert TOP_URL.format("{student_id}") in paths
    assert client.get("/health").json() == {"status": "ok"}
