"""PHASE 9-7.4 — 동일 attempt_id 동시 충돌 검증.

배경
    본문 비교(``problem_id``/``student_id``/``solution_text``)가
    ``claim_analysis(attempt_id)`` 락 **밖**에서 이뤄졌다. 그래서 같은 attempt_id 로
    본문이 다른 요청이 동시에 들어오면 둘 다 사전 검사를 통과하고, 나중 요청이
    **다른 제출의 분석을 200 으로 돌려받는** 잘못된 응답이 났다.

해결
    ``claim_analysis`` 락을 획득한 뒤, 저장된 분석을 반환하기 **직전**에 저장본과
    요청을 다시 비교한다. 다르면 409 E_ATTEMPT_CONFLICT.

검증 범위
  - Barrier 를 ``claim_analysis()`` 진입에 두어 두 요청이 사전 Attempt 확인을 모두
    통과한 뒤 충돌하도록 경쟁을 재현한다
  - 같은 attempt_id + 다른 problem_id → 한쪽만 성공, 다른 쪽 409
  - 같은 attempt_id + 다른 풀이 본문 → 동시 충돌
  - 동일 본문 동시 재전송 → 200 멱등, 분석 1건·관측 1회
  - 충돌 요청은 분석 재실행·Knowledge State 갱신·이력 추가가 없다
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository

PROBLEM_LATEX = "2(x-3)=6"
SOLUTION = "2(x-3)=6\n2x-6=6"
OTHER_SOLUTION = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


def _post(attempt_id, *, problem_id="P1", skills=("linear_equation",), text=SOLUTION,
          problem_latex=PROBLEM_LATEX, student_id="S1"):
    body = {
        "problem_id": problem_id,
        "problem_latex": problem_latex,
        "problem_skills": list(skills),
        "student_id": student_id,
        "solution_text": text,
    }
    return TestClient(app).post(f"/api/v1/attempts/{attempt_id}/analyze", json=body)


def _gate_claim_analysis(monkeypatch, parties=2):
    """``claim_analysis()`` 진입에 배리어를 둔다.

    두 요청이 **사전 Attempt 확인을 모두 통과한 뒤** attempt 락 진입 시점에 만나도록
    만든다 — 이 지점이 아니면 충돌 재현이 되지 않는다.

    주의: 배리어에 도달하려면 problem 리소스 락을 이미 통과해야 한다. 따라서 이
    게이트는 **problem_id 가 서로 다른** 동시 요청에만 쓴다. 같은 problem_id 라면
    두 번째 요청이 problem 락에서 대기하다가 배리어에 못 닿아 교착한다.
    """
    repo = get_repository()
    original = repo.claim_analysis
    barrier = threading.Barrier(parties)

    def gated(attempt_id):
        barrier.wait(timeout=15)
        original(attempt_id)

    monkeypatch.setattr(repo, "claim_analysis", gated)
    return barrier


def _run(calls):
    """calls 를 동시에 시작한다 (HTTP 진입 전 배리어)."""
    barrier = threading.Barrier(len(calls))

    def worker(index, kwargs):
        barrier.wait(timeout=15)
        return kwargs["attempt_id"], _post(**kwargs)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [
            pool.submit(worker, index, kwargs) for index, kwargs in enumerate(calls)
        ]
        return [f.result(timeout=60) for f in futures]


# ===========================================================================
# 1. 같은 attempt_id + 다른 problem_id → 동시 충돌
# ===========================================================================

def test_concurrent_same_attempt_different_problem_id_conflicts(monkeypatch):
    """같은 attempt_id 로 다른 problem_id 를 동시에 → 한쪽만 성공, 다른 쪽 409."""
    _gate_claim_analysis(monkeypatch, parties=2)
    results = _run([
        {"attempt_id": "X1", "problem_id": "P-A", "problem_latex": "2(x-3)=6"},
        {"attempt_id": "X1", "problem_id": "P-B", "problem_latex": "3x+5=20"},
    ])
    codes = sorted(response.status_code for _, response in results)
    assert codes == [200, 409], codes
    for attempt_id, response in results:
        if response.status_code == 409:
            assert response.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
            # 충돌 요청은 분석을 저장하지 않는다
            assert get_repository().has_analysis(attempt_id) is True  # 승자의 분석 1건
            assert len(get_repository().list_analyses(attempt_id)) == 1
        else:
            assert response.json()["analysis_status"] in ("ANALYZED", "REVIEW_REQUIRED")


def test_concurrent_conflict_does_not_reanalyze_or_touch_state(monkeypatch):
    """충돌 요청은 분석을 재실행하지 않고 Knowledge State 를 추가로 갱신하지 않는다."""
    _gate_claim_analysis(monkeypatch, parties=2)
    results = _run([
        {"attempt_id": "Y1", "problem_id": "P-A", "skills": ["linear_equation"]},
        {"attempt_id": "Y1", "problem_id": "P-B", "skills": ["distribution"]},
    ])
    codes = sorted(response.status_code for _, response in results)
    assert codes == [200, 409], codes

    # 분석은 1건, 관측은 1회 (충돌 쪽은 아무것도 안 했다)
    records = get_repository().list_analyses("Y1")
    assert len(records) == 1
    assert len(records[0].analysis.observations) == 1

    # Knowledge State 는 승자의 문제 스킬만 관측한 결과다
    rule = analyzer.get_engine("rule")
    observed = {key[1] for key in rule._streak if rule._streak[key] > 0}
    assert len(observed) == 1, observed

    # 충돌 응답은 승자의 분석을 돌려주지 않는다 (분석 식별자 자체가 없다)
    loser = [r for _, r in results if r.status_code == 409][0]
    assert "analysis_id" not in loser.json()


# ===========================================================================
# 2. 같은 attempt_id + 다른 풀이 본문 → 동시 충돌
# ===========================================================================

def test_concurrent_same_attempt_different_solution_text_conflicts(monkeypatch):
    """같은 attempt_id 로 다른 풀이 본문을 동시에 → 409."""
    _gate_claim_analysis(monkeypatch, parties=2)
    results = _run([
        {
            "attempt_id": "Z1", "problem_id": "P-C", "text": SOLUTION,
            "problem_latex": "2(x-3)=6",
        },
        {
            "attempt_id": "Z1", "problem_id": "P-D", "text": OTHER_SOLUTION,
            "problem_latex": "2(x-3)=6",
        },
    ])
    codes = sorted(response.status_code for _, response in results)
    assert codes == [200, 409], codes
    loser = [r for _, r in results if r.status_code == 409][0]
    assert loser.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert len(get_repository().list_analyses("Z1")) == 1


def test_concurrent_same_attempt_same_problem_different_solution_conflicts():
    """같은 problem_id + 다른 풀이 본문 — problem 락이 순서를 정하고 충돌을 막는다."""
    results = _run([
        {"attempt_id": "Z2", "problem_id": "P-SAME", "text": SOLUTION},
        {"attempt_id": "Z2", "problem_id": "P-SAME", "text": OTHER_SOLUTION},
    ])
    codes = sorted(response.status_code for _, response in results)
    assert codes == [200, 409], codes
    loser = [r for _, r in results if r.status_code == 409][0]
    assert loser.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert len(get_repository().list_analyses("Z2")) == 1


def test_concurrent_same_attempt_different_student_id_conflicts(monkeypatch):
    """student_id 가 달라도 충돌이다 (저장본과 요청의 student_id 비교)."""
    _gate_claim_analysis(monkeypatch, parties=2)
    results = _run([
        {"attempt_id": "Z3", "problem_id": "P-E", "student_id": "S1"},
        {"attempt_id": "Z3", "problem_id": "P-F", "student_id": "S2"},
    ])
    codes = sorted(response.status_code for _, response in results)
    assert codes == [200, 409], codes
    loser = [r for _, r in results if r.status_code == 409][0]
    assert loser.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"


# ===========================================================================
# 3. 동일 본문 동시 재전송 → 멱등
# ===========================================================================

def test_concurrent_same_attempt_same_body_is_idempotent():
    """동일 본문 동시 재전송은 200 멱등 응답이며 분석 1건·관측 1회다."""
    results = _run([
        {"attempt_id": "W1", "problem_id": "P-IDEM", "text": SOLUTION},
        {"attempt_id": "W1", "problem_id": "P-IDEM", "text": SOLUTION},
    ])
    assert all(response.status_code == 200 for _, response in results)
    bodies = [response.json() for _, response in results]
    assert bodies[0]["analysis_id"] == bodies[1]["analysis_id"]
    assert bodies[0]["analysis_count"] == 1

    records = get_repository().list_analyses("W1")
    assert len(records) == 1
    assert len(records[0].analysis.observations) == 1

    rule = analyzer.get_engine("rule")
    assert rule._streak[("S1", "linear_equation")] == 1


def test_concurrent_same_attempt_same_body_many_clients():
    """많이 동시 재전송해도 분석은 1건이다."""
    calls = [
        {"attempt_id": "W2", "problem_id": "P-MANY", "text": SOLUTION} for _ in range(6)
    ]
    results = _run(calls)
    assert all(response.status_code == 200 for _, response in results)
    assert len({r.json()["analysis_id"] for _, r in results}) == 1
    assert len(get_repository().list_analyses("W2")) == 1
    rule = analyzer.get_engine("rule")
    assert rule._streak[("S1", "linear_equation")] == 1


# ===========================================================================
# 4. 순차 충돌 (기존 동작 유지)
# ===========================================================================

def test_sequential_conflict_still_409():
    first = _post("V1", problem_id="P-SEQ", text=SOLUTION)
    assert first.status_code == 200
    second = _post("V1", problem_id="P-SEQ", text=OTHER_SOLUTION)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert len(get_repository().list_analyses("V1")) == 1


def test_conflict_response_has_no_analysis_status():
    """충돌은 요청 오류다 — analysis_status 를 붙이지 않는다 (기존 계약)."""
    _post("V2", problem_id="P-SEQ2", text=SOLUTION)
    conflict = _post("V2", problem_id="P-SEQ2", text=OTHER_SOLUTION)
    assert conflict.status_code == 409
    assert "analysis_status" not in conflict.json()["detail"]


# ===========================================================================
# 5. 9-7.2 복구 동작 유지
# ===========================================================================

def test_rejected_skill_request_still_recovers_after_conflict_changes():
    """거절 후 복구 동작이 9-7.4 변경으로 깨지지 않는다."""
    rejected = _post("U1", problem_id="P-REC", skills=[])
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    assert get_repository().list_problems() == []

    recovered = _post("U1", problem_id="P-REC", skills=["linear_equation"])
    assert recovered.status_code == 200
    assert get_repository().get_problem("P-REC").skills == ["linear_equation"]


def test_resource_lock_order_preserved():
    """problem → solution 순서로 획득된다 (교착 방지 계약)."""
    import inspect

    from app import main as main_module

    source = inspect.getsource(main_module.analyze_attempt_endpoint)
    assert '_resource_locks(repo, ("problem", req.problem_id), ("solution", solution_id))' in source
    problem_pos = source.index('("problem", req.problem_id)')
    solution_pos = source.index('("solution", solution_id)')
    assert problem_pos < solution_pos