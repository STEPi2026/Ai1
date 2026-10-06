"""Phase 6 — 학습 데이터 흐름.

    Problem
      → StudentAttempt (수업 풀이 시도)
      → verifier  (단계별 동치 판정)
      → classifier(오류 유형 → taxonomy 매핑)
      → ErrorRecord[]
      → KnowledgeObservation[]   (BKT outcome은 반드시 이진)
      → Knowledge State / BKT

정답 풀이(CorrectSolution)는 같은 SolutionStep 구조를 재사용해 정답/학생 풀이의
구조적 중복을 없앤다.

설계 원칙:
- taxonomy ID는 frozen(data/*.json)이며, 모든 검증은 app.taxonomy 헬퍼를 재사용한다.
- student_id 정책은 app.pipeline.knowledge.validate_student_id()를 그대로 재사용한다.
- error_type(관찰된 형태)과 misconception(개념적 원인)은 분리된 필드로 유지한다.
- group node는 Problem.skills 에 표현할 수 있으나 KnowledgeObservation 대상이 아니다.
- KnowledgeObservation.outcome 은 'correct' | 'incorrect' 뿐이다.
- 기존 analyze() / API 계약은 변경하지 않는다(신규 흐름은 그 위에 얹는다).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.taxonomy import (
    ERROR_TYPE_CATALOG,
    SkillNotTrackableError,
    is_active_error_subtype,
    is_known_skill,
    is_tracking_skill,
    is_valid_error_subtype,
    is_valid_error_type,
    is_valid_misconception_id,
    misconception_by_id,
    validate_tracking_skill,
)

from .analyzer import (
    _MAX_PROBLEM_CHARS,
    _MAX_SOLUTION_CHARS,
    _MAX_STEPS,
    AnalysisError,
    UntrackableSkillError,
    analysis_status_of,
    get_engine,
)
from .classifier import classify
from .knowledge import SKILL_LINEAR, validate_student_id
from .parser import parse_line, split_steps
from .verifier import _SolveBudget, equivalent, verify

# taxonomy 이름 lookup (description_ko 생성용)
_ERROR_TYPE_NAMES = {e.error_type_id: e.name for e in ERROR_TYPE_CATALOG.error_types}
_SUBTYPE_NAMES = {
    st.error_subtype_id: st.name
    for e in ERROR_TYPE_CATALOG.error_types
    for st in e.subtypes
}


def _describe_ko(error_type: str, error_subtype: str, misconception_ids: list[str]) -> str:
    base = f"{_ERROR_TYPE_NAMES.get(error_type, error_type)} · {_SUBTYPE_NAMES.get(error_subtype, error_subtype)}"
    if misconception_ids:
        names = [misconception_by_id(m).name for m in misconception_ids]
        base += f" (개념적 원인 추정: {', '.join(names)})"
    return base


# ---------------------------------------------------------------------------
# Step — 정답 풀이와 학생 풀이가 공유하는 단계 구조
# ---------------------------------------------------------------------------

class SolutionStep(BaseModel):
    """풀이의 한 단계. 정답 풀이/학생 풀이 공용 구조."""

    step_no: int = Field(..., ge=1, description="1-based 단계 번호")
    latex_text: str = Field(..., description="단계 원문 (LaTeX/평문 혼용)")
    skill_ids: list[str] = Field(
        default_factory=list,
        description="이 단계에서 관여하는 Skill ID (문제의 관련 Skill 중)",
    )
    aligned_step_no: int | None = Field(
        None, description="정답 풀이의 정렬 기준 단계 번호(정답 풀이에서 사용)"
    )
    unrecognized: bool = Field(
        False, description="이 단계가 인식/판정 불가였는지 (verifier 결과 반영)"
    )

    @field_validator("step_no")
    @classmethod
    def _positive_step_no(cls, v: int) -> int:
        if v < 1:
            raise ValueError("step_no 는 1 이상이어야 합니다")
        return v

    @field_validator("skill_ids")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not is_known_skill(sid):
                raise ValueError(f"taxonomy에 없는 skill_id: '{sid}'")
        return v


# ---------------------------------------------------------------------------
# Problem
# ---------------------------------------------------------------------------

class Problem(BaseModel):
    """문제. 관련 Skill을 복수로 표현한다."""

    problem_id: str
    problem_latex: str
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "문제의 관련 Skill ID 목록. 개념적 분류이므로 group node를 포함할 수 "
            "있지만 Knowledge Observation(BKT) 대상은 아니다."
        ),
    )
    question_text: str | None = Field(
        None,
        description=(
            "명세 problems.question(r25) 의 학생 제시용 표시 텍스트. problem_latex(기계 "
            "검증용 식)와 구분되며, 미제공이면 null 이다(추측하지 않는다)."
        ),
    )
    concept_name: str | None = Field(
        None,
        description=(
            "명세 concepts.name(r33) 에 대응하는 단원/개념 표시명. skill_id 에서 "
            "파생하지 않으며 요청이 명시적으로 준 값만 저장한다. 미제공이면 null."
        ),
    )
    unit_name: str | None = Field(
        None,
        description=(
            "표시용 단원명. 명세 대응 컬럼이 없어 표시 전용이며, 미제공이면 null."
        ),
    )

    @field_validator("skills")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not is_known_skill(sid):
                raise ValueError(f"taxonomy에 없는 skill_id: '{sid}'")
        return v

    def tracking_skill_ids(self) -> list[str]:
        """Knowledge State 추적 대상 skill만 (group node 제외)."""
        return [s for s in self.skills if is_tracking_skill(s)]

    def primary_tracking_skill(self) -> str:
        """API 호환 응답에 쓸 단일 대표 skill. 추적 가능 skill이 없으면 예외."""
        for sid in self.skills:
            if is_tracking_skill(sid):
                return sid
        raise UntrackableSkillError(",".join(self.skills) or "(없음)")


# ---------------------------------------------------------------------------
# CorrectSolution
# ---------------------------------------------------------------------------

class CorrectSolution(BaseModel):
    """문제의 정답 풀이."""

    solution_id: str
    problem_id: str
    solution_kind: str = Field("algebraic", description="풀이 유형 (예: algebraic)")
    steps: list[SolutionStep] = Field(..., min_length=1, description="정답 풀이 단계")
    answer_latex: str = Field(..., description="최종 답")
    is_primary: bool = Field(True, description="이 문제의 대표 정답 풀이 여부")

    @field_validator("steps")
    @classmethod
    def _unique_step_no(cls, v: list[SolutionStep]) -> list[SolutionStep]:
        nos = [s.step_no for s in v]
        if len(set(nos)) != len(nos):
            raise ValueError(f"step_no 가 중복됩니다: {nos}")
        return v

    def belongs_to(self, problem: Problem) -> bool:
        return self.problem_id == problem.problem_id


# ---------------------------------------------------------------------------
# Error / KnowledgeObservation
# ---------------------------------------------------------------------------

class ErrorRecord(BaseModel):
    """독립적인 오류 레코드. error_type(형태)과 misconception(원인)은 분리된다."""

    error_id: str
    attempt_id: str
    step_no: int = Field(..., ge=1, description="오류가 발생한 단계 번호(1-based)")
    ref_step_no: int | None = Field(
        None, description="대조 기준이 된 이전 학생 단계(없으면 문제식이 기준)"
    )
    ref_solution_step_no: int | None = Field(
        None,
        description=(
            "ref_step_no(기준 학생 단계)에 대응되는 정답 풀이 단계(Phase 7-1). "
            "미정렬이거나 기준이 문제식이면 None이다. "
            "학생 단계→정답 단계 전체 대응은 attempt.steps[].aligned_step_no 를 참조할 것."
        ),
    )
    error_type: str
    error_subtype: str
    skill_ids: list[str] = Field(default_factory=list)
    misconception_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_latex: str = Field(..., description="오류를 드러낸 학생 단계 원문")
    description_ko: str = Field(..., description="사람이 읽는 오류 설명")

    @field_validator("error_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if not is_valid_error_type(v):
            raise ValueError(f"taxonomy에 없는 error_type: '{v}'")
        return v

    @field_validator("error_subtype")
    @classmethod
    def _valid_subtype(cls, v: str, info) -> str:
        error_type = info.data.get("error_type")
        if error_type is not None:
            if not is_valid_error_subtype(error_type, v):
                raise ValueError(f"'{v}' 는 '{error_type}' 의 subtype 이 아닙니다")
        if not is_active_error_subtype(v):
            raise ValueError(f"'{v}' 는 future 상태라 지금 사용할 수 없습니다")
        return v

    @field_validator("skill_ids")
    @classmethod
    def _valid_skills(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not is_known_skill(sid):
                raise ValueError(f"taxonomy에 없는 skill_id: '{sid}'")
        return v

    @field_validator("misconception_ids")
    @classmethod
    def _valid_misconceptions(cls, v: list[str]) -> list[str]:
        for mid in v:
            if not is_valid_misconception_id(mid):
                raise ValueError(f"taxonomy에 없는 misconception_id: '{mid}'")
        return v


class KnowledgeObservation(BaseModel):
    """Knowledge State 갱신에 쓰는 관측값. BKT outcome은 반드시 이진."""

    observation_id: str
    attempt_id: str
    problem_id: str
    student_id: str
    skill_id: str
    outcome: Literal["correct", "incorrect"] = Field(
        ..., description="BKT 이진 관측. error_type 을 넣지 않는다."
    )
    error_type: str | None = Field(
        None, description="원인 분류(참고용). outcome 을 대체하지 않는다."
    )
    error_id: str | None = Field(None, description="연결된 Error.error_id")

    @field_validator("skill_id")
    @classmethod
    def _trackable_skill(cls, v: str) -> str:
        try:
            return validate_tracking_skill(v)
        except SkillNotTrackableError:
            raise ValueError(f"group node/추적 불가 skill 은 관측 대상이 될 수 없습니다: '{v}'")
        except Exception as exc:  # taxonomy 미등록
            raise ValueError(f"taxonomy에 없는 skill_id: '{v}'") from exc

    @field_validator("error_type")
    @classmethod
    def _valid_type(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_error_type(v):
            raise ValueError(f"taxonomy에 없는 error_type: '{v}'")
        return v

    def outcome_argument(self) -> str:
        """엔진에 넘길 이진 outcome (error_type 을 쓰지 않음)."""
        return self.outcome


# ---------------------------------------------------------------------------
# Step alignment (Phase 7-1) — 정답 풀이 ↔ 학생 풀이 단계 대응
# ---------------------------------------------------------------------------

MatchKind = Literal["identical_text", "normalized_text", "math_equivalent"]

# 강한 표현 증거(문자/표기 수준) — acceptance 게이트에서 모호성 검사를 하지 않는다.
_STRONG_MATCH_KINDS = ("identical_text", "normalized_text")

_WHITESPACE = re.compile(r"\s+")


def _swap_canonical(text: str) -> str | None:
    """등식 좌우 반전 정규 표현 (Phase 7-2-3). 대상이 아니면 None.

    '등식의 좌변과 우변을 단순히 swap 한 표현'까지만 다룬다:
      - '=' 이 정확히 하나인 등식만 (다중 등식 a=b=c 는 제외)
      - 공백만 제거한다 (기존 parser.normalize() 도 공백을 지운다.
        그 밖의 문자 보정은 하지 않는다 — ×, -, ^, {} 등 변환은 범위 밖)
      - 좌변/우변을 사전순으로 정렬해 '좌우가 뒤집힌 두 표현'을 같은 키로 만든다

    예: 2x-6=6 ↔ 6=2x-6  →  "2x-6=6"
        x=6     ↔ 6=x      →  "x=6"
    """
    if text.count("=") != 1:
        return None
    lhs, rhs = text.split("=")
    lhs, rhs = _WHITESPACE.sub("", lhs), _WHITESPACE.sub("", rhs)
    if not lhs or not rhs:
        return None
    first, second = sorted((lhs, rhs))
    return f"{first}={second}"


class AlignedStep(BaseModel):
    """학생 단계 1개 ↔ 정답 단계 1개의 대응 근거."""

    attempt_step_no: int = Field(..., ge=1)
    solution_step_no: int = Field(..., ge=1)
    match_kind: MatchKind = Field(
        ..., description="identical_text | normalized_text | math_equivalent"
    )


class StepAlignment(BaseModel):
    """정렬 결과. 정렬할 수 없는 단계는 '미정(None)'으로 남긴다."""

    attempt_id: str
    problem_id: str
    solution_id: str | None = None
    method: Literal["lcs-equivalence"] = "lcs-equivalence"
    aligned: bool = Field(False, description="하나라도 정렬되었는가")
    pairs: list[AlignedStep] = Field(default_factory=list)
    unaligned_attempt_steps: list[int] = Field(default_factory=list)
    unaligned_solution_steps: list[int] = Field(default_factory=list)
    ambiguous_attempt_steps: list[int] = Field(
        default_factory=list,
        description=(
            "math_equivalent 후보가 여러 정답 단계와 동시에 성립해 '어느 쪽인지' "
            "판단할 수 없어 정렬을 포기한 학생 단계(1-based, Phase 7-2-2). "
            "잘못 정렬하는 것보다 미정(None)을 택한다."
        ),
    )

    def solution_step_for(self, attempt_step_no: int) -> int | None:
        """학생 단계 번호 → 정답 단계 번호(미정이면 None)."""
        for p in self.pairs:
            if p.attempt_step_no == attempt_step_no:
                return p.solution_step_no
        return None


def _match_kind(
    attempt_text: str,
    solution_text: str,
    budget: "_SolveBudget | None",
    cache: dict[tuple[str, str], MatchKind | None],
) -> MatchKind | None:
    """두 단계가 같은 변환을 나타내는지 판정한다. 판정 불가면 None(정렬 안 함).

    신호 강도 순서 (Phase 7-2-3):
      1) identical_text  — 원본 문자열 완전 일치
      2) normalized_text — 원본은 다르지만 등식 좌우가 뒤집힌 같은 표현
      3) math_equivalent — 해집합 동치 (1변수에서는 서로 다른 단계도 모두 동치가 된다)
      4) None            — 판정 불가
    """
    key = (attempt_text, solution_text)
    if key in cache:
        return cache[key]
    result: MatchKind | None = None
    if attempt_text == solution_text:
        result = "identical_text"
    else:
        canonical_attempt = _swap_canonical(attempt_text)
        canonical_solution = _swap_canonical(solution_text)
        if (
            canonical_attempt is not None
            and canonical_solution is not None
            and canonical_attempt == canonical_solution
        ):
            result = "normalized_text"
        else:
            a, b = parse_line(attempt_text), parse_line(solution_text)
            if a is not None and b is not None and a[0] == "eq" and b[0] == "eq":
                if budget is None or budget.take(2):
                    try:
                        if equivalent(a, b, budget) is True:
                            result = "math_equivalent"
                    except Exception:
                        result = None  # 판정 불가 → 정렬하지 않는다
    cache[key] = result
    return result


def _match_weight(kind: MatchKind | None) -> int:
    """정렬 가중치. 문자 완전 일치/좌우반전 정규 일치는 동치보다 강한 신호다.

    같은 해집합을 갖는 등식은 서로 '동치'로 판정되므로(예: 2x=6+6 과 2x=12 는
    둘 다 해가 6) 동치만으로는 단계를 구별할 수 없다. 그래서 문자 근거가 있는
    신호(동일 텍스트·좌우반전 정규 일치)를 2, 동치를 1로 두어 삽입/생략 단계가
    뒤쪽 정렬을 흡수하지 않게 한다.

    normalized_text 를 identical_text 와 같은 2로 두는 이유: 둘 다 '기호 표기
    수준의 동일성'이라 추론이 필요 없고, math_equivalent 만이 SymPy 해집합
    비교(추론)를 요구하기 때문이다.
    """
    if kind in _STRONG_MATCH_KINDS:
        return 2
    if kind == "math_equivalent":
        return 1
    return 0


def _weighted_lcs_pairs(
    n: int, m: int, matches: list[list[MatchKind | None]]
) -> list[tuple[int, int]]:
    """가중치 합이 최대인 순서 보존 정렬(LCS). 동률은 앞쪽 우선."""
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = max(
                _match_weight(matches[i][j]) + dp[i + 1][j + 1],
                dp[i + 1][j],
                dp[i][j + 1],
            )
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        w = _match_weight(matches[i][j])
        if w and w + dp[i + 1][j + 1] == dp[i][j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _accept_pairs(
    pairs: list[tuple[int, int]],
    matrix: list[list[MatchKind | None]],
    width: int,
    gate: bool,
    attempt_texts: list[str],
    solution_texts: list[str],
) -> tuple[list[tuple[int, int]], list[int]]:
    """LCS 후보 쌍에서 '정렬해도 되는' 쌍만 남긴다 (Phase 7-2-2, 7-2-3).

    _match_kind()(match 판정)와 분리된 'alignment acceptance' 계층이다.

    - identical_text : 문자 근거가 있으므로 항상 채택한다(최우선).
    - normalized_text: '등식 좌우 반전'이라는 표기 수준의 근거이므로 강한
                       신호로 취급해 math_equivalent 모호성 검사를 받지 않는다.
                       단, 같은 정규 표현을 가진 정답 단계가 둘 이상이면
                       '어느 단계인지' 확정할 수 없으므로 여전히 거부한다.
    - math_equivalent: 단독으로는 신뢰하지 않는다. 학생 1단계가 여러 정답 단계와
                       동시에 성립하면 LCS 의 앞쪽 우선 동률 규칙이 임의의
                       선택을 확정해 ref_solution_step_no 가 오연결될 수 있다.

    gate=False 인 경우(단계 수가 같고 모두 위치 대응 가능한 엄격 1:1)는 구조적
    근거가 이미 있으므로 채택 게이트를 적용하지 않는다.
    """
    accepted: list[tuple[int, int]] = []
    ambiguous: list[int] = []
    taken: set[int] = set()
    for i, j in pairs:
        kind = matrix[i][j]
        if kind == "normalized_text":
            target = _swap_canonical(attempt_texts[i])
            same = [
                k
                for k in range(width)
                if k not in taken and _swap_canonical(solution_texts[k]) == target
            ]
            if len(same) > 1:
                ambiguous.append(i + 1)
                continue
        elif kind == "math_equivalent" and gate:
            if sum(1 for k in range(width) if matrix[i][k] is not None) > 1:
                ambiguous.append(i + 1)
                continue
        accepted.append((i, j))
        taken.add(j)
    return accepted, ambiguous


def align_attempt(
    problem: Problem,
    attempt: StudentAttempt,
    solution: CorrectSolution | None,
) -> StepAlignment:
    """학생 풀이 단계를 정답 풀이 단계에 대응시킨다.

    알고리즘(3단계):
      1) 단계 수가 같고 모든 같은 번호 쌍이 대응 가능하면 엄격한 1:1 정렬
         (학업상 정상 풀이의 기본값. 구조적 근거가 있으므로 채택 게이트 미적용)
      2) 그렇지 않으면 '일치 가능한 쌍의 최대 가중치'를 유지하는 가중 LCS.
         정렬은 순서를 뒤집지 않는다.
      3) acceptance 게이트(Phase 7-2-2): 모호한 math_equivalent 쌍은 버린다.

    정렬하지 않는 경우(모두 미정 None):
      - 텍스트가 다르고 등치로도 동치가 증명되지 않는 단계
      - solve 예산 소진으로 판정 불가한 단계
      - 비등식(식 단독) 단계
      - math_equivalent 로 여러 정답 단계에 동시에 성립해 모호한 단계
    '단순히 번호가 같다고' 정렬하지 않는다 — 반드시 동치 증거가 있어야 한다.
    '정렬하지 않는 것이 잘못 정렬하는 것보다 낫다' — ref_solution_step_no 가
    오연결되면 ErrorRecord 가 잘못된 정답 단계에 묶이므로 보수적으로 None 을 남긴다.
    """
    if solution is None:
        raise ValueError("step alignment 를 수행하려면 CorrectSolution 이 필요합니다")
    if not solution.belongs_to(problem):
        raise ValueError(
            f"solution.problem_id({solution.problem_id}) != problem.problem_id({problem.problem_id})"
        )

    att_steps = attempt.steps
    sol_steps = solution.steps
    n, m = len(att_steps), len(sol_steps)
    budget = _SolveBudget()
    cache: dict[tuple[str, str], MatchKind | None] = {}
    matrix: list[list[MatchKind | None]] = [
        [_match_kind(att_steps[i].latex_text, sol_steps[j].latex_text, budget, cache) for j in range(m)]
        for i in range(n)
    ]

    if n and m and n == m and all(matrix[i][i] is not None for i in range(n)):
        pairs = [(i, i) for i in range(n)]  # 1) 엄격한 1:1 (구조적 근거)
        gate = False
    else:
        pairs = _weighted_lcs_pairs(n, m, matrix)  # 2) 가중 LCS
        gate = True

    accepted, ambiguous = _accept_pairs(  # 3) 채택 게이트
        pairs, matrix, m, gate, [s.latex_text for s in att_steps], [s.latex_text for s in sol_steps]
    )

    aligned_pairs = [
        AlignedStep(
            attempt_step_no=att_steps[i].step_no,
            solution_step_no=sol_steps[j].step_no,
            match_kind=matrix[i][j] or "identical_text",
        )
        for i, j in accepted
    ]
    matched_attempt = {i for i, _ in accepted}
    matched_solution = {j for _, j in accepted}
    return StepAlignment(
        attempt_id=attempt.attempt_id,
        problem_id=problem.problem_id,
        solution_id=solution.solution_id,
        aligned=bool(aligned_pairs),
        pairs=aligned_pairs,
        unaligned_attempt_steps=[s.step_no for i, s in enumerate(att_steps) if i not in matched_attempt],
        unaligned_solution_steps=[s.step_no for j, s in enumerate(sol_steps) if j not in matched_solution],
        ambiguous_attempt_steps=ambiguous,
    )


def apply_alignment(attempt: StudentAttempt, alignment: StepAlignment) -> StudentAttempt:
    """정렬 결과를 학생 단계의 aligned_step_no 에 반영한 복사본을 반환한다."""
    if alignment.attempt_id != attempt.attempt_id:
        raise ValueError("alignment 의 attempt_id 가 다릅니다")
    return attempt.model_copy(
        update={
            "steps": [
                s.model_copy(
                    update={"aligned_step_no": alignment.solution_step_for(s.step_no)}
                )
                for s in attempt.steps
            ]
        }
    )


# ---------------------------------------------------------------------------
# Attempt / Analysis
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """timezone-aware UTC 현재 시각.

    attempted_at(명세 r49)은 항상 이 값으로 생성되며, naive datetime 이 들어오면
    UTC 로 간주해 정규화한다(저장/응답 모두 timezone-aware 를 보장).
    """
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """naive datetime 을 UTC 로 간주하고, aware 값은 UTC 로 변환한다."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StudentAttempt(BaseModel):
    """학생의 실제 풀이 시도."""

    attempt_id: str
    student_id: str
    problem_id: str
    solution_text: str = Field(..., description="학생 풀이 원문")
    attempted_at: datetime = Field(
        default_factory=utc_now,
        description=(
            "첫 분석 요청을 받은 서버 시각 (명세 r49 attempted_at). "
            "timezone-aware UTC 로 저장·응답된다. 분석 완료 시각이 아니다."
        ),
    )
    steps: list[SolutionStep] = Field(
        default_factory=list, description="미지정 시 solution_text에서 단계 분리"
    )
    analysis: "AttemptAnalysis | None" = None

    @field_validator("student_id")
    @classmethod
    def _valid_student_id(cls, v: str) -> str:
        # Phase 4 정책 재사용 (새 student_id 정책 만들지 않는다)
        return validate_student_id(v)

    @field_validator("attempted_at")
    @classmethod
    def _utc_aware(cls, v: datetime) -> datetime:
        return as_utc(v)

    @model_validator(mode="after")
    def _derive_steps(self) -> StudentAttempt:
        if not self.steps and self.solution_text:
            self.steps = [
                SolutionStep(step_no=i, latex_text=text)
                for i, text in enumerate(split_steps(self.solution_text), start=1)
            ]
        return self

    def with_analysis(self, analysis: AttemptAnalysis) -> StudentAttempt:
        """분석 결과를 연결한 복사본을 반환한다 (원본은 불변)."""
        return self.model_copy(update={"analysis": analysis})


class AttemptAnalysis(BaseModel):
    """StudentAttempt 1건에 대한 분석 결과."""

    attempt: StudentAttempt
    attempt_id: str
    problem_id: str
    student_id: str
    correct: bool
    observed_skill_ids: list[str] = Field(
        default_factory=list, description="이번 시도에서 실제로 관측된 Skill"
    )
    errors: list[ErrorRecord] = Field(default_factory=list)
    suppressed_downstream_steps: list[int] = Field(
        default_factory=list,
        description=(
            "연쇄 오류로 판단되어 ErrorRecord 로 기록하지 않은 단계(1-based). "
            "verdict 는 valid=False 였으나 이전 오류에서 이어진 결과다 (Phase 7-2-1)."
        ),
    )
    observations: list[KnowledgeObservation] = Field(default_factory=list)
    alignment: StepAlignment | None = Field(
        None, description="정답 풀이 ↔ 학생 풀이 단계 정렬 결과 (solution 미제공 시 None)"
    )
    states: dict[str, str] = Field(default_factory=dict, description="skill_id → state")
    mastery: dict[str, float] = Field(default_factory=dict, description="skill_id → 숙련도")
    api_response: dict | None = Field(
        None, description="기존 AnalyzeSolutionResponse 규격으로 변환한 dict (호환 확인용)"
    )


StudentAttempt.model_rebuild()


# ---------------------------------------------------------------------------
# 흐름 연결
# ---------------------------------------------------------------------------

def _check_parsing(problem: Problem, steps: list[str], solution_text: str) -> None:
    """analyze()와 동일한 입력 정책 (에러 코드/메시지 유지)."""
    if not steps:
        raise AnalysisError("E_EMPTY_SOLUTION", "풀이 단계가 비어 있습니다.")
    if len(problem.problem_latex) > _MAX_PROBLEM_CHARS:
        raise AnalysisError("E_INPUT_TOO_LARGE", "문제식이 너무 깁니다.")
    if len(solution_text) > _MAX_SOLUTION_CHARS:
        raise AnalysisError(
            "E_INPUT_TOO_LARGE", f"풀이 입력이 너무 깁니다 (최대 {_MAX_SOLUTION_CHARS}자)."
        )
    if len(steps) > _MAX_STEPS:
        raise AnalysisError(
            "E_INPUT_TOO_LARGE", f"풀이 단계가 너무 많습니다 (최대 {_MAX_STEPS}단계)."
        )
    node = parse_line(problem.problem_latex)
    if node is None or node[0] != "eq":
        raise AnalysisError("E_BAD_PROBLEM", "문제식을 인식할 수 없습니다.")


def _alignment_is_usable(alignment: "StepAlignment | None") -> bool:
    """정렬 정보를 '연쇄 오류 판정' 근거로 쓸 수 있는가.

    정답 풀이가 없거나, 정렬이 하나도 성립하지 않아(단계가 전부 모호) 정보가
    비어 있으면 추측하지 않는다 (요구사항: 불확실하면 추측 금지).
    """
    return (
        alignment is not None
        and alignment.solution_id is not None
        and alignment.aligned
        and bool(alignment.pairs)
    )


def _classify_error_steps(
    problem: Problem,
    verdict,
    step_texts: list[str],
    attempt_id: str,
    alignment: "StepAlignment | None",
) -> tuple[list[ErrorRecord], list[int]]:
    """verdict + 앞뒤 단계 + 정답 정렬로 독립 오류와 연쇄 오류를 나눈다.

    후보는 valid=False 인 모든 단계다. 하지만 '모든 False = 독립 오류'가 아니다.

    연쇄(downstream) 판별은 두 신호의 OR 다:
      (1) 기준 줄 자체가 오류(valid=False) — 기존 규칙(Phase 7-2-1)
      (2) 기준 줄이 '학생이 틀린 값을 올바르게 이어받아 만든' 줄이고(기준이
          verified), 그 기준 줄도 오류 후보도 모두 정답 경로 밖에 있을 때 —
          Phase 7-3 추가. 이 경우 후보는 이미 벗어난 경로 위의 계산 차이다.

    (2) 는 두 줄이 '둘 다' off-path 일 때만 적용한다. 후보 단계가 정답 경로로
    돌아와 있으면(예: 틀린 줄 위에서 우연히 정답에 도달) 그 오류는 독립적이다.

    (2) 는 정답 정렬이 실제로 성립했을 때만 판단한다(추측 금지). 기준 줄이
    verified 가 아니거나 정렬이 불확실하면 판단하지 않는다.

    개념 오류/오독은 연쇄 구간이어도 기록한다 — 새 원인일 수 있기 때문이다.
    '기준 줄이 유효한 단계'인 오류는 항상 독립 오류로 기록한다.
    """
    errors: list[ErrorRecord] = []
    downstream: list[int] = []
    candidate_indices = [i for i, v in enumerate(verdict.valid) if v is False]
    use_alignment = _alignment_is_usable(alignment)

    for i in candidate_indices:
        ref = verdict.ref_step_no[i] if i < len(verdict.ref_step_no) else None
        ref_idx = (ref - 1) if ref is not None else None
        baseline_is_error = ref_idx is not None and verdict.valid[ref_idx] is False
        # 기준 줄이 스스로 '오류'가 아니라면, 즉 학생이 틀린 값을 올바르게 이어받아
        # 만든 줄이라면, 그 정렬 결과로 올바른 경로에서 벗어났는지 본다.
        baseline_verified = ref_idx is not None and verdict.valid[ref_idx] is True
        baseline_off_path = (
            use_alignment
            and ref is not None
            and baseline_verified
            and alignment.solution_step_for(ref) is None
        )
        # 후보 단계가 정답 경로로 '돌아와' 있으면 연쇄가 아니라 독립 오류다.
        # (학생이 틀린 줄 위에서 우연히 정답에 도달하는 경우 등)
        candidate_off_path = (
            use_alignment and alignment.solution_step_for(i + 1) is None
        )
        in_error_run = baseline_is_error or (baseline_off_path and candidate_off_path)
        info = classify(problem.problem_latex, step_texts, i)
        if in_error_run and info.error_type not in ("concept_error", "comprehension"):
            # 이전 오류에서 이어진 계산 차이 — 독립 오류로 확정하지 않는다
            downstream.append(i + 1)
            continue
        errors.append(
            ErrorRecord(
                error_id=f"{attempt_id}-E{i + 1}",
                attempt_id=attempt_id,
                step_no=i + 1,
                ref_step_no=ref,
                ref_solution_step_no=(
                    alignment.solution_step_for(ref)
                    if alignment is not None and ref is not None
                    else None
                ),
                error_type=info.error_type,
                error_subtype=info.error_subtype,
                skill_ids=list(info.skill_ids),
                misconception_ids=list(info.misconception_ids),
                confidence=info.confidence,
                evidence_latex=step_texts[i],
                description_ko=_describe_ko(
                    info.error_type, info.error_subtype, list(info.misconception_ids)
                ),
            )
        )
    return errors, downstream


def analyze_attempt(
    problem: Problem,
    attempt: StudentAttempt,
    solution: CorrectSolution | None = None,
    engine: str = "rule",
) -> AttemptAnalysis:
    """Problem + StudentAttempt → Error[] → KnowledgeObservation[] → Knowledge State.

    기존 analyze()와 동일한 verifier/classifier를 재사용하되, 관측 단위를
    '문제 하나가 아닌 실제로 관측된 Skill'로 세운다. 관측되지 않은 Skill은
    Knowledge State를 건드리지 않는다.
    """
    if attempt.problem_id != problem.problem_id:
        raise ValueError(
            f"attempt.problem_id({attempt.problem_id}) != problem.problem_id({problem.problem_id})"
        )
    if solution is not None and not solution.belongs_to(problem):
        raise ValueError(
            f"solution.problem_id({solution.problem_id}) != problem.problem_id({problem.problem_id})"
        )

    steps = attempt.steps
    step_texts = [s.latex_text for s in steps]
    _check_parsing(problem, step_texts, attempt.solution_text)

    verdict = verify(problem.problem_latex, step_texts)
    if not any(p is not None for p in verdict.parsed):
        raise AnalysisError(
            "E_UNRECOGNIZED",
            "모든 풀이 단계를 인식하지 못했습니다. 직접 입력으로 다시 시도해 주세요.",
        )
    if not any(v is not None for v in verdict.valid):
        raise AnalysisError(
            "E_UNVERIFIABLE",
            "풀이 단계를 대조 판정할 수 없습니다. 문제식과 풀이를 확인해 주세요.",
        )

    # 단계별 unrecognized 을 StudentAttempt.step 에 반영 (원본 보존)
    unrecognized_set = set(verdict.unrecognized)
    marked_steps = [
        step.model_copy(update={"unrecognized": step.step_no in unrecognized_set})
        for step in steps
    ]
    attempt = attempt.model_copy(update={"steps": marked_steps})

    # 실제로 관측된 skill.
    #   - 단계에 skill_ids 선언이 있으면 '문제 선언분 ∩ 단계 선언분'만 관측
    #   - 선언이 없으면 문제의 대표 추적 skill 1개만 관측 (과대 귀속 방지.
    #     관측되지 않은 skill 을 correct 처리하지 않기 위한 보수적 기본값이며,
    #     기존 analyze() 와 동일하게 한 문제당 한 skill 만 갱신한다)
    declared = {sid for step in steps for sid in step.skill_ids}
    if declared:
        observed = [s for s in problem.skills if s in declared]
    else:
        observed = [problem.primary_tracking_skill()]

    errors: list[ErrorRecord] = []
    downstream_steps: list[int] = []
    alignment: StepAlignment | None = None
    if solution is not None:
        # 정답 풀이 정렬(Phase 7-1). 학생 단계의 aligned_step_no 를 채우고,
        # 오류 단계의 정답 대응(ref_solution_step_no)을 얻는다.
        alignment = align_attempt(problem, attempt, solution)
        attempt = apply_alignment(attempt, alignment)

    # 다중 오류 수집(Phase 7-2-1)
    # 후보는 verifier 가 valid=False 로 판정한 모든 단계다. 단 '모든 False = 독립
    # 오류'가 아니므로 연쇄 오류를 걸러 낸다 (아래 _classify_error_steps).
    errors, downstream_steps = _classify_error_steps(
        problem, verdict, step_texts, attempt.attempt_id, alignment
    )

    primary_skill: str
    primary_error_type: str | None = None
    primary_error_subtype: str | None = None
    primary_misconception: str | None = None
    primary_confidence: float | None = None

    if verdict.first_error_index is None:
        correct = True
        primary_skill = problem.primary_tracking_skill()
    else:
        correct = False
        primary_skill = errors[0].skill_ids[0] if errors and errors[0].skill_ids else SKILL_LINEAR
        primary_error_type = errors[0].error_type if errors else None
        primary_error_subtype = errors[0].error_subtype if errors else None
        # 기존 단일 필드(하위호환) — 다중 오개념 목록의 첫 항목을 대표로 삼는다
        primary_misconception = (
            errors[0].misconception_ids[0] if errors and errors[0].misconception_ids else None
        )
        primary_confidence = errors[0].confidence if errors else None

    # KnowledgeObservation — BKT outcome 은 이진(correct | incorrect).
    #  - 정답: 관측된 skill 만 correct. 관측 안 된 skill 은 건드리지 않는다.
    #  - 오답: 오류를 일으킨 skill 은 정의상 '시도한' skill 이므로 관측된 것으로
    #          본다(문제에 미선언이어도 갱신한다 — 기존 analyze() 와 동일).
    # 중복 방지: 한 attempt 에서 같은 tracking skill 은 최대 1회만 갱신한다.
    if correct:
        target_skills = [s for s in observed if is_tracking_skill(s)]
        skill_source: dict[str, ErrorRecord | None] = {s: None for s in target_skills}
    else:
        target_skills = []
        skill_source = {}
        for err in errors:  # 첫 오류가 같은 skill 을 언급하면 그 오류를 원인으로 쓴다
            for sid in err.skill_ids:
                if is_tracking_skill(sid) and sid not in skill_source:
                    skill_source[sid] = err
                    target_skills.append(sid)
        observed = list(dict.fromkeys(observed + target_skills))

    observations: list[KnowledgeObservation] = []
    for n, skill_id in enumerate(target_skills, start=1):
        source = skill_source.get(skill_id)
        observations.append(
            KnowledgeObservation(
                observation_id=f"{attempt.attempt_id}-O{n}",
                attempt_id=attempt.attempt_id,
                problem_id=problem.problem_id,
                student_id=attempt.student_id,
                skill_id=skill_id,
                outcome="correct" if correct else "incorrect",
                error_type=source.error_type if source is not None else None,
                error_id=source.error_id if source is not None else None,
            )
        )
    kb = get_engine(engine)
    states: dict[str, str] = {}
    mastery: dict[str, float] = {}
    for obs in observations:
        kb.update_from_observation(
            obs.student_id, obs.skill_id, obs.outcome_argument(), obs.error_type
        )
        states[obs.skill_id] = kb.state(obs.student_id, obs.skill_id)
        mastery[obs.skill_id] = kb.mastery(obs.student_id, obs.skill_id)

        api_response = {
            "correct": correct,
        "error_step": errors[0].step_no if errors else None,
        "error_type": primary_error_type,
        "skill": primary_skill,
        "state": states.get(primary_skill, kb.state(attempt.student_id, primary_skill)),
        "mastery": mastery.get(primary_skill, kb.mastery(attempt.student_id, primary_skill)),
        "misconception_id": primary_misconception,
        "confidence": primary_confidence,
        "steps_latex": step_texts,
        "valid": verdict.valid,
        "unrecognized": verdict.unrecognized,
        "analysis_status": analysis_status_of(verdict),
        "skills": list(errors[0].skill_ids) if errors else [primary_skill],
        "skill_ids": list(errors[0].skill_ids) if errors else [primary_skill],
        "error_subtype": primary_error_subtype,
        "misconception_ids": [primary_misconception] if primary_misconception else [],
    }

    return AttemptAnalysis(
        attempt=attempt,
        attempt_id=attempt.attempt_id,
        problem_id=problem.problem_id,
        student_id=attempt.student_id,
        correct=correct,
        observed_skill_ids=list(observed),
        errors=errors,
        suppressed_downstream_steps=downstream_steps,
        observations=observations,
        alignment=alignment,
        states=states,
        mastery=mastery,
        api_response=api_response,
    )


__all__ = [
    "AlignedStep",
    "AttemptAnalysis",
    "CorrectSolution",
    "ErrorRecord",
    "KnowledgeObservation",
    "Problem",
    "SKILL_LINEAR",
    "SolutionStep",
    "StepAlignment",
    "StudentAttempt",
    "align_attempt",
    "analyze_attempt",
    "apply_alignment",
]
