"""PHASE 9-7.3 — 지연 저장의 동시 source-of-truth 보장.

배경
    PHASE 9-7.2 는 문제를 분석 **뒤에** 저장하도록 바꿨다. 그 결과 같은
    ``problem_id`` / ``solution_id`` 로 동시에 들어온 요청이 각각 자기 후보로
    분석·저장할 수 있었다 — Knowledge State 는 후보마다 다르게 반영되고,
    저장본은 먼저 도착한 쪽이 이긴다. 지연 저장 자체가 동시성 구멍을 만들었다.

해결
    리소스 ID 단위 락(``problem`` → ``solution`` 순서 획득)으로 resolve →
    source-of-truth 확인 → analyze → persist 구간을 직렬화한다.

검증 범위
  - 같은 problem_id 동시 신규 등록: 두 응답 모두 최종 저장된 Problem 기준
  - 같은 solution_id 동시 사용: 서로 다른 Problem 이면 성공 응답으로 나가지 않는다
  - 거절(Skill 계약) 시 Problem/CorrectSolution/Attempt/Analysis 미남김 + 복구
  - 파싱 오류 우선순위 유지
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.taxonomy import validate_all

PROBLEM_LATEX = "2(x-3)=6"
SOLUTION = "2(x-3)=6\n2x-6=6"


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


def _post(attempt_id, *, problem_id="P1", skills=("linear_equation",), text=SOLUTION,
          problem_latex=PROBLEM_LATEX, solution=None, student_id="S1"):
    body = {
        "problem_id": problem_id,
        "problem_latex": problem_latex,
        "problem_skills": list(skills),
        "student_id": student_id,
        "solution_text": text,
    }
    if solution is not None:
        body["correct_solution"] = solution
    # 스레드마다 별도 클라이언트를 쓴다(공유 앱 인스턴스는 동일)
    return TestClient(app).post(f"/api/v1/attempts/{attempt_id}/analyze", json=body)


def _run_concurrently(calls):
    """calls 를 동시에 시작시켜 실제로 경쟁하게 만든다.

    시작 전에 ``Barrier`` 로 맞춰서 두 요청이 사전 조회를 마친 뒤 분석하는
    상황을 재현한다.

    반환값은 ``[(attempt_id, response), ...]`` 다 — 어느 요청이 어떤 결과를
    받았는지 알 수 있어야 한다(승자/패자 판정이 경합에 의존하지 않아야 한다).
    """
    barrier = threading.Barrier(len(calls))

    def worker(index, kwargs):
        barrier.wait(timeout=10)
        return kwargs["attempt_id"], _post(**kwargs)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [
            pool.submit(worker, index, kwargs) for index, kwargs in enumerate(calls)
        ]
        return [f.result(timeout=60) for f in futures]


# ===========================================================================
# 1. 같은 problem_id 로 동시 신규 등록
# ===========================================================================

def test_concurrent_same_problem_id_both_use_final_stored_problem():
    """두 요청이 서로 다른 skill 선언으로 같은 problem_id 를 동시 등록한다.

    계약 (둘 중 하나여야 한다)
      (a) 둘 다 200 — 이때 **두 분석 모두 최종 저장된 Problem 기준**이어야 한다.
      (b) 한쪽만 200, 다른 쪽은 명확한 conflict — 이때 그쪽은 **분석도 상태 변경도
          없어야 한다** (Knowledge State 가 새면 안 된다).

    어느 경우든 최종 저장된 Problem 은 1건이고, Knowledge State 는 그 Problem 의
    tracking Skill 만 관측한 결과여야 한다.
    """
    results = _run_concurrently([
        {"attempt_id": "C1", "problem_id": "P-SHARED", "skills": ["linear_equation"]},
        {"attempt_id": "C2", "problem_id": "P-SHARED", "skills": ["distribution"]},
    ])
    codes = sorted(response.status_code for _, response in results)

    # 최종 저장된 Problem 은 1건이다
    problems = get_repository().list_problems()
    assert len(problems) == 1
    stored = problems[0]
    assert stored.problem_id == "P-SHARED"
    tracking = stored.tracking_skill_ids()
    assert len(tracking) == 1
    expected_skill = tracking[0]

    if codes == [200, 200]:
        # (a) 둘 다 최종 저장된 Problem 기준이다
        for attempt_id, response in results:
            body = response.json()
            assert body["skill"] == expected_skill, body["skill"]
            assert set(body["states"]) == {expected_skill}, body["states"]
            assert set(body["mastery_by_skill"]) == {expected_skill}
            assert len(get_repository().list_analyses(attempt_id)) == 1
    else:
        # (b) 한쪽은 명확한 conflict 를 받고 분석/상태 변경이 없어야 한다
        assert codes == [200, 409], codes
        for attempt_id, response in results:
            if response.status_code == 409:
                assert response.json()["detail"]["code"] in (
                    "E_RESOURCE_CONFLICT",
                    "E_SOLUTION_PROBLEM_MISMATCH",
                )
                assert get_repository().has_analysis(attempt_id) is False
            else:
                assert len(get_repository().list_analyses(attempt_id)) == 1

    # 어느 경우든 Knowledge State 는 최종 저장된 Problem 의 tracking Skill 만
    # 관측한 결과여야 한다 (경합으로 다른 후보가 새면 안 된다).
    rule = analyzer.get_engine("rule")
    observed = {
        key[1] for key in rule._streak if key[0] == "S1" and rule._streak[key] > 0
    }
    assert observed == {expected_skill}, observed


def test_concurrent_same_problem_id_same_skills_agree():
    """선언이 같은 두 동시 요청도 결과가 일관된다."""
    results = _run_concurrently([
        {"attempt_id": "D1", "problem_id": "P-SAME", "skills": ["linear_equation"]},
        {"attempt_id": "D2", "problem_id": "P-SAME", "skills": ["linear_equation"]},
    ])
    assert sorted(r.status_code for _, r in results) == [200, 200]
    bodies = [r.json() for _, r in results]
    assert bodies[0]["skill"] == bodies[1]["skill"]
    assert set(bodies[0]["states"]) == set(bodies[1]["states"])
    assert len(get_repository().list_problems()) == 1


def test_concurrent_same_problem_id_registers_problem_only_once():
    results = _run_concurrently([
        {"attempt_id": "E1", "problem_id": "P-ONCE", "skills": ["linear_equation"]},
        {"attempt_id": "E2", "problem_id": "P-ONCE", "skills": ["linear_equation"]},
        {"attempt_id": "E3", "problem_id": "P-ONCE", "skills": ["linear_equation"]},
    ])
    assert all(r.status_code == 200 for _, r in results)
    assert len(get_repository().list_problems()) == 1
    for attempt_id, _ in results:
        assert len(get_repository().list_analyses(attempt_id)) == 1


def test_concurrent_state_reflects_both_attempts_of_same_problem():
    """같은 문제의 두 시도는 각각 관측된다 (한 번으로 접히지 않는다)."""
    results = _run_concurrently([
        {"attempt_id": "F1", "problem_id": "P-STATE", "skills": ["linear_equation"]},
        {"attempt_id": "F2", "problem_id": "P-STATE", "skills": ["linear_equation"]},
    ])
    assert all(r.status_code == 200 for _, r in results)
    rule = analyzer.get_engine("rule")
    assert rule._streak[("S1", "linear_equation")] == 2


# ===========================================================================
# 2. 같은 solution_id 를 서로 다른 Problem 에 사용
# ===========================================================================

SOLUTION_PAYLOAD = {
    "solution_id": "SOL-X",
    "steps": [
        {"latex_text": "2(x-3)=6"},
        {"latex_text": "2x-6=6"},
        {"latex_text": "2x=12"},
        {"latex_text": "x=6"},
    ],
    "answer_latex": "x=6",
}


def test_same_solution_id_for_different_problem_is_rejected():
    """다른 문제 소속 정답 풀이로 분석이 성공 응답으로 나가면 안 된다."""
    first = _post(
        "G1", problem_id="P-SOL-OWNER", skills=["linear_equation"],
        solution=SOLUTION_PAYLOAD,
    )
    assert first.status_code == 200
    assert get_repository().get_correct_solution("SOL-X").problem_id == "P-SOL-OWNER"

    second = _post(
        "G2", problem_id="P-OTHER", skills=["linear_equation"],
        solution=SOLUTION_PAYLOAD,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "E_SOLUTION_PROBLEM_MISMATCH"
    # 거절된 요청은 아무것도 남기지 않는다
    assert get_repository().has_analysis("G2") is False
    assert len(get_repository().list_problems()) == 1


def test_concurrent_same_solution_id_different_problems_no_success_mismatch():
    """같은 solution_id 를 서로 다른 Problem 이 동시에 쓰면 성공 응답이 둘 다 나면 안 된다."""
    results = _run_concurrently([
        {
            "attempt_id": "H1", "problem_id": "P-RACE-A", "skills": ["linear_equation"],
            "solution": SOLUTION_PAYLOAD,
        },
        {
            "attempt_id": "H2", "problem_id": "P-RACE-B", "skills": ["linear_equation"],
            "solution": SOLUTION_PAYLOAD,
        },
    ])
    codes = sorted(r.status_code for _, r in results)
    assert 200 in codes, "한쪽은 정상 분석되어야 한다"
    assert 409 in codes, "다른 Problem 에서 같은 solution_id 를 쓰면 충돌이어야 한다"
    for attempt_id, response in results:
        if response.status_code == 409:
            assert response.json()["detail"]["code"] in (
                "E_SOLUTION_PROBLEM_MISMATCH",
                "E_RESOURCE_CONFLICT",
            )
            # 충돌한 쪽은 분석을 저장하지 않는다
            assert get_repository().has_analysis(attempt_id) is False
        else:
            assert len(get_repository().list_analyses(attempt_id)) == 1

    # 저장된 정답 풀이는 한 건이고, 그것이 등록된 Problem 에 속한다
    stored_solution = get_repository().get_correct_solution("SOL-X")
    assert len(get_repository().list_correct_solutions(stored_solution.problem_id)) == 1
    assert stored_solution.problem_id in ("P-RACE-A", "P-RACE-B")


def test_same_solution_id_same_problem_is_fine():
    """같은 문제에서 같은 solution_id 를 다시 쓰는 것은 정상이다 (멱등)."""
    first = _post("I1", problem_id="P-OK", skills=["linear_equation"],
                  solution=SOLUTION_PAYLOAD)
    second = _post("I2", problem_id="P-OK", skills=["linear_equation"],
                   solution=SOLUTION_PAYLOAD)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(get_repository().list_correct_solutions("P-OK")) == 1


# ===========================================================================
# 3. 거절(Skill 계약) 후 복구 — 동시 상황에서도 유지
# ===========================================================================

def test_concurrent_rejected_skill_request_leaves_nothing_and_recovers():
    """거절된 동시 요청은 아무것도 남기지 않고, 같은 ID 로 복구된다."""
    results = _run_concurrently([
        {"attempt_id": "J1", "problem_id": "P-RECOVER", "skills": []},
        {"attempt_id": "J2", "problem_id": "P-RECOVER", "skills": []},
    ])
    for attempt_id, response in results:
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
        assert get_repository().has_analysis(attempt_id) is False
    assert get_repository().list_problems() == []

    recovered = _post("J3", problem_id="P-RECOVER", skills=["linear_equation"])
    assert recovered.status_code == 200
    assert get_repository().get_problem("P-RECOVER").skills == ["linear_equation"]


def test_rejected_request_does_not_change_knowledge_state():
    before = analyzer.get_engine("rule").state("S1", "linear_equation")
    _post("K1", problem_id="P-NS", skills=[])
    after = analyzer.get_engine("rule").state("S1", "linear_equation")
    assert after == before


# ===========================================================================
# 4. 오류 우선순위 유지
# ===========================================================================

def test_parsing_error_still_takes_priority_in_concurrent_run():
    """거절된 요청도 파싱 오류가 우선한다 (동시 상황에서도)."""
    results = _run_concurrently([
        {"attempt_id": "L1", "problem_id": "P-PRI", "skills": [], "text": "???"},
        {"attempt_id": "L2", "problem_id": "P-PRI", "skills": ["linear_equation"]},
    ])
    codes = sorted(
        r.json()["detail"]["code"] for _, r in results if r.status_code != 200
    )
    assert "E_UNRECOGNIZED" in codes
    # 성공한 쪽만 문제를 등록한다
    assert len(get_repository().list_problems()) == 1


# ===========================================================================
# 5. 저장소 락 자체
# ===========================================================================

def test_resource_lock_is_reentrant_safe_across_threads():
    """리소스 락은 같은 ID 에서만 직렬화되고, 다른 ID 는 막지 않는다."""
    repo = get_repository()
    order: list[str] = []
    started = threading.Event()

    def worker(name: str, resource_id: str, wait_first: bool):
        if wait_first:
            started.wait(timeout=5)
        repo.claim_resource("problem", resource_id)
        order.append(name + ":in")
        started.set()
        order.append(name + ":out")
        repo.release_resource("problem", resource_id)

    t1 = threading.Thread(target=worker, args=("A", "P-A", True))
    t2 = threading.Thread(target=worker, args=("B", "P-B", False))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert len(order) == 4
    # 같은 ID 안에서만 직렬화된다
    assert order.index("A:in") < order.index("A:out")
    assert order.index("B:in") < order.index("B:out")


def test_resource_lock_serializes_same_id():
    """같은 ID 로 들어온 두 스레드는 겹치지 않는다."""
    repo = get_repository()
    active = []
    overlap = []
    guard = threading.Lock()

    def worker():
        repo.claim_resource("solution", "SOL-Z")
        with guard:
            if active:
                overlap.append(True)
            active.append(1)
        with guard:
            active.pop()
        repo.release_resource("solution", "SOL-Z")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert overlap == []


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