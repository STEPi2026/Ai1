"""Pydantic 요청/응답 스키마 — 입출력 JSON 계약 (Step 4)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EngineName = Literal["rule", "bkt"]

# 앱 r49: 분석 완료/사람 확인 필요/판정 불가 3상태.
# - ANALYZED      : 분석 가능한 모든 단계가 판정됨 (correct=true/false 와 별개)
# - REVIEW_REQUIRED: 일부만 판정되고 unrecognized 또는 판정 보류 단계가 남아 있음
# - UNKNOWN       : 신뢰할 수 있는 단계 판정이 하나도 없음 (기존 422 에러에 대응)
AnalysisStatus = Literal["ANALYZED", "REVIEW_REQUIRED", "UNKNOWN"]


class AnalyzeSolutionRequest(BaseModel):
    # S12: 분석 API 요청 계약이므로 알 수 없는 필드를 조용히 무시하지 않는다
    # (오타로 옵션이 사라지는 silent failure 방지). 응답 모델과 다른 스키마에는
    # 일단 적용하지 않는다.
    model_config = ConfigDict(extra="forbid")

    problem_id: str | None = Field(None, description="문제 ID (선택)")
    problem_latex: str = Field(..., description="문제식 LaTeX 예: 2(x-3)=6")
    solution_text: str = Field(
        ..., description="학생 풀이 텍스트. 줄바꿈 또는 화살표(→)로 단계 구분"
    )
    student_id: str = Field("anonymous", description="학생 식별자 (상태 저장 키)")
    engine: EngineName = Field("rule", description="knowledge state 엔진: rule(Stage0) | bkt(Stage1)")
    problem_skill: str = Field(
        "linear_equation",
        min_length=1,
        description=(
            "문제의 tracking Skill ID — Knowledge State/BKT에 직접 연결된다. "
            "frozen taxonomy(data/skills.json)의 bkt_eligible=true인 leaf만 허용한다. "
            "미등록 ID는 E_UNKNOWN_SKILL, group node/future skill은 E_SKILL_NOT_TRACKABLE로 "
            "거절된다(422). 문제의 개념적 분류는 response의 skills/skill_ids를 참조할 것."
        ),
    )


class SolutionStepInput(BaseModel):
    """요청으로 들어오는 정답 풀이 단계 1개."""

    model_config = ConfigDict(extra="forbid")

    latex_text: str = Field(..., description="정답 풀이 단계 원문")
    skill_ids: list[str] = Field(default_factory=list, description="이 단계의 관련 Skill ID")


class CorrectSolutionInput(BaseModel):
    """요청으로 들어오는 정답 풀이 (최초 등장 등록용)."""

    model_config = ConfigDict(extra="forbid")

    solution_id: str = Field(..., description="정답 풀이 식별자")
    steps: list[SolutionStepInput] = Field(..., min_length=1, description="정답 풀이 단계")
    answer_latex: str = Field(..., description="최종 답")
    is_primary: bool = Field(True, description="대표 정답 풀이 여부")


class AnalyzeAttemptRequest(BaseModel):
    """POST /api/v1/attempts/{attempt_id}/analyze 요청.

    저장소에 아직 없는 Problem/StudentAttempt 는 이 요청으로 '최초 등장 등록'한다.
    이미 등록된 attempt_id 는 저장본을 사용하므로 재요청이 이력을 덮어쓰지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    problem_id: str = Field(..., description="문제 식별자")
    problem_latex: str | None = Field(
        None, description="미등록 문제를 최초 등록할 때의 문제식 (등록되어 있으면 무시)"
    )
    problem_skills: list[str] = Field(
        default_factory=list,
        description="문제의 개념적 Skill 목록 (group node 허용). 미등록 문제 등록 시 사용",
    )
    student_id: str = Field(..., description="학생 식별자")
    solution_text: str = Field(..., description="학생 풀이 원문")
    correct_solution: CorrectSolutionInput | None = Field(
        None, description="정답 풀이 (미제공 시 단계 정렬 없이 분석)"
    )
    engine: EngineName = Field("rule", description="knowledge state 엔진")
    problem_question_text: str | None = Field(
        None,
        description=(
            "문제 표시 텍스트 (명세 problems.question r25). 미등록 문제를 최초 등록할 "
            "때만 저장되고, 이미 등록된 문제면 무시된다(저장본이 source of truth)."
        ),
    )
    problem_concept_name: str | None = Field(
        None,
        description=(
            "단원/개념 표시명 (명세 concepts.name r33). skill_id 에서 자동 대응시키지 "
            "않으며 명시 입력만 저장한다. 최초 등록 시만 반영."
        ),
    )
    problem_unit_name: str | None = Field(
        None,
        description="표시용 단원명. 명세 대응 컬럼이 없는 표시 전용 값. 최초 등록 시만 반영.",
    )


class StepView(BaseModel):
    """명세서 `step_list` 항목."""

    step_no: int = Field(..., ge=1, description="1-based 단계 번호")
    latex_text: str = Field(..., description="단계 원문")
    is_correct: bool | None = Field(
        None, description="기존 valid 의미 그대로: True=정답, False=오류, None=판정 보류"
    )
    status: Literal["correct", "error", "unreviewable"] = Field(
        ..., description="is_correct 를 문자 상태로 표현 (None 은 판정 보류)"
    )
    skill_ids: list[str] = Field(default_factory=list)
    aligned_solution_step_no: int | None = Field(
        None, description="정렬된 정답 풀이 단계 (미정렬이면 null)"
    )
    unrecognized: bool = Field(False, description="인식 실패/판정 불가 단계 여부")


class AnalyzeAttemptResponse(BaseModel):
    """POST /api/v1/attempts/{attempt_id}/analyze 응답."""

    model_config = ConfigDict(extra="forbid")

    # --- 식별자 / 상태 ---
    analysis_id: str = Field(
        ..., description="저장소가 부여한 분석 식별자 (멱등 재요청 시 기존 값 그대로)"
    )
    attempt_id: str
    problem_id: str
    student_id: str
    analysis_status: AnalysisStatus = Field(..., description="ANALYZED/REVIEW_REQUIRED/UNKNOWN")
    analysis_count: int = Field(
        ...,
        ge=1,
        description="이 attempt 에 쌓인 분석 이력 수 (멱등 재요청은 증가하지 않음)",
    )
    attempted_at: datetime = Field(
        ...,
        description=(
            "첫 분석 요청을 받은 서버 시각 (명세 r49). timezone-aware UTC, ISO 8601. "
            "멱등 재요청·충돌 거절로 변하지 않으며, 분석 완료 시각이 아니다."
        ),
    )
    # --- 기존 결과 필드 (의미 변경 없음) ---
    correct: bool
    error_step: int | None = Field(None, description="첫 오류 줄 번호 (1-based)")
    first_error_step_index: int | None = Field(
        None,
        description="명세서 필드. error_step 과 동일한 1-based 값 (기존 계약 유지)",
    )
    error_type: str | None = None
    error_subtype: str | None = None
    skill: str
    state: Literal["mastered", "learning", "needs_practice"]
    mastery: float
    misconception_id: str | None = None
    confidence: float | None = None
    steps_latex: list[str]
    valid: list[bool | None]
    unrecognized: list[int]
    # --- 단계 목록 ---
    step_list: list[StepView] = Field(default_factory=list, description="명세서 step_list")
    # --- 다중 오류 / 관측 ---
    # 직렬화된 ErrorRecord / KnowledgeObservation dict 다.
    # app.schemas → app.pipeline 임포트는 순환 참조가 되므로(analyzer 가 schemas 를
    # 임포트) 중첩 타입을 느슨하게 두고, 키 집합은 테스트로 고정한다.
    errors: list[dict] = Field(default_factory=list, description="독립 오류(ErrorRecord) 직렬화")
    observations: list[dict] = Field(
        default_factory=list, description="KnowledgeObservation 직렬화"
    )
    misconception_tags: list[str] = Field(
        default_factory=list,
        description="명세서 misconception_tag. frozen taxonomy 에 있는 값만 담긴다",
    )
    states: dict[str, str] = Field(default_factory=dict, description="관측된 skill_id → state")
    mastery_by_skill: dict[str, float] = Field(
        default_factory=dict, description="관측된 skill_id → 숙련도"
    )


class AnalysisSummaryItem(BaseModel):
    """분석 이력 목록 항목 (저장 시점 스냅샷의 요약)."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    attempt_id: str
    problem_id: str
    student_id: str
    analysis_status: AnalysisStatus = Field(..., description="ANALYZED/REVIEW_REQUIRED/UNKNOWN")
    correct: bool
    error_type: str | None = None
    error_subtype: str | None = None
    skill: str
    state: Literal["mastered", "learning", "needs_practice"]
    mastery: float
    misconception_id: str | None = None
    first_error_step_index: int | None = Field(
        None, description="첫 오류 줄 번호 (1-based, 없으면 null)"
    )
    confidence: float | None = None
    error_count: int = Field(..., ge=0, description="독립 오류(ErrorRecord) 건수")
    observation_count: int = Field(..., ge=0, description="KnowledgeObservation 건수")


class AnalysisListResponse(BaseModel):
    """GET /api/v1/attempts/{attempt_id}/analyses 응답."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    analysis_count: int = Field(..., ge=0, description="이 attempt 에 저장된 분석 이력 수")
    latest_analysis_id: str | None = Field(
        None, description="가장 최근에 저장된 analysis_id (이력이 없으면 null)"
    )
    order: Literal["created_asc"] = Field(
        "created_asc",
        description=(
            "항목 정렬은 저장소 생성 순서(저장 순) 기준 오래된→최신 오름차순이다. "
            "즉 analyses[0] 이 가장 오래된 분석이고 analyses[-1] 이 가장 최근 분석이다. "
            "최신순 조회는 reverse=true 로 뒤집는다."
        ),
    )
    analyses: list[AnalysisSummaryItem] = Field(default_factory=list)


class AnalysisDetailResponse(AnalyzeAttemptResponse):
    """GET /api/v1/attempts/{attempt_id}/analyses/{analysis_id} 응답.

    저장 시점의 AnalysisRecord 스냅샷이며, 조회는 Knowledge State 를 건드리지
    않는다. POST 응답과 동일한 본문 필드에 raw_result(응답 원본 전체)를 더한다.
    """

    model_config = ConfigDict(extra="forbid")

    raw_result: dict = Field(
        ..., description="저장 시점의 응답 원본 스냅샷 (ai_analysis_results.raw_result 대응)"
    )


class StudentHistoryItem(BaseModel):
    """학생 학습 이력 1건 (Attempt + 최신 분석 요약).

    원본 분석 결과 전체는 중복 노출하지 않는다. 상세는
    `detail_path` 가 가리키는 `GET /api/v1/attempts/{attempt_id}/analyses/{analysis_id}`
    를 사용한다.
    """

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    problem_id: str
    attempted_at: datetime = Field(
        ...,
        description=(
            "첫 분석 요청을 받은 서버 시각 (명세 r49). timezone-aware UTC, ISO 8601. "
            "정렬 기준이며, 동률이면 나중에 저장된 기록이 먼저 온다."
        ),
    )
    latest_analysis_id: str | None = Field(
        None, description="이 attempt 의 가장 최근 analysis_id (분석 없으면 null)"
    )
    detail_path: str | None = Field(
        None,
        description=(
            "상세 조회 경로. analysis_id 의 '#' 은 %23 으로 인코딩되어 있다 "
            "(경로에 그대로 쓰면 URL fragment 로 잘린다). 분석 없으면 null."
        ),
    )
    analysis_count: int = Field(..., ge=0, description="이 attempt 에 저장된 분석 이력 수")
    # --- 저장된 최신 분석 스냅샷의 요약 (분석이 없으면 모두 null) ---
    analysis_status: AnalysisStatus | None = None
    correct: bool | None = None
    error_step: int | None = Field(None, description="첫 오류 줄 번호 (1-based)")
    first_error_step_index: int | None = Field(
        None, description="명세서 필드. error_step 과 동일한 1-based 값"
    )
    error_type: str | None = None
    error_subtype: str | None = None
    skill: str | None = None
    state: Literal["mastered", "learning", "needs_practice"] | None = None
    mastery: float | None = None
    misconception_id: str | None = None
    misconception_tags: list[str] = Field(default_factory=list)
    error_count: int = Field(..., ge=0, description="독립 오류(ErrorRecord) 건수")
    observation_count: int = Field(..., ge=0, description="KnowledgeObservation 건수")
    step_count: int = Field(..., ge=0, description="제출된 풀이 단계 수")
    # --- 문제 표시 메타데이터 (미등록/미제공이면 null, 추측하지 않는다) ---
    problem_text: str | None = Field(
        None,
        description=(
            "문제 표시 텍스트 (명세 problems.question r25). Problem.question_text 에서 "
            "가져오며, 메타데이터가 없으면 null."
        ),
    )
    concept_name: str | None = Field(
        None,
        description="단원/개념 표시명 (명세 concepts.name r33). skill_id 에서 파생하지 않는다.",
    )
    unit_name: str | None = Field(
        None, description="표시용 단원명. 명세 대응 컬럼이 없는 표시 전용 값."
    )


class StudentHistoryResponse(BaseModel):
    """GET /api/v1/students/{student_id}/history 응답."""

    model_config = ConfigDict(extra="forbid")

    student_id: str
    items: list[StudentHistoryItem] = Field(default_factory=list)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    total: int = Field(..., ge=0, description="요청한 학생의 전체 기록 수 (페이지 무관)")
    total_pages: int = Field(..., ge=0, description="ceil(total / page_size). total=0 이면 0")
    order: Literal["attempted_at_desc"] = Field(
        "attempted_at_desc",
        description=(
            "정렬은 attempted_at 내림차순(최신 제출 먼저)이다. 시각이 같은 항목의 "
            "보조 기준은 '나중에 저장된 것 먼저'(저장 인덱스 내림차순)라 순서가 "
            "안정적이다. 인덱스가 클수록 먼저 저장된 오래된 기록이다."
        ),
    )


class MisconceptionTopItem(BaseModel):
    """반복 오개념 TOP 항목 1개 (명세 웹 r30 / 앱 r64)."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(..., ge=1, description="1부터 시작하는 순위")
    misconception_id: str = Field(..., description="AI① frozen taxonomy 의 misconception_id")
    name: str = Field(..., description="taxonomy 의 이름")
    description: str = Field(..., description="taxonomy 의 설명")
    attempt_count: int = Field(
        ..., ge=1, description="이 오개념이 등장한 서로 다른 Attempt 수"
    )
    related_skill_ids: list[str] = Field(
        default_factory=list, description="taxonomy 의 related_skill_ids (표시용, BKT 키 아님)"
    )


class MisconceptionTopResponse(BaseModel):
    """GET /api/v1/students/{student_id}/misconceptions/top 응답."""

    model_config = ConfigDict(extra="forbid")

    student_id: str
    limit: int = Field(..., ge=1, description="반환할 최대 항목 수 (기본 5)")
    order: Literal["attempt_count_desc"] = Field(
        "attempt_count_desc",
        description=(
            "정렬은 attempt_count 내림차순, 동률은 misconception_id 오름차순으로 고정한다. "
            "기준이 저장소 스냅샷이라 반복 조회해도 순서가 같다."
        ),
    )
    distinct_misconception_count: int = Field(
        ..., ge=0, description="집계된 서로 다른 오개념 총 개수 (limit 절단 전)"
    )
    items: list[MisconceptionTopItem] = Field(default_factory=list)


class AnalyzeSolutionResponse(BaseModel):
    """AI① 최소 계약 + 확장 필드 (하위호환)."""

    correct: bool = Field(
        ...,
        description=(
            "현재 입력된 풀이에서 오류가 검출되지 않았는지를 뜻한다. "
            "풀이 과정의 완전성이나 최종 정답의 독립적인 증명을 의미하지 않는다 — "
            "단계 생략(예: 정답만 제출)도 correct=true가 될 수 있으며, "
            "unrecognized 줄이 있으면 부분 검증에 해당한다."
        ),
    )
    error_step: int | None = Field(..., description="첫 오류 줄 번호 (1-based, 오류 없으면 null)")
    error_type: str | None = Field(
        ..., description="calculation | concept_error | procedure | comprehension | null"
    )
    skill: str = Field(..., description="매핑된 Skill (오류 시 오류 스킬, 정답 시 문제 스킬)")
    state: Literal["mastered", "learning", "needs_practice"] = Field(
        ..., description="갱신된 Knowledge State"
    )
    mastery: float = Field(..., description="숙련도 (rule=상태 대표값 / bkt=p_known)")
    misconception_id: str | None = Field(..., description="오개념 트리 node_code, 없으면 null")
    confidence: float | None = Field(..., description="분류 신뢰도, 정답 시 null")
    steps_latex: list[str] = Field(..., description="인식된 풀이 단계 원문")
    valid: list[bool | None] = Field(
        ...,
        description="단계별 동치 판정. null = 판정 보류(인식 실패/식 단독 줄). "
        "인식 실패 줄은 건너뛰고 앞의 판정 가능 줄과 대조한다.",
    )
    unrecognized: list[int] = Field(
        ..., description="인식 실패 또는 판정 불가(식 단독) 줄 번호 (1-based)"
    )
    analysis_status: AnalysisStatus | None = Field(
        None,
        description=(
            "분석 상태 (앱 r49). ANALYZED=모든 단계 판정 완료, "
            "REVIEW_REQUIRED=일부만 판정되어 사람 확인 필요, UNKNOWN=판정 불가. "
            "요청/입력 오류(422·413) 응답에는 이 필드가 없으며, "
            "구버전 payload 처리를 위해 null 도 허용한다."
        ),
    )
    # --- additive 필드 (S3) — 위 기존 필드는 삭제/개명하지 않는다 ---
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "문제와 연결된 Skill ID 목록 (taxonomy 기준). 개념적 분류이므로 "
            "group node가 포함될 수 있다. 정답 시 문제의 tracking skill 1개."
        ),
    )
    skill_ids: list[str] = Field(
        default_factory=list,
        description=(
            "skills의 canonical alias. 호환성 때문에 현재는 skills와 동일한 값을 담는다. "
            "향후 정렬·canonicalization이 필요해질 때 이 필드에 반영할 예정."
        ),
    )
    error_subtype: str | None = Field(
        None,
        description=(
            "오류 세부 형태(error_subtype_id). taxonomy(data/error_types.json)의 "
            "error_type 하위 subtype 중 하나이며, 정답 시 null."
        ),
    )
    misconception_ids: list[str] = Field(
        default_factory=list,
        description=(
            "오류와 연결된 오개념 ID 목록(misconception_id). 실제 근거가 있을 때만 "
            "채워지므로 단순 계산 오류는 빈 목록이다. 정답 시 빈 목록."
        ),
    )
