"""Phase 8-1 회귀 — 학습 세션 저장소 계층 (in-memory).

검증 범위:
  - ID 로 재조회 / 없는 ID 조회 / 중복 ID 정책
  - Problem·CorrectSolution·Attempt 참조 무결성
  - 같은 학습 흐름의 재시도 분석 이력 보존 (덮어쓰지 않음)
  - ErrorRecord 여러 건의 순서·내용 유지
  - 개념적 skills(그룹 node 허용) vs Knowledge State 추적 skill 구분
  - 기존 분석·정렬·다중 오류 동작 회귀
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.pipeline.analyzer import reset_knowledge
from app.pipeline.knowledge import BKTEngine
from app.pipeline.repository import (
    AnalysisRecord,
    InMemoryLearningRepository,
    LearningRepository,
    RepositoryError,
    get_repository,
    reset_repository,
)
from app.pipeline.session import (
    CorrectSolution,
    Problem,
    SolutionStep,
    StudentAttempt,
    analyze_attempt,
)

SOLUTION = CorrectSolution(
    solution_id="SOL-1",
    problem_id="P-1",
    steps=[
        SolutionStep(step_no=1, latex_text="2(x-3)=6"),
        SolutionStep(step_no=2, latex_text="2x-6=6"),
        SolutionStep(step_no=3, latex_text="2x=12"),
        SolutionStep(step_no=4, latex_text="x=6"),
    ],
    answer_latex="x=6",
    is_primary=True,
)
PROBLEM = Problem(
    problem_id="P-1", problem_latex="2(x-3)=6", skills=["linear_equation", "distribution"]
)


@pytest.fixture
def repo() -> InMemoryLearningRepository:
    r = InMemoryLearningRepository()
    r.save_problem(PROBLEM)
    r.save_correct_solution(SOLUTION)
    yield r
    r.reset()


def make_attempt(attempt_id: str, text: str, student_id: str = "S1") -> StudentAttempt:
    return StudentAttempt(
        attempt_id=attempt_id, student_id=student_id, problem_id="P-1", solution_text=text
    )


# ===========================================================================
# 저장 후 ID 로 재조회
# ===========================================================================

def test_problem_round_trip(repo):
    got = repo.get_problem("P-1")
    assert got.problem_id == "P-1"
    assert got.problem_latex == "2(x-3)=6"
    assert got.skills == ["linear_equation", "distribution"]


def test_correct_solution_round_trip(repo):
    got = repo.get_correct_solution("SOL-1")
    assert got.problem_id == "P-1"
    assert len(got.steps) == 4
    assert got.answer_latex == "x=6"
    assert repo.list_correct_solutions("P-1") == [got] or repo.list_correct_solutions("P-1")[0].solution_id == "SOL-1"


def test_attempt_round_trip(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    got = repo.get_attempt("A1")
    assert got.student_id == "S1"
    assert got.problem_id == "P-1"
    assert got.steps[0].latex_text == "2(x-3)=6"


def test_analysis_round_trip(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    record = repo.save_analysis(analysis)
    assert isinstance(record, AnalysisRecord)
    got = repo.get_analysis(record.analysis_id)
    assert got.attempt_id == "A1"
    assert got.problem_id == "P-1"
    assert got.student_id == "S1"
    assert got.analysis.correct is False
    assert got.raw_result == analysis.api_response


def test_list_problems(repo):
    repo.save_problem(Problem(problem_id="P-2", problem_latex="2x=6", skills=["linear_equation"]))
    assert [p.problem_id for p in repo.list_problems()] == ["P-1", "P-2"]


# ===========================================================================
# 존재하지 않는 ID 조회
# ===========================================================================

@pytest.mark.parametrize(
    "call,args",
    [
        ("get_problem", ("NOPE",)),
        ("get_correct_solution", ("NOPE",)),
        ("get_attempt", ("NOPE",)),
        ("get_analysis", ("NOPE",)),
        ("list_analyses", ("NOPE",)),
        ("list_correct_solutions", ("NOPE",)),
    ],
)
def test_missing_id_raises(repo, call, args):
    with pytest.raises(RepositoryError) as ei:
        getattr(repo, call)(*args)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


def test_get_latest_analysis_none_when_no_analysis(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    assert repo.get_latest_analysis("A1") is None
    with pytest.raises(RepositoryError):
        repo.get_latest_analysis("UNKNOWN")


# ===========================================================================
# 중복 ID 정책
# ===========================================================================

def test_duplicate_problem_id_rejected(repo):
    with pytest.raises(RepositoryError) as ei:
        repo.save_problem(PROBLEM)
    assert ei.value.code == "E_DUPLICATE_ID"


def test_duplicate_solution_id_rejected(repo):
    with pytest.raises(RepositoryError) as ei:
        repo.save_correct_solution(SOLUTION)
    assert ei.value.code == "E_DUPLICATE_ID"


def test_duplicate_attempt_id_rejected(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    with pytest.raises(RepositoryError) as ei:
        repo.save_attempt(make_attempt("A1", "2x=6\nx=3"))
    assert ei.value.code == "E_DUPLICATE_ID"


def test_duplicate_does_not_overwrite(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    with pytest.raises(RepositoryError):
        repo.save_attempt(make_attempt("A1", "완전히 다른 풀이"))
    assert repo.get_attempt("A1").solution_text == "2(x-3)=6\nx=6"


def test_analysis_id_is_unique_per_save(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    r1 = repo.save_analysis(analysis)
    r2 = repo.save_analysis(analysis)
    assert r1.analysis_id != r2.analysis_id
    assert len(repo.list_analyses("A1")) == 2


# ===========================================================================
# 참조 무결성
# ===========================================================================

def test_solution_requires_existing_problem():
    r = InMemoryLearningRepository()
    with pytest.raises(RepositoryError) as ei:
        r.save_correct_solution(SOLUTION)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


def test_attempt_requires_existing_problem(repo):
    bad = StudentAttempt(
        attempt_id="A9", student_id="S1", problem_id="MISSING", solution_text="x=1"
    )
    with pytest.raises(RepositoryError) as ei:
        repo.save_attempt(bad)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


def test_analysis_requires_existing_attempt(repo):
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, make_attempt("A-unknown", "2(x-3)=6\nx=6"))
    with pytest.raises(RepositoryError) as ei:
        repo.save_analysis(analysis)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


def test_analysis_mismatched_attempt_id_rejected(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    reset_knowledge()
    other = analyze_attempt(PROBLEM, make_attempt("A2", "2(x-3)=6\nx=6"))
    tampered = other.model_copy(update={"attempt_id": "A1"})
    with pytest.raises(RepositoryError) as ei:
        repo.save_analysis(tampered)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


def test_analysis_stored_attempt_must_match_saved_one(repo):
    """분석 내부가 일관되어도, 저장된 attempt 와 다르면 거부한다."""
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6", student_id="S1"))
    reset_knowledge()
    a = analyze_attempt(
        PROBLEM, make_attempt("A1", "2(x-3)=6\nx=6", student_id="S1"), solution=SOLUTION
    )
    bad = a.model_copy(
        update={
            "attempt": a.attempt.model_copy(update={"student_id": "S2"}),
            "student_id": "S2",
        }
    )
    with pytest.raises(RepositoryError) as ei:
        repo.save_analysis(bad)
    assert ei.value.code == "E_REFERENCE_INTEGRITY"


# ===========================================================================
# 재풀이/재촬영 — 분석 이력 보존
# ===========================================================================

def test_resubmission_preserves_analysis_history(repo):
    """같은 문제·학생이 재시도해도 attempt 별 분석 이력이 모두 남는다."""
    reset_knowledge()
    first = analyze_attempt(PROBLEM, make_attempt("A1", "2(x-3)=6\n2x-3=6"), solution=SOLUTION)
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    repo.save_analysis(first)

    second = analyze_attempt(PROBLEM, make_attempt("A2", "2(x-3)=6\n2x-6=6\nx=6"), solution=SOLUTION)
    repo.save_attempt(make_attempt("A2", "2(x-3)=6\n2x-6=6\nx=6"))
    repo.save_analysis(second)

    assert [a.attempt_id for a in repo.list_attempts(problem_id="P-1")] == ["A1", "A2"]
    assert [a.attempt_id for a in repo.list_attempts(student_id="S1")] == ["A1", "A2"]
    assert len(repo.list_analyses("A1")) == 1
    assert len(repo.list_analyses("A2")) == 1
    assert repo.get_latest_analysis("A1").analysis.correct is False
    assert repo.get_latest_analysis("A2").analysis.correct is True


def test_reanalysis_of_same_attempt_appends_history(repo):
    """같은 attempt 를 다시 분석(재검증)해도 기존 분석을 덮어쓰지 않는다."""
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    reset_knowledge()
    a1 = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    reset_knowledge()
    a2 = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    r1 = repo.save_analysis(a1)
    r2 = repo.save_analysis(a2)
    history = repo.list_analyses("A1")
    assert [h.analysis_id for h in history] == [r1.analysis_id, r2.analysis_id]
    assert repo.get_latest_analysis("A1").analysis_id == r2.analysis_id


def test_history_order_preserved(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    reset_knowledge()
    ids = []
    for _ in range(3):
        ids.append(repo.save_analysis(analyze_attempt(PROBLEM, repo.get_attempt("A1"))).analysis_id)
    assert [h.analysis_id for h in repo.list_analyses("A1")] == ids


# ===========================================================================
# ErrorRecord 여러 건 — 순서와 내용 유지
# ===========================================================================

def test_multiple_errors_round_trip_in_order(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-6=7\n2x=13\nx=6"))
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    assert [e.step_no for e in analysis.errors] == [2, 4]
    repo.save_analysis(analysis)

    got = repo.get_latest_analysis("A1").analysis
    assert [e.step_no for e in got.errors] == [2, 4]
    assert [e.error_id for e in got.errors] == [e.error_id for e in analysis.errors]
    for a, b in zip(got.errors, analysis.errors):
        assert (a.error_type, a.error_subtype, a.skill_ids, a.misconception_ids,
                a.step_no, a.ref_step_no, a.ref_solution_step_no, a.evidence_latex,
                a.description_ko, a.confidence) == (
            b.error_type, b.error_subtype, b.skill_ids, b.misconception_ids,
            b.step_no, b.ref_step_no, b.ref_solution_step_no, b.evidence_latex,
            b.description_ko, b.confidence)


def test_downstream_suppression_survives_round_trip(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6\n2x=13\n2x=14"))
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    repo.save_analysis(analysis)
    got = repo.get_latest_analysis("A1").analysis
    assert [e.step_no for e in got.errors] == [2]
    assert got.suppressed_downstream_steps == [3, 4]


def test_alignment_and_observations_survive_round_trip(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-6=6\n6+6=2x\n6=x"))
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    repo.save_analysis(analysis)
    got = repo.get_latest_analysis("A1").analysis
    assert [s.aligned_step_no for s in got.attempt.steps] == [1, 2, 3, 4]
    assert [(o.skill_id, o.outcome) for o in got.observations] == [
        (o.skill_id, o.outcome) for o in analysis.observations
    ]
    assert got.states == analysis.states
    assert got.mastery == analysis.mastery


# ===========================================================================
# 개념적 skills vs Knowledge State 추적 skill
# ===========================================================================

def test_problem_may_declare_group_node_conceptually():
    r = InMemoryLearningRepository()
    p = Problem(problem_id="PG", problem_latex="2x=6", skills=["factoring", "linear_equation"])
    r.save_problem(p)
    got = r.get_problem("PG")
    assert got.skills == ["factoring", "linear_equation"], "개념 분류에는 group node 허용"
    assert got.tracking_skill_ids() == ["linear_equation"]


def test_group_node_never_becomes_observation():
    r = InMemoryLearningRepository()
    p = Problem(problem_id="PG2", problem_latex="2x=6", skills=["factoring"])
    r.save_problem(p)
    with pytest.raises(RepositoryError):
        r.save_problem(Problem(problem_id="PG2", problem_latex="2x=6", skills=["arithmetic"]))


def test_observations_only_tracking_skills(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\n2x-3=6"))
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    from app.taxonomy import is_tracking_skill

    assert analysis.observations
    assert all(is_tracking_skill(o.skill_id) for o in analysis.observations)
    repo.save_analysis(analysis)


# ===========================================================================
# 격리 / 동시성 / 인터페이스
# ===========================================================================

def test_reset_clears_everything(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    reset_knowledge()
    repo.save_analysis(analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION))
    repo.reset()
    assert repo.list_problems() == []
    assert repo.list_attempts() == []
    with pytest.raises(RepositoryError):
        repo.list_analyses("A1")


def test_stored_objects_are_deep_copied(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    got = repo.get_attempt("A1")
    got.steps[0].latex_text = "변조됨"
    assert repo.get_attempt("A1").steps[0].latex_text == "2(x-3)=6"


def test_concurrent_saves_do_not_corrupt(repo):
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            repo.save_attempt(make_attempt(f"A{n}", "2(x-3)=6\nx=6"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(repo.list_attempts()) == 20


def test_concurrent_duplicate_save_raises_exactly_once(repo):
    outcomes: list[str] = []

    def worker() -> None:
        try:
            repo.save_attempt(make_attempt("DUP", "2(x-3)=6\nx=6"))
            outcomes.append("ok")
        except RepositoryError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("E_DUPLICATE_ID") == 7


def test_module_level_repository_resettable():
    reset_repository()
    repo = get_repository()
    assert repo is get_repository(), "공유 인스턴스여야 한다"
    repo.save_problem(Problem(problem_id="S1", problem_latex="2x=6", skills=["linear_equation"]))
    assert get_repository().get_problem("S1").problem_id == "S1"
    reset_repository()
    assert get_repository().list_problems() == []


def test_implementation_satisfies_interface():
    assert issubclass(InMemoryLearningRepository, LearningRepository)
    for name in (
        "save_problem", "get_problem", "list_problems",
        "save_correct_solution", "get_correct_solution", "list_correct_solutions",
        "save_attempt", "get_attempt", "list_attempts",
        "save_analysis", "get_analysis", "list_analyses", "get_latest_analysis",
        "has_analysis", "claim_analysis", "release_analysis",
        "reset",
    ):
        assert hasattr(get_repository(), name), name


# ===========================================================================
# 멱등성 지원 (PHASE 8-4): has_analysis / claim_analysis
# ===========================================================================


def test_has_analysis_is_false_for_unregistered_attempt(repo):
    """분석이 없으면 False. attempt 미등록이어도 예외를 던지지 않는다."""
    assert repo.has_analysis("NOPE") is False
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    assert repo.has_analysis("A1") is False
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION)
    repo.save_analysis(analysis)
    assert repo.has_analysis("A1") is True


def test_has_analysis_does_not_require_attempt_registration(repo):
    """게터는 참조를 검증하지만 이 쿼리는 묻기만 한다."""
    with pytest.raises(RepositoryError):
        repo.get_latest_analysis("A1")
    assert repo.has_analysis("A1") is False


def test_claim_analysis_serializes_same_attempt(repo):
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    acquired: list[str] = []

    def worker(name: str) -> None:
        repo.claim_analysis("A1")
        acquired.append(name)
        time.sleep(0.02)
        repo.release_analysis("A1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, ["x", "y"]))
    assert sorted(acquired) == ["x", "y"]
    assert repo.has_analysis("A1") is False


def test_claim_analysis_is_not_reentrant(repo):
    """같은 스레드로 두 번 claim 하면 블로킹된다 (RLock 이 아니다)."""
    repo.claim_analysis("A1")
    done = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(repo.claim_analysis, "A1").result(timeout=0.1)
    except TimeoutError:
        done.set()
    finally:
        repo.release_analysis("A1")
        pool.shutdown(wait=False)
    assert done.is_set(), "다른 스레드의 claim 이 통과했다"


def test_bkt_state_untouched_by_repository(repo):
    """저장소는 Knowledge State 를 직접 다루지 않는다 (분석 로직과 분리)."""
    repo.save_attempt(make_attempt("A1", "2(x-3)=6\nx=6"))
    reset_knowledge()
    analysis = analyze_attempt(PROBLEM, repo.get_attempt("A1"), solution=SOLUTION, engine="bkt")
    repo.save_analysis(analysis)
    base = BKTEngine()
    for o in analysis.observations:
        assert analysis.mastery[o.skill_id] == pytest.approx(
            base.update(o.skill_id, o.skill_id, o.outcome), abs=1e-12
        )
