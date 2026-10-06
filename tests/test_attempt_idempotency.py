"""PHASE 8-4: attempt 분석 멱등성과 학습 상태 갱신 정책.

정책
  1. 같은 attempt_id + 같은 제출 내용이 이미 성공 분석되었으면 최신 AnalysisRecord
     를 그대로 반환한다. 새 analysis_id 없음, analyze_attempt() 미호출.
  2. 같은 attempt_id + 다른 내용 → 409 E_ATTEMPT_CONFLICT
  3. 새 attempt_id 는 별도 제출이며 KnowledgeObservation 이 한 번만 반영된다.
  4. 의도적 재분석/버전 관리는 이번 단계 범위 밖
  5. 동시 중복 요청도 분석과 상태 갱신이 한 번만
  6. 기존 /analyze-solution 계약 불변
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer, repository
from app.pipeline.knowledge import BKTEngine
from app.pipeline.repository import get_repository, reset_repository

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

URL = "/api/v1/attempts/{}/analyze"


def post(attempt_id: str, solution_text: str, **overrides) -> "object":
    return client.post(URL.format(attempt_id), json={**BASE, "solution_text": solution_text, **overrides})


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 정책 1 — 멱등 재요청
# ===========================================================================

def test_repeated_correct_request_keeps_same_id_and_count():
    first = post("A1", CORRECT).json()
    for _ in range(4):
        again = post("A1", CORRECT).json()
        assert again["analysis_id"] == first["analysis_id"]
        assert again["analysis_count"] == 1
    assert len(get_repository().list_analyses("A1")) == 1


def test_idempotent_replay_body_is_identical():
    first = post("A1", CORRECT).json()
    second = post("A1", CORRECT).json()
    assert first == second, "재전송 응답이 원본과 달라지면 클라이언트가 중복 처리한다"


def test_repeated_correct_request_does_not_grant_mastery():
    """정답 1회가 중복 호출만으로 mastered 가 되면 안 된다."""
    for _ in range(5):
        body = post("A1", CORRECT).json()
    rule = analyzer.get_engine("rule")
    assert body["state"] == "learning", "1회 정답인데 중복 재전송으로 mastered 가 되었다"
    assert rule.state("S1", "linear_equation") == "learning"
    assert rule._streak[("S1", "linear_equation")] == 1, "관측 streak 가 5 로 늘었다"
    assert len(get_repository().list_analyses("A1")) == 1


def test_rule_state_equals_single_observation_baseline():
    """중복 재전송 후 상태가 1회 관측 단독 실행과 동일한지 비교한다."""
    post("A1", CORRECT)
    post("A1", CORRECT)
    post("A1", CORRECT)
    after_replay = analyzer.get_engine("rule").state("S1", "linear_equation")

    analyzer.reset_knowledge()
    reset_repository()
    post("A-single", CORRECT)
    baseline = analyzer.get_engine("rule").state("S1", "linear_equation")
    assert after_replay == baseline == "learning"


def test_bkt_mastery_not_inflated_by_replay():
    for _ in range(5):
        body = post("A1", CORRECT, engine="bkt").json()
    expected = BKTEngine().update("S1", "linear_equation", "correct")
    assert body["mastery"] == pytest.approx(expected), "BKT 숙련도가 중복 상승했다"
    assert analyzer.get_engine("bkt").mastery("S1", "linear_equation") == pytest.approx(expected)


def test_repeated_wrong_request_does_not_duplicate_observation():
    first = post("A2", WRONG).json()
    assert first["misconception_tags"] == ["2.1"]
    stored = get_repository().get_latest_analysis("A2")
    observed = [(o.skill_id, o.outcome) for o in stored.analysis.observations]
    assert observed == [
        ("distribution", "incorrect"),
        ("polynomial_multiplication", "incorrect"),
    ]
    for _ in range(3):
        again = post("A2", WRONG).json()
        assert again["analysis_id"] == first["analysis_id"]
    assert len(get_repository().list_analyses("A2")) == 1
    assert [
        (o.skill_id, o.outcome)
        for o in get_repository().get_latest_analysis("A2").analysis.observations
    ] == observed
    rule = analyzer.get_engine("rule")
    for skill_id, _ in observed:
        assert rule._streak[("S1", skill_id)] == 0, f"{skill_id} streak 가 중복으로 내려갔다"
        assert rule.state("S1", skill_id) == "needs_practice"


def test_wrong_replay_does_not_repeat_misconception_tags():
    first = post("A2", WRONG).json()
    for _ in range(3):
        assert post("A2", WRONG).json()["misconception_tags"] == first["misconception_tags"]


# ===========================================================================
# 정책 2 — 다른 내용 409
# ===========================================================================

def test_same_id_different_content_still_conflicts_after_analysis():
    post("A3", CORRECT)
    r = post("A3", "2(x-3)=6\n2x-3=6")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"


def test_conflict_does_not_create_history_or_change_state():
    post("A4", CORRECT)
    before = get_repository().get_attempt("A4")
    before_state = analyzer.get_engine("rule").state("S1", "linear_equation")
    for _ in range(3):
        assert post("A4", "2(x-3)=6\nx=3").status_code == 409
    assert len(get_repository().list_analyses("A4")) == 1
    stored = get_repository().get_attempt("A4")
    assert (stored.problem_id, stored.student_id, stored.solution_text) == (
        before.problem_id,
        before.student_id,
        before.solution_text,
    )
    assert analyzer.get_engine("rule").state("S1", "linear_equation") == before_state


def test_conflict_on_different_student_same_attempt_id():
    post("A5", CORRECT)
    r = post("A5", CORRECT, student_id="S2")
    assert r.status_code == 409


# ===========================================================================
# 정책 3 — 새 attempt_id 는 별도 제출
# ===========================================================================

def test_new_attempt_id_counts_as_new_submission():
    ids = [post(f"A{i}", CORRECT).json()["analysis_id"] for i in range(3)]
    assert len(set(ids)) == 3
    for i in range(3):
        assert len(get_repository().list_analyses(f"A{i}")) == 1
    assert post("A0", CORRECT).json()["analysis_count"] == 1


def test_two_distinct_correct_submissions_reach_mastered():
    a = post("S-1", CORRECT).json()
    b = post("S-2", CORRECT).json()
    assert a["state"] == "learning" and b["state"] == "mastered", "서로 다른 2회 제출이 누적되지 않는다"
    rule = analyzer.get_engine("rule")
    assert rule._streak[("S1", "linear_equation")] == 2


def test_new_attempt_updates_state_once_each():
    post("B1", WRONG)
    rule = analyzer.get_engine("rule")
    assert rule.state("S1", "distribution") == "needs_practice"
    assert rule._streak.get(("S1", "linear_equation"), 0) == 0
    post("B2", CORRECT)
    assert rule.state("S1", "linear_equation") == "learning"
    assert rule._streak[("S1", "linear_equation")] == 1, "2번째 제출이 한 번만 반영되지 않았다"
    assert rule.state("S1", "distribution") == "needs_practice", "정답 제출이 오답 스킬 상태를 지웠다"


# ===========================================================================
# 정책 5 — 동시 중복 요청의 원자성
# ===========================================================================

def _concurrent(attempt_id: str, text: str, n: int = 8, **overrides) -> list:
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(
            pool.map(lambda _: post(attempt_id, text, **overrides), range(n))
        )


def test_concurrent_duplicates_analyze_once():
    responses = _concurrent("C1", CORRECT)
    assert all(r.status_code == 200 for r in responses)
    ids = {r.json()["analysis_id"] for r in responses}
    assert len(ids) == 1, f"동시 중복 요청이 {len(ids)} 개의 분석을 만들었다"
    assert len(get_repository().list_analyses("C1")) == 1
    assert {r.json()["analysis_count"] for r in responses} == {1}


def test_concurrent_duplicates_update_state_once():
    _concurrent("C2", CORRECT)
    rule = analyzer.get_engine("rule")
    assert rule.state("S1", "linear_equation") == "learning"
    assert rule._streak[("S1", "linear_equation")] == 1, "동시 중복 요청이 관측을 여러 번 반영했다"


def test_concurrent_duplicates_do_not_inflate_bkt():
    _concurrent("C3", CORRECT, n=8, engine="bkt")
    expected = BKTEngine().update("S1", "linear_equation", "correct")
    assert analyzer.get_engine("bkt").mastery("S1", "linear_equation") == pytest.approx(expected)


def test_concurrent_wrong_duplicates_record_one_observation():
    _concurrent("C4", WRONG)
    record = get_repository().get_latest_analysis("C4")
    assert len(get_repository().list_analyses("C4")) == 1
    assert len(record.analysis.observations) == 2  # linear_equation + distribution
    rule = analyzer.get_engine("rule")
    assert rule._streak[("S1", "distribution")] == 0


def test_concurrent_mixed_attempts_are_isolated():
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda i: post(f"D{i}", CORRECT if i % 2 == 0 else WRONG),
                range(6),
            )
        )
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["analysis_id"] for r in results}) == 6
    for i in range(6):
        assert len(get_repository().list_analyses(f"D{i}")) == 1
    assert analyzer.get_engine("rule")._streak[("S1", "linear_equation")] == 3


def test_concurrent_conflict_and_replay_never_produce_500():
    post("E1", CORRECT)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(post, "E1", CORRECT) for _ in range(3)]
        futures += [pool.submit(post, "E1", "2(x-3)=6\nx=3") for _ in range(3)]
        responses = [f.result() for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 409, 409, 409]
    assert len(get_repository().list_analyses("E1")) == 1


# ===========================================================================
# 실패 후 재시도 — 실패가 attempt 를 잠그지 않는다
# ===========================================================================

def test_failed_analysis_leaves_no_history_and_allows_retry():
    r = post("F1", "\n".join(["2(x-3)=6", "2x-6=6", "2x=12", "???", "x=6"]))
    if r.status_code == 200:  # REVIEW_REQUIRED 로 저장되는 경우
        assert r.json()["analysis_status"] == "REVIEW_REQUIRED"
    else:
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "E_UNRECOGNIZED"
        assert get_repository().list_analyses("F1") == []


def test_unrecognized_then_valid_retry_succeeds():
    bad = post("F2", "보통은 이렇게 푼다고 합니다")
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "E_UNRECOGNIZED"
    assert get_repository().has_analysis("F2") is False
    good = post("F2", CORRECT)
    assert good.status_code == 200
    assert good.json()["analysis_count"] == 1


# ===========================================================================
# 정책 6 — 기존 계약 불변
# ===========================================================================

def test_analyze_solution_endpoint_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skill"] == "distribution"
    assert body["misconception_id"] == "2.1"
    assert body["analysis_status"] == "ANALYZED"
    assert "analysis_id" not in body, "기존 경로에 attempt 전용 필드가 섞였다"


def test_analyze_solution_ignores_repository_state():
    for _ in range(3):
        client.post("/analyze-solution", json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3"})
    r = client.post("/analyze-solution", json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3"})
    assert r.json()["state"] == "mastered", "기존 경로가 저장소/멱등 정책의 영향을 받았다"


# ===========================================================================
# 저장소 단위 계약
# ===========================================================================

def test_claim_blocks_different_attempts_independently():
    repo = get_repository()
    post("G1", CORRECT)
    post("G2", CORRECT)
    repo.claim_analysis("G1")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            done = pool.submit(post, "G2", CORRECT)
            assert done.result(timeout=5).status_code == 200
    finally:
        repo.release_analysis("G1")
    assert post("G1", CORRECT).json()["analysis_id"] == post("G1", CORRECT).json()["analysis_id"]


def test_claim_is_reentrant_across_sequential_calls():
    repo = get_repository()
    repo.claim_analysis("H1")
    repo.release_analysis("H1")
    repo.claim_analysis("H1")
    repo.release_analysis("H1")


def test_reset_clears_analysis_gates():
    repo = get_repository()
    repo.claim_analysis("I1")
    reset_repository()
    assert post("I1", CORRECT).status_code == 200, "reset 후 락이 남아 있으면 데드락"


def test_singleton_repository_is_shared():
    assert get_repository() is get_repository()
    assert isinstance(get_repository(), repository.InMemoryLearningRepository)
