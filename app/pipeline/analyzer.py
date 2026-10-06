"""파이프라인 오케스트레이션: 텍스트 풀이 → 검증 → 분류 → Knowledge State → JSON."""
from __future__ import annotations

from typing import Callable

from app.schemas import AnalysisStatus
from app.taxonomy import (
    SkillNotFoundError,
    SkillNotTrackableError,
    validate_tracking_skill,
)

from .classifier import classify
from .knowledge import (
    BKTEngine,
    InvalidStudentIdError,
    RuleStateEngine,
    SKILL_LINEAR,
    validate_student_id,
)
from .parser import parse_line, split_steps
from .verifier import verify


class AnalysisError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InvalidStudentIdAnalysisError(AnalysisError):
    """Knowledge State 키로 사용할 수 없는 student_id (S5)."""

    def __init__(self, message: str) -> None:
        super().__init__("E_INVALID_STUDENT_ID", message)


class UnknownSkillError(AnalysisError):
    """taxonomy에 없는 problem_skill (S4)."""

    def __init__(self, skill_id: str) -> None:
        super().__init__("E_UNKNOWN_SKILL", f"taxonomy에 없는 skill입니다: '{skill_id}'")


class UntrackableSkillError(AnalysisError):
    """taxonomy에 있지만 Knowledge State 추적 대상이 아닌 problem_skill (S4).

    bkt_eligible=false인 group node(factoring, polynomial_arithmetic)와
    future 상태 skill은 추적 대상이 아니다.
    """

    def __init__(self, skill_id: str) -> None:
        super().__init__(
            "E_SKILL_NOT_TRACKABLE",
            f"Knowledge State 추적 대상이 아닌 skill입니다 (bkt_eligible=false): '{skill_id}'",
        )


# 엔진 레지스트리 — Stage 0: 규칙 기반 / Stage 1: BKT (기획 로드맵 그대로)
_ENGINES: dict[str, RuleStateEngine | BKTEngine] = {
    "rule": RuleStateEngine(),
    "bkt": BKTEngine(),
}

# 입력 크기 상한 (중학 문항/풀이 현실 기준)
# _MAX_STEPS는 실제 golden/사용 데이터에 따라 조정할 수 있는 설정값이다.
# S2 적용 근거: 현재 공통수학1 MVP에서 30단계를 초과하는 정상 풀이에 대한
# 근거가 없으며, 상한 200은 solve() 과다 호출(2차식 200단계 = 13초)을 허용했다.
_MAX_PROBLEM_CHARS = 2000
_MAX_SOLUTION_CHARS = 20000
_MAX_STEPS = 30


def get_engine(name: str) -> RuleStateEngine | BKTEngine:
    if name not in _ENGINES:
        raise AnalysisError("E_UNKNOWN_ENGINE", f"알 수 없는 knowledge engine: {name}")
    return _ENGINES[name]


def reset_knowledge(engine: str | None = None) -> None:
    if engine is None:
        for e in _ENGINES.values():
            e.reset()
    else:
        get_engine(engine).reset()


# 에러 코드 → analysis_status (앱 r49).
# 분석을 수행했으나 신뢰 가능한 단계 판정이 하나도 없는 경우에만 UNKNOWN 이다.
# 요청 형식 오류·입력 크기 초과·ID 검증 실패는 분석을 수행하지 않았으므로
# 여기 포함하지 않는다 (UNKNOWN 으로 섞지 않는다).
_ANALYSIS_STATUS_BY_ERROR: dict[str, AnalysisStatus] = {
    "E_UNRECOGNIZED": "UNKNOWN",
    "E_UNVERIFIABLE": "UNKNOWN",
}


def analysis_status_of(verdict) -> AnalysisStatus:
    """verdict/parser 결과만으로 분석 상태를 결정한다. 원인·정답을 추측하지 않는다.

    - unrecognized 가 있거나 어떤 단계의 판정이 보류(None)면 REVIEW_REQUIRED
      (사람이 확인해야 한다)
    - 그 외에는 모든 단계가 판정되었으므로 ANALYZED
      (correct=true/false 와는 별개의 개념이다)
    UNKNOWN 은 여기서 나오지 않는다 — 신뢰 가능한 단계 판정이 하나도 없으면
    위에서 E_UNRECOGNIZED / E_UNVERIFIABLE 로 거절된다.
    """
    if verdict.unrecognized:
        return "REVIEW_REQUIRED"
    if any(v is None for v in verdict.valid):
        return "REVIEW_REQUIRED"
    return "ANALYZED"


def analyze(
    problem_latex: str,
    solution_text: str,
    student_id: str = "anonymous",
    engine: str = "rule",
    problem_skill: str = SKILL_LINEAR,
    segmenter: Callable[[str], list[str]] | None = None,
) -> dict:
    """풀이 분석 메인 엔트리.

    1) 단계 분리 (규칙 기반. 향후 LLM 단계 분석기 주입 지점 = segmenter 인자)
    2) SymPy 동치 판정 → valid[], 첫 오류 줄
    3) 오류 유형 분류 → 오개념 트리 매핑
    4) Knowledge State 갱신 (rule | bkt)
    5) 계약 JSON 반환
    """
    steps = (segmenter or split_steps)(solution_text)
    if not steps:
        raise AnalysisError("E_EMPTY_SOLUTION", "풀이 단계가 비어 있습니다.")
    # 입력 크기 상한 — 과다 입력으로 인한 판정 지연 차단
    if len(problem_latex) > _MAX_PROBLEM_CHARS:
        raise AnalysisError("E_INPUT_TOO_LARGE", "문제식이 너무 깁니다.")
    if len(solution_text) > _MAX_SOLUTION_CHARS:
        raise AnalysisError("E_INPUT_TOO_LARGE", f"풀이 입력이 너무 깁니다 (최대 {_MAX_SOLUTION_CHARS}자).")
    if len(steps) > _MAX_STEPS:
        raise AnalysisError(
            "E_INPUT_TOO_LARGE", f"풀이 단계가 너무 많습니다 (최대 {_MAX_STEPS}단계)."
        )
    problem_node = parse_line(problem_latex)
    if problem_node is None or problem_node[0] != "eq":
        # 문제식(등식)을 못 읽으면 검증 자체가 불가 — 정답으로 단정하지 않는다
        raise AnalysisError("E_BAD_PROBLEM", "문제식을 인식할 수 없습니다.")

    verdict = verify(problem_latex, steps)
    if not any(p is not None for p in verdict.parsed):
        raise AnalysisError(
            "E_UNRECOGNIZED",
            "모든 풀이 단계를 인식하지 못했습니다. 직접 입력으로 다시 시도해 주세요.",
        )
    if not any(v is not None for v in verdict.valid):
        # 한 줄도 동치 판정에 성공하지 못함 → 정답/오답 단정 불가 (ADR-03)
        raise AnalysisError(
            "E_UNVERIFIABLE",
            "풀이 단계를 대조 판정할 수 없습니다. 문제식과 풀이를 확인해 주세요.",
        )

    # S4: problem_skill은 Knowledge State/BKT에 직접 연결되는 tracking skill이므로
    # frozen taxonomy의 bkt_eligible 정의를 source of truth로 검증한다.
    # 통과한 값만 kb.update()에 사용하므로 임의 문자열이 상태 키를 만들 수 없다.
    try:
        problem_skill = validate_tracking_skill(problem_skill)
    except SkillNotFoundError:
        raise UnknownSkillError(problem_skill) from None
    except SkillNotTrackableError:
        raise UntrackableSkillError(problem_skill) from None

    # S5: student_id도 Knowledge State 키가 되므로 같은 정책 블록에서 검증한다.
    # (엔진 update() 경로에도 동일 헬퍼 검증이 있어 직접 호출을 방어한다)
    try:
        student_id = validate_student_id(student_id)
    except InvalidStudentIdError as exc:
        raise InvalidStudentIdAnalysisError(str(exc)) from None

    kb = get_engine(engine)

    if verdict.first_error_index is None:
        # 오류 없음 (인식 실패 줄은 오답이 아님 — ADR-03)
        kb.update(student_id, problem_skill, "correct")
        state = kb.state(student_id, problem_skill)
        return {
            "correct": True,
            "error_step": None,
            "error_type": None,
            "skill": problem_skill,
            "state": state,
            "mastery": kb.mastery(student_id, problem_skill),
            "misconception_id": None,
            "confidence": None,
            "steps_latex": verdict.steps,
            "valid": verdict.valid,
            "unrecognized": verdict.unrecognized,
            "analysis_status": analysis_status_of(verdict),
            # --- additive (S3) ---
            # skills: 문제와 연결된 Skill ID 목록 (taxonomy 기준, 개념적 분류)
            # skill_ids: 위 skills의 canonical alias — 지금은 동일 값을 담으며
            #            향후 정렬/normalization 결과가 들어갈 예약 필드다.
            "skills": [problem_skill],
            "skill_ids": [problem_skill],
            "error_subtype": None,
            "misconception_ids": [],
        }

    info = classify(problem_latex, steps, verdict.first_error_index)
    kb.update(student_id, info.skill, info.error_type)
    state = kb.state(student_id, info.skill)
    return {
        "correct": False,
        "error_step": verdict.first_error_index + 1,  # 1-based 줄 번호
        "error_type": info.error_type,
        "skill": info.skill,
        "state": state,
        "mastery": kb.mastery(student_id, info.skill),
        "misconception_id": info.misconception_id,
        "confidence": info.confidence,
        "steps_latex": verdict.steps,
        "valid": verdict.valid,
        "unrecognized": verdict.unrecognized,
        "analysis_status": analysis_status_of(verdict),
        # --- additive (S3) ---
        "skills": list(info.skill_ids),
        "skill_ids": list(info.skill_ids),
        "error_subtype": info.error_subtype,
        "misconception_ids": list(info.misconception_ids),
    }
