"""PHASE — 학생 Skill 숙련도 목록 API (명세 웹 r26/r27).

검증 범위:
  - 관측된 Skill 만 반환 (taxonomy 전체 나열·0 채우기 금지)
  - rule 엔진 상태 전이(정답 1회 learning 0.6 / 2회 mastered 0.85 / 개념오류 0.3)
  - bkt 엔진 state = 기존 임계값, engine 별 상태를 섞지 않음
  - 읽기 전용: 조회가 상태·LRU 를 바꾸지 않음
  - 그룹 노드(bkt_eligible=false) 제외, skill_id 오름차순
  - 입력 검증(engine/student_id)과 인증 guard
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integration.internal_auth import require_internal_auth
from app.main import app
from app.pipeline.analyzer import get_engine, reset_knowledge
from app.pipeline.repository import reset_repository

client = TestClient(app)
ANALYZE_URL = "/api/v1/attempts/{attempt_id}/analyze"
SKILLS_URL = "/api/v1/students/{student_id}/skills"


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    reset_repository()
    yield
    reset_knowledge()
    reset_repository()


def analyze(
    attempt_id: str,
    solution_text: str,
    *,
    student_id: str = "S1",
    problem_latex: str = "2x=6",
    problem_skills: list[str] | None = None,
    engine: str = "rule",
):
    payload = {
        "problem_id": "P1",
        "problem_latex": problem_latex,
        "problem_skills": problem_skills or ["linear_equation"],
        "student_id": student_id,
        "solution_text": solution_text,
        "engine": engine,
    }
    return client.post(ANALYZE_URL.format(attempt_id=attempt_id), json=payload)


def get_skills(student_id: str, engine: str | None = None):
    params = {} if engine is None else {"engine": engine}
    return client.get(SKILLS_URL.format(student_id=student_id), params=params)


def _items(body) -> list[dict]:
    return body["skills"]


# ===========================================================================
# 기본 응답 · 빈 목록
# ===========================================================================

def test_unknown_student_returns_empty_list():
    """1) 기록 없는 학생 → 200, skills=[], count=0 (404 아님)."""
    r = get_skills("NOBODY")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "student_id": "NOBODY",
        "engine": "rule",
        "skills": [],
        "count": 0,
    }


def test_correct_once_returns_learning_with_rule_mastery():
    """2) 정답 1회 → linear_equation, learning, 0.6."""
    analyzed = analyze("A1", "2x=6\nx=3")
    assert analyzed.status_code == 200
    r = get_skills("S1")
    assert r.status_code == 200
    body = r.json()
    assert body["student_id"] == "S1"
    assert body["engine"] == "rule"
    assert body["count"] == 1
    assert _items(body) == [
        {"skill_id": "linear_equation", "state": "learning", "mastery": 0.6}
    ]


def test_two_correct_attempts_returns_mastered():
    """3) 같은 학생 정답 2회(서로 다른 attempt) → mastered, 0.85."""
    assert analyze("A1", "2x=6\nx=3").status_code == 200
    assert analyze("A2", "2x=6\nx=3").status_code == 200
    body = get_skills("S1").json()
    assert body["count"] == 1
    assert _items(body) == [
        {"skill_id": "linear_equation", "state": "mastered", "mastery": 0.85}
    ]


def test_concept_error_returns_needs_practice():
    """4) concept_error 유발 → 해당 skill needs_practice, 0.3."""
    r = analyze(
        "A3",
        "2(x-3)=6\n2x-3=6",
        problem_latex="2(x-3)=6",
        problem_skills=["linear_equation", "distribution"],
    )
    assert r.status_code == 200
    assert r.json()["error_type"] == "concept_error"
    body = get_skills("S1").json()
    items = {item["skill_id"]: item for item in _items(body)}
    # 분류기는 concept_error 를 distribution 과 하위 skill 에 함께 귀속한다
    # (기존 분류 동작). 관측된 skill 만 목록에 나오는지 확인한다.
    assert items["distribution"] == {
        "skill_id": "distribution",
        "state": "needs_practice",
        "mastery": 0.3,
    }
    assert body["count"] == len(items)
    assert "linear_equation" not in items  # 관측되지 않은 선언 skill 은 없다


# ===========================================================================
# 엔진 선택 · 분리
# ===========================================================================

def test_bkt_engine_snapshot_matches_thresholds():
    """5) engine=bkt → mastery 0~1, state 가 기존 임계값과 일치."""
    assert analyze("B1", "2x=6\nx=3", student_id="SB", engine="bkt").status_code == 200
    r = get_skills("SB", engine="bkt")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "bkt"
    assert body["count"] == 1
    item = _items(body)[0]
    assert item["skill_id"] == "linear_equation"
    assert 0.0 <= item["mastery"] <= 1.0
    bkt = get_engine("bkt")
    expected_state = (
        "mastered"
        if item["mastery"] >= bkt.mastered_threshold
        else "learning"
        if item["mastery"] >= bkt.learning_threshold
        else "needs_practice"
    )
    assert item["state"] == expected_state
    # 엔진 분리: 같은 학생이라도 rule 엔진에는 관측이 없다
    rule_body = get_skills("SB", engine="rule").json()
    assert rule_body["skills"] == []
    assert rule_body["count"] == 0


def test_unknown_engine_is_422():
    """6) engine=unknown → 422 E_UNKNOWN_ENGINE."""
    r = get_skills("S1", engine="magic")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "E_UNKNOWN_ENGINE"


# ===========================================================================
# 입력 검증
# ===========================================================================

@pytest.mark.parametrize("student_id", ["%20%20", "x" * 65])
def test_invalid_student_id_is_422(student_id):
    """7) student_id 공백/64자 초과 → 422 E_INVALID_STUDENT_ID."""
    r = client.get(SKILLS_URL.format(student_id=student_id))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_STUDENT_ID"


# ===========================================================================
# 읽기 전용 · 정렬 · 그룹 노드
# ===========================================================================

def test_snapshot_is_read_only():
    """8) 조회 2회 값 동일 · tracked_students 불변 (LRU/상태 미변경)."""
    assert analyze("R1", "2x=6\nx=3").status_code == 200
    engine = get_engine("rule")
    tracked_before = engine.tracked_students()
    snapshot_before = engine.snapshot("S1")

    first = get_skills("S1")
    second = get_skills("S1")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert engine.tracked_students() == tracked_before
    assert engine.snapshot("S1") == snapshot_before
    # 관측이 없는 학생도 조회만으로 추적 대상에 들어가지 않는다
    assert get_skills("GHOST").json()["count"] == 0
    assert engine.tracked_students() == tracked_before


def test_group_node_is_never_included():
    """9) 그룹 노드(bkt_eligible=false)는 절대 포함하지 않는다."""
    # 분석 경로는 그룹 노드를 거절하므로 엔진에 직접 넣어 방어 필터를 검증한다.
    get_engine("rule").update("SG", "factoring", "correct")
    r = get_skills("SG")
    assert r.status_code == 200
    assert r.json()["skills"] == []
    assert r.json()["count"] == 0


def test_skills_are_sorted_ascending():
    """10) skill_id 오름차순 고정."""
    engine = get_engine("rule")
    for skill in ("transposition", "linear_equation", "arithmetic"):
        engine.update("SS", skill, "correct")
    body = get_skills("SS").json()
    assert [item["skill_id"] for item in _items(body)] == [
        "arithmetic",
        "linear_equation",
        "transposition",
    ]


# ===========================================================================
# 직렬화 · 인증
# ===========================================================================

def test_response_field_sets():
    """11) 응답/항목 필드 집합·직렬화 확인."""
    assert analyze("C1", "2x=6\nx=3").status_code == 200
    body = get_skills("S1").json()
    assert set(body) == {"student_id", "engine", "skills", "count"}
    assert set(body["skills"][0]) == {"skill_id", "state", "mastery"}
    assert isinstance(body["skills"][0]["mastery"], float)
    assert body["count"] == len(body["skills"])


def test_missing_auth_is_401(monkeypatch):
    """12) 인증 누락 → 401 (기존 guard 동작 확인)."""
    monkeypatch.setenv("AI1_INTERNAL_API_KEYS", "dummy-student-skills-key")
    app.dependency_overrides.pop(require_internal_auth, None)
    try:
        r = get_skills("S1")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "E_AUTH_INVALID_KEY"
        # 더미 키를 보내면 통과한다
        ok = client.get(
            SKILLS_URL.format(student_id="S1"),
            headers={"X-Internal-Api-Key": "dummy-student-skills-key"},
        )
        assert ok.status_code == 200
    finally:
        app.dependency_overrides[require_internal_auth] = lambda: None
