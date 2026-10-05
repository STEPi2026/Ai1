"""Phase 8-1 — 학습 세션 저장소 계층 (in-memory).

분석 도메인 객체(Problem / CorrectSolution / StudentAttempt / AttemptAnalysis)를
attempt 단위로 저장·조회한다. 저장 방식과 분석 로직을 분리해 나중에 MySQL
구현으로 교체할 수 있게 한다.

    Problem ──1:N── CorrectSolution
       │
       └──1:N── StudentAttempt ──1:N── AttemptAnalysis(분석 이력)

설계 결정
- **식별자는 기존 것을 그대로 쓴다.** 도메인 모델에 이미 있는
  `problem_id` / `solution_id` / `attempt_id` 를 그대로 저장 키로 쓴다.
  분석 결과는 저장소만 아는 `analysis_id` 를 추가로 부여한다
  (MySQL `ai_analysis_results` auto-increment PK 대응. 도메인 모델은 불변).
- **중복 ID 는 저장하지 않는다.** 같은 ID 재저장은 오류다. 분석 이력은 같은
  attempt 에 여러 건 쌓이는 것이지, 같은 분석을 덮어쓰는 것이 아니다.
  (MySQL unique 제약과 동일语义)
- **Problem.skills(개념적 분류)와 Knowledge State 추적 skill 은 구분한다.**
  개념 분류에는 taxonomy 의 group node 를 허용한다. 반면
  `AttemptAnalysis.observations[].skill_id` 는 반드시 검증된 추적 가능
  leaf skill 이어야 한다(그룹 node 금지) — 이미 도메인 모델이 강제하지만
  저장 경로에서도 재확인한다.
- **저장 시 깊은 복사**한다. 호출자가 저장된 객체를 나중에 변형해도 저장본이
  바뀌지 않는다. (MySQL 직렬화와 동일 의미)
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.taxonomy import is_tracking_skill

from .session import AttemptAnalysis, CorrectSolution, Problem, StudentAttempt


class RepositoryError(ValueError):
    """저장소 오류. 기존 AnalysisError 와 같은 코드 관례를 따른다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AnalysisRecord:
    """저장된 분석 1건. analysis_id 는 저장소만 부여한다."""

    analysis_id: str
    attempt_id: str
    analysis: AttemptAnalysis

    @property
    def problem_id(self) -> str:
        return self.analysis.problem_id

    @property
    def student_id(self) -> str:
        return self.analysis.student_id

    @property
    def raw_result(self) -> dict | None:
        """MySQL `ai_analysis_results.raw_result` 대응 (응답 원본 전체)."""
        return self.analysis.api_response


class LearningRepository(ABC):
    """학습 저장소 인터페이스 — 최소 메서드만 정의한다."""

    # --- Problem ---
    @abstractmethod
    def save_problem(self, problem: Problem) -> None: ...
    @abstractmethod
    def get_problem(self, problem_id: str) -> Problem: ...
    @abstractmethod
    def list_problems(self) -> list[Problem]: ...

    # --- CorrectSolution ---
    @abstractmethod
    def save_correct_solution(self, solution: CorrectSolution) -> None: ...
    @abstractmethod
    def get_correct_solution(self, solution_id: str) -> CorrectSolution: ...
    @abstractmethod
    def list_correct_solutions(self, problem_id: str) -> list[CorrectSolution]: ...

    # --- StudentAttempt ---
    @abstractmethod
    def save_attempt(self, attempt: StudentAttempt) -> None: ...
    @abstractmethod
    def get_attempt(self, attempt_id: str) -> StudentAttempt: ...
    @abstractmethod
    def list_attempts(
        self, problem_id: str | None = None, student_id: str | None = None
    ) -> list[StudentAttempt]: ...

    # --- AttemptAnalysis (이력) ---
    @abstractmethod
    def save_analysis(self, analysis: AttemptAnalysis) -> AnalysisRecord: ...
    @abstractmethod
    def get_analysis(self, analysis_id: str) -> AnalysisRecord: ...
    @abstractmethod
    def list_analyses(self, attempt_id: str) -> list[AnalysisRecord]: ...
    @abstractmethod
    def get_latest_analysis(self, attempt_id: str) -> AnalysisRecord | None: ...
    @abstractmethod
    def has_analysis(self, attempt_id: str) -> bool: ...
    @abstractmethod
    def claim_analysis(self, attempt_id: str) -> None: ...
    @abstractmethod
    def release_analysis(self, attempt_id: str) -> None: ...

    @abstractmethod
    def reset(self) -> None: ...


class InMemoryLearningRepository(LearningRepository):
    """dict + RLock 기반 구현. 분석 흐름과 저장 로직을 분리한다."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._problems: dict[str, Problem] = {}
        self._solutions: dict[str, CorrectSolution] = {}
        self._attempts: dict[str, StudentAttempt] = {}
        self._analyses: dict[str, AnalysisRecord] = {}
        self._analysis_ids: dict[str, list[str]] = {}  # attempt_id -> [analysis_id]
        self._analysis_gates: dict[str, threading.Lock] = {}  # attempt_id 단위 분석 락
        self._seq = 0

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _require_new(mapping: dict, key: str, kind: str) -> None:
        if key in mapping:
            raise RepositoryError("E_DUPLICATE_ID", f"{kind} 이(가) 이미 존재합니다: '{key}'")

    def _require_ref(self, mapping: dict, key: str, kind: str) -> None:
        if key not in mapping:
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY", f"{kind} 를 참조합니다: '{key}' (존재하지 않음)"
            )

    def _next_analysis_id(self, attempt_id: str) -> str:
        self._seq += 1
        return f"{attempt_id}#{self._seq}"

    # ------------------------------------------------------------------
    # Problem
    # ------------------------------------------------------------------
    def save_problem(self, problem: Problem) -> None:
        with self._lock:
            self._require_new(self._problems, problem.problem_id, "problem_id")
            # 개념적 skills 는 group node 를 허용한다 (추적 여부로 제한하지 않는다)
            self._problems[problem.problem_id] = problem.model_copy(deep=True)

    def get_problem(self, problem_id: str) -> Problem:
        with self._lock:
            self._require_ref(self._problems, problem_id, "problem_id")
            return self._problems[problem_id].model_copy(deep=True)

    def list_problems(self) -> list[Problem]:
        with self._lock:
            return [p.model_copy(deep=True) for p in self._problems.values()]

    # ------------------------------------------------------------------
    # CorrectSolution
    # ------------------------------------------------------------------
    def save_correct_solution(self, solution: CorrectSolution) -> None:
        with self._lock:
            self._require_ref(self._problems, solution.problem_id, "problem_id")
            self._require_new(self._solutions, solution.solution_id, "solution_id")
            self._solutions[solution.solution_id] = solution.model_copy(deep=True)

    def get_correct_solution(self, solution_id: str) -> CorrectSolution:
        with self._lock:
            self._require_ref(self._solutions, solution_id, "solution_id")
            return self._solutions[solution_id].model_copy(deep=True)

    def list_correct_solutions(self, problem_id: str) -> list[CorrectSolution]:
        with self._lock:
            self._require_ref(self._problems, problem_id, "problem_id")
            return [
                s.model_copy(deep=True)
                for s in self._solutions.values()
                if s.problem_id == problem_id
            ]

    # ------------------------------------------------------------------
    # StudentAttempt
    # ------------------------------------------------------------------
    def save_attempt(self, attempt: StudentAttempt) -> None:
        with self._lock:
            self._require_ref(self._problems, attempt.problem_id, "problem_id")
            self._require_new(self._attempts, attempt.attempt_id, "attempt_id")
            self._attempts[attempt.attempt_id] = attempt.model_copy(deep=True)

    def get_attempt(self, attempt_id: str) -> StudentAttempt:
        with self._lock:
            self._require_ref(self._attempts, attempt_id, "attempt_id")
            return self._attempts[attempt_id].model_copy(deep=True)

    def list_attempts(
        self, problem_id: str | None = None, student_id: str | None = None
    ) -> list[StudentAttempt]:
        """재풀이/재촬영 회차 목록. 저장 순서(제출 순서)를 유지한다."""
        with self._lock:
            if problem_id is not None:
                self._require_ref(self._problems, problem_id, "problem_id")
            return [
                a.model_copy(deep=True)
                for a in self._attempts.values()
                if (problem_id is None or a.problem_id == problem_id)
                and (student_id is None or a.student_id == student_id)
            ]

    # ------------------------------------------------------------------
    # AttemptAnalysis (분석 이력)
    # ------------------------------------------------------------------
    def save_analysis(self, analysis: AttemptAnalysis) -> AnalysisRecord:
        with self._lock:
            self._require_ref(self._attempts, analysis.attempt_id, "attempt_id")
            self._require_ref(self._problems, analysis.problem_id, "problem_id")
            self._check_analysis_consistency(analysis)
            analysis_id = self._next_analysis_id(analysis.attempt_id)
            record = AnalysisRecord(
                analysis_id=analysis_id,
                attempt_id=analysis.attempt_id,
                analysis=analysis.model_copy(deep=True),
            )
            self._analyses[analysis_id] = record
            self._analysis_ids.setdefault(analysis.attempt_id, []).append(analysis_id)
            return record

    def _check_analysis_consistency(self, analysis: AttemptAnalysis) -> None:
        """분석과 attempt/problem 의 식별자가 일치하는지, 관측이 추적 가능한지."""
        attempt = analysis.attempt
        if attempt.attempt_id != analysis.attempt_id:
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY",
                f"analysis.attempt_id({analysis.attempt_id}) != "
                f"analysis.attempt.attempt_id({attempt.attempt_id})",
            )
        if attempt.problem_id != analysis.problem_id:
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY",
                f"attempt.problem_id({attempt.problem_id}) != "
                f"analysis.problem_id({analysis.problem_id})",
            )
        if attempt.student_id != analysis.student_id:
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY",
                f"attempt.student_id({attempt.student_id}) != "
                f"analysis.student_id({analysis.student_id})",
            )
        stored = self._attempts[analysis.attempt_id]
        if (stored.problem_id, stored.student_id) != (attempt.problem_id, attempt.student_id):
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY",
                f"저장된 attempt 와 분석이 다릅니다: '{analysis.attempt_id}'",
            )
        # Knowledge State 는 검증된 추적 가능 leaf skill 만 관측한다
        # (Problem.skills 의 개념적 그룹 node 와는 구분된다)
        for obs in analysis.observations:
            if not is_tracking_skill(obs.skill_id):
                raise RepositoryError(
                    "E_REFERENCE_INTEGRITY",
                    f"관측 skill 이 추적 대상이 아닙니다: '{obs.skill_id}'",
                )

    def get_analysis(self, analysis_id: str) -> AnalysisRecord:
        with self._lock:
            self._require_ref(self._analyses, analysis_id, "analysis_id")
            record = self._analyses[analysis_id]
            return AnalysisRecord(
                analysis_id=record.analysis_id,
                attempt_id=record.attempt_id,
                analysis=record.analysis.model_copy(deep=True),
            )

    def list_analyses(self, attempt_id: str) -> list[AnalysisRecord]:
        """한 attempt 의 분석 이력. 저장 순서(분석 시각 순)를 유지한다."""
        with self._lock:
            self._require_ref(self._attempts, attempt_id, "attempt_id")
            ids = self._analysis_ids.get(attempt_id, [])
            return [self.get_analysis(i) for i in ids]

    def get_latest_analysis(self, attempt_id: str) -> AnalysisRecord | None:
        with self._lock:
            ids = self._analysis_ids.get(attempt_id, [])
            if not ids:
                self._require_ref(self._attempts, attempt_id, "attempt_id")
                return None
            return self.get_analysis(ids[-1])

    def has_analysis(self, attempt_id: str) -> bool:
        """이 attempt 에 분석이 하나라도 저장돼 있는가.

        게터(get_*)는 참조 무결성을 검증하지만, 이 메서드는 '분석 이력이 있는가'
        만 묻는 쿼리다. 아직 분석되지 않은 attempt_id 는 False 를 돌려주므로
        attempt 가 분석 성공 뒤에 등록되는 흐름에서도 안전하다.
        """
        with self._lock:
            return bool(self._analysis_ids.get(attempt_id))

    # ------------------------------------------------------------------
    def claim_analysis(self, attempt_id: str) -> None:
        """attempt 단위 분석 구간을 원자적으로 확보한다 (블로킹).

        `check(이력 유무) → analyze → save` 를 직렬화해, 동시 중복 요청이
        같은 제출을 두 번 분석하거나 Knowledge State 를 두 번 갱신하는 것을
        막는다. 서로 다른 attempt 는 서로를 막지 않는다.
        반드시 release_analysis() 로 해제해야 한다(try/finally).
        """
        gate = self._analysis_gate(attempt_id)
        gate.acquire()

    def release_analysis(self, attempt_id: str) -> None:
        """claim_analysis() 으로 확보한 구간을 해제한다."""
        self._analysis_gate(attempt_id).release()

    def _analysis_gate(self, attempt_id: str) -> threading.Lock:
        with self._lock:
            gate = self._analysis_gates.get(attempt_id)
            if gate is None:
                gate = threading.Lock()
                self._analysis_gates[attempt_id] = gate
            return gate

    def reset(self) -> None:
        """테스트 격리용. MySQL 구현에서는 truncate/롤백에 대응한다."""
        with self._lock:
            self._problems.clear()
            self._solutions.clear()
            self._attempts.clear()
            self._analyses.clear()
            self._analysis_ids.clear()
            self._analysis_gates.clear()
            self._seq = 0


# 공유 인스턴스 — 동시성 보호는 InMemoryLearningRepository 의 RLock 가 담당한다.
_repository = InMemoryLearningRepository()


def get_repository() -> InMemoryLearningRepository:
    """공유 저장소. 분석 로직은 저장 방식에 의존하지 않도록 이 함수만 쓴다."""
    return _repository


def reset_repository() -> None:
    """테스트 전용 초기화."""
    _repository.reset()


__all__ = [
    "AnalysisRecord",
    "InMemoryLearningRepository",
    "LearningRepository",
    "RepositoryError",
    "get_repository",
    "reset_repository",
]
