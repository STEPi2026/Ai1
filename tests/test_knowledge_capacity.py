"""S5 회귀 — Knowledge State 메모리/입력 방어.

in-memory MVP 구조는 유지하면서:
  - student_id 정책(문자열/공백 제거/비어있지 않음/64자/제어문자 금지)
  - 학생 수 상한(MAX_KNOWLEDGE_STUDENTS) + 학생 단위 LRU eviction
  - RLock 기반 최소한의 동시성 보호
  - invalid skill / group node가 state를 만들지 않는 invariant (S4 경계)
를 고정한다.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import (
    AnalysisError,
    InvalidStudentIdAnalysisError,
    analyze,
    get_engine,
    reset_knowledge,
)
from app.pipeline.knowledge import (
    MAX_KNOWLEDGE_STUDENTS,
    MAX_STUDENT_ID_LENGTH,
    BKTEngine,
    InvalidStudentIdError,
    RuleStateEngine,
    validate_student_id,
)
from app.taxonomy import validate_tracking_skill

client = TestClient(app)
SOLUTION = "2x=6\nx=3"


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


# ---------------------------------------------------------------------------
# A. 정상 student_id -> state 생성
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", ["S001", "student_001", "anonymous", "s4-inv", "golden-G01"])
def test_valid_student_ids_accepted(sid):
    assert validate_student_id(sid) == sid


def test_whitespace_is_trimmed():
    assert validate_student_id("  S001  ") == "S001"


def test_valid_student_creates_state():
    analyze("2x=6", SOLUTION, student_id="S001")
    assert ("S001", "linear_equation") in get_engine("rule")._state


# ---------------------------------------------------------------------------
# B/C. 빈 student_id / 과도하게 긴 student_id -> validation error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\t", "\n"])
def test_empty_student_id_rejected(bad):
    with pytest.raises(InvalidStudentIdError):
        validate_student_id(bad)


def test_too_long_student_id_rejected():
    with pytest.raises(InvalidStudentIdError):
        validate_student_id("S" * (MAX_STUDENT_ID_LENGTH + 1))


def test_max_length_student_id_accepted():
    sid = "S" * MAX_STUDENT_ID_LENGTH
    assert validate_student_id(sid) == sid


def test_control_character_rejected():
    with pytest.raises(InvalidStudentIdError):
        validate_student_id("S001\x00admin")


@pytest.mark.parametrize("bad", [None, 123, 4.5, ["S001"], {"id": "S001"}])
def test_non_string_student_id_rejected(bad):
    with pytest.raises(InvalidStudentIdError):
        validate_student_id(bad)


# ---------------------------------------------------------------------------
# API 레벨: E_INVALID_STUDENT_ID (422)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "S" * 200])
def test_api_rejects_invalid_student_id(bad):
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": SOLUTION, "student_id": bad},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_STUDENT_ID"


def test_api_error_is_analysis_error_subclass():
    assert issubclass(InvalidStudentIdAnalysisError, AnalysisError)
    assert InvalidStudentIdAnalysisError("x").code == "E_INVALID_STUDENT_ID"


def test_invalid_student_id_creates_no_state():
    for bad in ("", "   ", "S" * 200, "S\x0001"):
        with pytest.raises(AnalysisError):
            analyze("2x=6", SOLUTION, student_id=bad)
    assert get_engine("rule")._state == {}


# ---------------------------------------------------------------------------
# D/E. invalid skill / group node -> state 생성 안 됨 (S4 경계 재확인)
# ---------------------------------------------------------------------------

def test_invalid_skill_creates_no_state_via_analyze():
    for bad_skill in ("존재하지않는스킬ZZZ", "factoring", "polynomial_arithmetic"):
        with pytest.raises(AnalysisError):
            analyze("2x=6", SOLUTION, student_id="S002", problem_skill=bad_skill)
    assert get_engine("rule")._state == {}
    assert get_engine("rule")._streak == {}
    assert get_engine("bkt")._p == {}


def test_state_keys_only_contain_trackable_skills():
    analyze("2x=6", "2(x-3)=6\n2x-3=6", student_id="S003", problem_skill="linear_equation")
    for (_, skill) in get_engine("rule")._state:
        assert validate_tracking_skill(skill) == skill


def test_group_node_never_becomes_bkt_key():
    """group node(factoring)는 tracking 대상이므로 BKT 키가 될 수 없다."""
    with pytest.raises(AnalysisError):
        analyze("2x=6", SOLUTION, student_id="S004", problem_skill="factoring")
    assert all(skill != "factoring" for _, skill in get_engine("bkt")._p)


# ---------------------------------------------------------------------------
# F. 여러 student state 생성
# ---------------------------------------------------------------------------

def test_multiple_students_tracked_independently():
    for i in range(5):
        analyze("2x=6", SOLUTION, student_id=f"stu-{i}")
    tracked = set(get_engine("rule").tracked_students())
    assert tracked == {f"stu-{i}" for i in range(5)}
    assert len(get_engine("rule")._state) == 5


def test_state_is_per_student():
    analyze("2x=6", SOLUTION, student_id="a")  # learning
    analyze("2x=6", SOLUTION, student_id="b")
    analyze("2x=6", SOLUTION, student_id="a")  # mastered (2연속)
    rule = get_engine("rule")
    assert rule.state("a", "linear_equation") == "mastered"
    assert rule.state("b", "linear_equation") == "learning"


# ---------------------------------------------------------------------------
# G/H. 상한 + 학생 단위 eviction
# ---------------------------------------------------------------------------

def test_max_students_is_a_configuration_constant():
    assert isinstance(MAX_KNOWLEDGE_STUDENTS, int)
    assert MAX_KNOWLEDGE_STUDENTS >= 1000, "정상 사용자를 부당하게 제한하면 안 된다"


def test_lru_evicts_whole_students_at_limit():
    engine = RuleStateEngine(max_students=3)
    for i in range(3):
        engine.update(f"s{i}", "linear_equation", "correct")
    assert sorted(engine.tracked_students()) == ["s0", "s1", "s2"]

    engine.update("s3", "linear_equation", "correct")  # s0 eviction
    assert sorted(engine.tracked_students()) == ["s1", "s2", "s3"]
    # 학생 단위로 통째로 제거되어 부분 상태가 남지 않는다
    assert all(key[0] != "s0" for key in engine._state)
    assert all(key[0] != "s0" for key in engine._streak)


def test_lru_eviction_removes_all_skills_of_student():
    """evict는 개별 skill이 아니라 학생 단위여야 한다."""
    engine = RuleStateEngine(max_students=2)
    engine.update("old", "distribution", "correct")
    engine.update("old", "arithmetic", "correct")
    engine.update("old", "transposition", "correct")
    assert len([k for k in engine._state if k[0] == "old"]) == 3

    engine.update("n1", "linear_equation", "correct")
    engine.update("n2", "linear_equation", "correct")
    assert all(key[0] != "old" for key in engine._state), "학생 단위로 제거되지 않았다"
    assert all(key[0] != "old" for key in engine._streak)


def test_lru_refreshes_on_access():
    """최근 사용 학생은 제거되지 않는다 (사용 표시가 있어야 오래된 학생만 제거)."""
    engine = RuleStateEngine(max_students=2)
    engine.update("old", "linear_equation", "correct")
    engine.update("mid", "linear_equation", "correct")
    engine.update("old", "linear_equation", "correct")  # old를 최근 사용으로
    engine.update("new", "linear_equation", "correct")  # mid이 가장 오래됨 → 제거
    assert sorted(engine.tracked_students()) == ["new", "old"]


def test_bkt_engine_also_enforces_limit():
    engine = BKTEngine(max_students=2)
    for i in range(4):
        engine.update(f"b{i}", "linear_equation", "correct")
    assert len(engine.tracked_students()) == 2
    assert len(engine._p) == 2, "evict된 학생의 p_known이 남았다"


def test_default_engines_use_default_limit():
    """production singleton 엔진은 기본 상한을 사용한다 (테스트 전용 축소 아님)."""
    for name in ("rule", "bkt"):
        engine = get_engine(name)
        assert engine._lru._limit == MAX_KNOWLEDGE_STUDENTS


# ---------------------------------------------------------------------------
# I. reset 으로 테스트 간 state 격리
# ---------------------------------------------------------------------------

def test_reset_clears_all_state_and_lru():
    analyze("2x=6", SOLUTION, student_id="iso-1")
    reset_knowledge()
    assert get_engine("rule")._state == {}
    assert get_engine("rule")._streak == {}
    assert get_engine("rule").tracked_students() == []
    assert get_engine("bkt")._p == {}
    assert get_engine("bkt").tracked_students() == []


# ---------------------------------------------------------------------------
# J. 동시 update에서 state가 깨지지 않는가 (최소 수준 concurrency)
# ---------------------------------------------------------------------------

def test_concurrent_updates_keep_state_consistent():
    """동시 정답 업데이트 후 streak/state가 손실 없이 누적되어야 한다."""
    engine = RuleStateEngine()
    threads, per_thread = 8, 400

    def worker():
        for _ in range(per_thread):
            engine.update("race", "linear_equation", "correct")

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert engine._streak[("race", "linear_equation")] == threads * per_thread
    assert engine.state("race", "linear_equation") == "mastered"
    assert engine.tracked_students() == ["race"]


def test_concurrent_multi_student_updates_do_not_corrupt_keys():
    engine = RuleStateEngine(max_students=50)
    def worker(n: int):
        for _ in range(50):
            engine.update(f"c{n}", "linear_equation", "correct")
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(engine._state) == 10
    assert all(k[1] == "linear_equation" for k in engine._state)
    assert all(v == "mastered" for v in engine._state.values())


def test_concurrent_bkt_updates_stay_in_bounds():
    engine = BKTEngine()
    def worker():
        for _ in range(200):
            engine.update("race-bkt", "linear_equation", "correct")
    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    p = engine._p[("race-bkt", "linear_equation")]
    assert 0.0 <= p <= 1.0
    assert len(engine._p) == 1


def test_reset_is_safe_while_no_updates_in_flight():
    reset_knowledge()
    assert get_engine("rule")._state == {}
    analyze("2x=6", SOLUTION, student_id="after-reset")
    assert len(get_engine("rule")._state) == 1
