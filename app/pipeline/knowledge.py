from __future__ import annotations

import threading
import unicodedata
from collections import OrderedDict

# 오개념 트리 노드 정의는 3주차까지 동결 (기획서 5.4)
SKILL_LINEAR = "linear_equation"
SKILL_ARITHMETIC = "arithmetic"
SKILL_DISTRIBUTION = "distribution"
SKILL_TRANSPOSITION = "transposition"

STATE_MASTERED = "mastered"
STATE_LEARNING = "learning"
STATE_NEEDS_PRACTICE = "needs_practice"

# 규칙 기반 상태 → 숙련도 대표값 (Stage 0. BKT 전환 시 실제 p_known으로 대체)
_MASTERY_BY_STATE = {
    STATE_MASTERED: 0.85,
    STATE_LEARNING: 0.60,
    STATE_NEEDS_PRACTICE: 0.30,
}

# ---------------------------------------------------------------------------
# S5 Knowledge State 방어 상수
# ---------------------------------------------------------------------------

# student_id 정책 — 과도한 정규식을 강제하지 않고 최소한의 안전장치만 둔다.
# 실제 서비스에서 쓰는 형식(S001, student_001, API 키 등)과 충돌하지 않는다.
MAX_STUDENT_ID_LENGTH = 64

# 추적하는 학생 수 상한 (설정 상수). 초과 시 '가장 오래 사용되지 않은 학생'을
# 통째로 evict(LRU)하므로 개별 skill이 사라져 Knowledge State가 부분적으로
# 깨지는 일이 없다. 정상 사용자를 제한하지 않도록 넉넉하게 잡았다:
# 공통수학1 MVP는 학교 코호트(1개 학교 1학년 ≈ 수천~1만 명)를 대상으로 하며,
# 학생 1명당 (skill) 쌍이 수십 개이므로 1만 명이면 메모리도 수십 MB 이내다.
# 향후 MySQL로 이전하면 이 상한은 DB 캐시/커넥션 정책으로 대체된다.
MAX_KNOWLEDGE_STUDENTS = 10_000

# ---------------------------------------------------------------------------
# S13 — error_type별 상태 전이 표
#
# 분석 파이프라인은 오류를 '관찰된 형태'(error_type)로 분류한다. 이 표는
# 그 형태가 Knowledge State 전이에 어떤 의미인지를 명시한다.
#   calculation  : 개념은 이해했고 산술만 틀림      -> 부분 정답(learning)
#   procedure    : 개념은 이해했고 절차만 틀림      -> 부분 정답(learning)
#                  (step_skipped/incomplete_solution/method_mismatch/domain_violation)
#   concept_error: 개념 자체가 오해됨               -> needs_practice
#   comprehension: 문제를 잘못 읽음                 -> needs_practice
#
# 주의: procedure를 개념 오류로 취급하지 않는다. 기존 else 분기는 표가 없어
# procedure까지 needs_practice로 밀어넣었다.
# 표에 없는 outcome은 보수적으로 needs_practice를 유지한다(500으로 바꾸지 않음).
_ERROR_TYPE_TO_STATE: dict[str, str] = {
    "calculation": STATE_LEARNING,
    "procedure": STATE_LEARNING,
    "concept_error": STATE_NEEDS_PRACTICE,
    "comprehension": STATE_NEEDS_PRACTICE,
    # 이진 관측만 있고 원인 분류가 없는 경우 (KnowledgeObservation.outcome)
    "incorrect": STATE_NEEDS_PRACTICE,
}
_FALLBACK_ERROR_STATE = STATE_NEEDS_PRACTICE


def is_correct_outcome(outcome: str) -> bool:
    """BKT 이진 관측 판정 (S13).

    BKT는 '정답/오답'만 본다. error_type을 outcome으로 쓰지 않고,
    concept_error·procedure·comprehension·calculation은 모두 incorrect로 접힌다.
    """
    return outcome == "correct"


class InvalidStudentIdError(ValueError):
    """student_id가 Knowledge State 키로 사용할 수 없는 값."""


def validate_student_id(student_id) -> str:
    """student_id를 정규화·검증한다. 통과하면 정규화된 값을 반환한다.

    정책: 문자열 / 앞뒤 공백 제거 / 비어있지 않을 것 / 최대 64자 /
    제어문자(Unicode category Cc) 금지.
    """
    if not isinstance(student_id, str):
        raise InvalidStudentIdError(f"student_id 는 문자열이어야 합니다: {type(student_id).__name__}")
    normalized = student_id.strip()
    if not normalized:
        raise InvalidStudentIdError("student_id 는 빈 문자열일 수 없습니다.")
    if len(normalized) > MAX_STUDENT_ID_LENGTH:
        raise InvalidStudentIdError(
            f"student_id 는 {MAX_STUDENT_ID_LENGTH}자를 초과할 수 없습니다: {len(normalized)}자"
        )
    if any(unicodedata.category(ch) == "Cc" for ch in normalized):
        raise InvalidStudentIdError("student_id 에 제어문자가 포함될 수 없습니다.")
    return normalized


class _StudentLru:
    """학생 단위 LRU 인덱스 — Knowledge State 키 무제한 증가를 막는다."""

    def __init__(self, limit: int = MAX_KNOWLEDGE_STUDENTS) -> None:
        self._limit = limit
        self._order: OrderedDict[str, None] = OrderedDict()

    def touch(self, student_id: str) -> list[str]:
        """사용 표시 후 상한을 넘으면 가장 오래된 학생부터 반환한다."""
        self._order.pop(student_id, None)
        self._order[student_id] = None
        evicted: list[str] = []
        while len(self._order) > self._limit:
            oldest, _ = self._order.popitem(last=False)
            evicted.append(oldest)
        return evicted

    def forget(self, student_id: str) -> None:
        self._order.pop(student_id, None)

    def clear(self) -> None:
        self._order.clear()

    def tracked(self) -> list[str]:
        return list(self._order)

    def __len__(self) -> int:
        return len(self._order)


class RuleStateEngine:
    """Stage 0: 규칙 기반 Skill State (MVP용).

    규칙:
      - 정답 2연속 → mastered
      - 정답 1회 → learning (needs_practice에서 벗어남)
      - 계산/절차 오류 → learning (개념은 살아 있음, streak 초기화)
      - 개념 오류 / 문제 오독 → needs_practice 고정

    error_type → 상태 전이는 _ERROR_TYPE_TO_STATE 표가 담당한다 (S13).
    S5: student_id 검증 + 학생 단위 LRU 상한 + RLock 보호를 갖는다.
    """

    def __init__(self, max_students: int = MAX_KNOWLEDGE_STUDENTS) -> None:
        self._lock = threading.RLock()
        self._streak: dict[tuple[str, str], int] = {}
        self._state: dict[tuple[str, str], str] = {}
        self._lru = _StudentLru(max_students)

    def _forget(self, student_id: str) -> None:
        """학생 한 명의 상태를 통째로 제거한다 (부분 삭제 금지)."""
        for key in [k for k in self._state if k[0] == student_id]:
            del self._state[key]
        for key in [k for k in self._streak if k[0] == student_id]:
            del self._streak[key]
        self._lru.forget(student_id)

    def update(self, student_id: str, skill: str, outcome: str) -> str:
        student_id = validate_student_id(student_id)
        with self._lock:
            for evicted in self._lru.touch(student_id):
                self._forget(evicted)
            key = (student_id, skill)
            if is_correct_outcome(outcome):
                streak = self._streak.get(key, 0) + 1
                self._streak[key] = streak
                state = STATE_MASTERED if streak >= 2 else STATE_LEARNING
            else:
                self._streak[key] = 0
                state = _ERROR_TYPE_TO_STATE.get(outcome, _FALLBACK_ERROR_STATE)
            self._state[key] = state
            return state

    def state(self, student_id: str, skill: str) -> str:
        with self._lock:
            return self._state.get((student_id, skill), STATE_LEARNING)

    def update_from_observation(
        self, student_id: str, skill: str, outcome: str, error_type: str | None = None
    ) -> str:
        """KnowledgeObservation 기반 갱신 (Phase 6).

        outcome(이진)이 기준이고 error_type 은 오답일 때 상태를 세분화하는
        보조 정보다. 규칙 엔진은 concept_error→needs_practice,
        calculation/procedure→learning 으로 갈린다.
        정답인데 error_type 이 없으므로 outcome 을 다시 보존해야 한다.
        """
        if is_correct_outcome(outcome):
            return self.update(student_id, skill, "correct")
        return self.update(student_id, skill, error_type or "incorrect")

    def mastery(self, student_id: str, skill: str) -> float:
        with self._lock:
            return _MASTERY_BY_STATE[self._state.get((student_id, skill), STATE_LEARNING)]

    def tracked_students(self) -> list[str]:
        """현재 추적 중인 학생 목록 (진단/테스트용)."""
        with self._lock:
            return self._lru.tracked()

    def reset(self) -> None:
        with self._lock:
            self._streak.clear()
            self._state.clear()
            self._lru.clear()


class BKTEngine:
    """Stage 1: BKT 직접 구현 (PyTorch 없이도 동일 수식 — 학습 목표는 수식 이해).

    파라미터(초기값, 단원별 EM fitting 후 튜닝):
      p_init=0.3, p_learn=0.15, p_slip=0.1, p_guess=0.2

    갱신식:
      관측 전:  p' = P(L | 관측)
      관측 후:  p'' = p' + (1 - p') * p_learn

    S13: 관측은 이진이다. error_type을 outcome으로 쓰지 않고 is_correct_outcome()
    으로 '정답/오답'만 판정한다(concept_error·procedure·comprehension·
    calculation은 모두 incorrect로 접힌다).

    S5: student_id 검증 + 학생 단위 LRU 상한 + RLock 보호를 갖는다.
    """

    def __init__(
        self,
        p_init: float = 0.3,
        p_learn: float = 0.15,
        p_slip: float = 0.1,
        p_guess: float = 0.2,
        mastered_threshold: float = 0.7,
        learning_threshold: float = 0.4,
        max_students: int = MAX_KNOWLEDGE_STUDENTS,
    ) -> None:
        self.p_init = p_init
        self.p_learn = p_learn
        self.p_slip = p_slip
        self.p_guess = p_guess
        self.mastered_threshold = mastered_threshold
        self.learning_threshold = learning_threshold
        self._lock = threading.RLock()
        self._p: dict[tuple[str, str], float] = {}
        self._lru = _StudentLru(max_students)

    def _forget(self, student_id: str) -> None:
        """학생 한 명의 p_known을 통째로 제거한다 (부분 삭제 금지)."""
        for key in [k for k in self._p if k[0] == student_id]:
            del self._p[key]
        self._lru.forget(student_id)

    def update(self, student_id: str, skill: str, outcome: str) -> float:
        student_id = validate_student_id(student_id)
        with self._lock:
            for evicted in self._lru.touch(student_id):
                self._forget(evicted)
            correct = is_correct_outcome(outcome)
            p = self._p.get((student_id, skill), self.p_init)
            if correct:
                posterior = (p * (1 - self.p_slip)) / (
                    p * (1 - self.p_slip) + (1 - p) * self.p_guess
                )
            else:
                posterior = (p * self.p_slip) / (
                    p * self.p_slip + (1 - p) * (1 - self.p_guess)
                )
            p_next = posterior + (1 - posterior) * self.p_learn
            # 값域 고정
            p_next = min(max(p_next, 0.0), 1.0)
            self._p[(student_id, skill)] = p_next
            return p_next

    def mastery(self, student_id: str, skill: str) -> float:
        with self._lock:
            return self._p.get((student_id, skill), self.p_init)

    def state(self, student_id: str, skill: str) -> str:
        p = self.mastery(student_id, skill)
        if p >= self.mastered_threshold:
            return STATE_MASTERED
        if p >= self.learning_threshold:
            return STATE_LEARNING
        return STATE_NEEDS_PRACTICE

    def update_from_observation(
        self, student_id: str, skill: str, outcome: str, error_type: str | None = None
    ) -> float:
        """KnowledgeObservation 기반 갱신 (Phase 6).

        BKT는 이진 관측만 사용한다. error_type을 outcome으로 넘기지 않는다
        (is_correct_outcome()이 'correct' 여부만 판정).
        """
        return self.update(student_id, skill, outcome)

    def tracked_students(self) -> list[str]:
        """현재 추적 중인 학생 목록 (진단/테스트용)."""
        with self._lock:
            return self._lru.tracked()

    def reset(self) -> None:
        with self._lock:
            self._p.clear()
            self._lru.clear()
