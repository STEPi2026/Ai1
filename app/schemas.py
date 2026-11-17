"""Pydantic 요청/응답 스키마 — 입출력 JSON 계약 (Step 4)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EngineName = Literal["rule", "bkt"]

# 앱 r49: 분석 완료/사람 확인 필요/판정 불가 3상태.
# - ANALYZED      : 분석 가능한 모든 단계가 판정됨 (correct=true/false 와 별개)
# - REVIEW_REQUIRED: 일부만 판정되고 unrecognized 또는 판정 보류 단계가 남아 있음
# - UNKNOWN       : 신뢰할 수 있는 단계 판정이 하나도 없음 (기존 422 에러에 대응)
AnalysisStatus = Literal["ANALYZED", "REVIEW_REQUIRED", "UNKNOWN"]

# A6 P1: target_form 허용값.
# 실제 값 검증은 파이프라인(validate_problem_mode)이 수행한다 — 커스텀 오류 계약
# (422 E_INVALID_TARGET_FORM / retryable=false)을 유지하기 위해서다. 스키마에는
# 소비자가 허용값을 알 수 있도록 **문서용 enum** 만 노출하고 런타임 제약은 추가하지
# 않는다. 목록은 pipeline.forms.ALLOWED_TARGET_FORMS 와 일치해야 한다
# (tests/test_schema_target_form_enum.py 가 고정).
TARGET_FORM_VALUES = ("integer", "expanded", "factored")
TargetForm = Annotated[
    str,
    Field(max_length=32, json_schema_extra={"enum": list(TARGET_FORM_VALUES)}),
]


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
    problem_type: Literal["equation", "expression"] = Field(
        "equation",
        description=(
            "A6 P1: 문제 유형. 생략하면 equation(기존 동작). expression 이면 "
            "정답식 없이 줄↔직전 줄 동치로 판정한다. 부등식은 범위 밖이다."
        ),
    )
    target_form: TargetForm | None = Field(
        None,
        description=(
            "A6 P1: expression 문제의 요구 결과 형태(integer|expanded|factored). "
            "None 이면 동치만 검사한다. equation 과 함께 보내면 "
            "422 E_INVALID_TARGET_FORM."
        ),
    )
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
        description=(
            "문제의 개념적 Skill 목록 (group node 허용). 미등록 문제 등록 시 사용. "
            "AI① 내부 frozen skill_id 를 쓰거나, Backend 공식 Skill 코드"
            "(SKL-*) 를 보낸다 — 코드는 crosswalk(approved + provisional) 에 있는 "
            "것만 역변환하고, 모르는 코드는 422 로 거절한다(기본 Skill 추측 금지)."
        ),
    )
    student_id: str | int = Field(
        ...,
        description=(
            "학생 식별자. AI① 내부는 문자열로 다루며, 정수를 보내면 십진 문자열로 "
            "정규화한다(접두사 없음). 문자열은 선행 0 을 지우지 않고 그대로 보존한다."
        ),
    )
    solution_text: str = Field(..., description="학생 풀이 원문")
    correct_solution: CorrectSolutionInput | None = Field(
        None, description="정답 풀이 (미제공 시 단계 정렬 없이 분석)"
    )
    engine: EngineName = Field("rule", description="knowledge state 엔진")
    problem_type: Literal["equation", "expression"] = Field(
        "equation",
        description=(
            "A6 P1: 문제 유형. 생략하면 equation(기존 동작). expression 이면 "
            "정답식 없이 줄↔직전 줄 동치로 판정한다. 부등식은 범위 밖이다."
        ),
    )
    target_form: TargetForm | None = Field(
        None,
        description=(
            "A6 P1: expression 문제의 요구 결과 형태(integer|expanded|factored). "
            "None 이면 동치만 검사한다. equation 과 함께 보내면 "
            "422 E_INVALID_TARGET_FORM."
        ),
    )
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

    @field_validator("student_id", mode="before")
    @classmethod
    def _reject_bool_student_id(cls, v):
        """bool 은 정수 서브클래스라 `True` 가 학생 "1" 로 조용히 정규화된다.

        정규화 전에 명시적으로 거절해 식별자 손실을 막는다.
        """
        if isinstance(v, bool):
            raise ValueError(
                "student_id 는 bool 을 받을 수 없습니다 (정수/문자열만 허용): "
                f"{v!r}"
            )
        return v


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
    correct: bool | None = Field(
        None,
        description=(
            "ANALYZED 이면 true/false. REVIEW_REQUIRED 이면 null(판정 보류). "
            "correct=true 는 '오류 미검출'이며 완전한 정답 증명이 아니다."
        ),
    )
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
    # --- Backend 전달용 additive 필드 (PHASE 9-7) ---
    # 위 기존 필드(taxonomy ID)는 그대로 두고, Backend 코드만 따로 노출한다.
    # taxonomy ID 를 Backend 코드로 덮어쓰지 않는다.
    misconception_tag: str | None = Field(
        None,
        description=(
            "앱 r48 호환 단수 필드. 대표 오류(가장 낮은 step_no)의 내부 "
            "Misconception ID 이며, 없으면 null. 복수 misconception_tags 와 구분된다."
        ),
    )
    official_skill_codes: list[str] = Field(
        default_factory=list,
        description="Backend 승인 Skill 코드 (SKL-1-004 등). 없으면 빈 배열",
    )
    official_misconception_codes: list[str] = Field(
        default_factory=list,
        description="Backend 승인 Misconception 코드 (MIS-1-004 등). 없으면 빈 배열",
    )
    provisional_skill_codes: list[str] = Field(
        default_factory=list,
        description=(
            "후보(미승인) Skill 코드 (SKL-2-037 등). Backend 승인분과 분리해 "
            "노출한다. 없으면 빈 배열"
        ),
    )
    provisional_misconception_codes: list[str] = Field(
        default_factory=list,
        description=(
            "후보(미승인) Misconception 코드 (MIS-2-001 등). Backend 승인분과 "
            "분리해 노출한다. 없으면 빈 배열"
        ),
    )


class AnalysisSummaryItem(BaseModel):
    """분석 이력 목록 항목 (저장 시점 스냅샷의 요약)."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    attempt_id: str
    problem_id: str
    student_id: str
    analysis_status: AnalysisStatus = Field(..., description="ANALYZED/REVIEW_REQUIRED/UNKNOWN")
    correct: bool | None = Field(
        None, description="ANALYZED 이면 true/false, REVIEW_REQUIRED 이면 null"
    )
    error_type: str | None = None
    error_subtype: str | None = None
    skill: str = Field(
        ...,
        description=(
            "이번 시도에서 관측된 대표 Skill. 정답이면 문제의 대표 추적 Skill, "
            "오답이면 첫 오류가 귀속된 Skill 이다. 요청의 problem_skills(문제 선언 "
            "Skill)와 다를 수 있다 — 선언과 관측을 같은 값으로 가정하지 않는다."
        ),
    )
    state: Literal["mastered", "learning", "needs_practice"]
    mastery: float = Field(
        ...,
        description=(
            "관측 Skill(위 skill 필드) 기준 BKT 값. 요청의 problem_skills 나 "
            "공식 Skill 코드 매핑과 1:1 대응을 보장하지 않는다."
        ),
    )
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

    correct: bool | None = Field(
        ...,
        description=(
            "현재 입력된 풀이에서 오류가 검출되지 않았는지를 뜻한다. "
            "REVIEW_REQUIRED(판정 보류)이면 null 이다. "
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


# ===========================================================================
# OCR 경계 (PHASE 9-8A)
#
# 사진 원본 저장은 Backend 책임이다. AI① 은 Backend 가 준 `image_reference` 를
# 입력으로 받기만 하고, 사진 bytes 를 직접 받지 않는다(업로드 경로 중복 구현 금지).
#
# **OCR confidence 와 AI¹ 수학 분석 confidence 는 서로 다른 값이다.**
# 수학 분석 응답의 `confidence` 는 분류 신뢰도이고, OCR 응답의 `ocr_confidence` /
# `confidence`(problem·region·step) 는 문자 인식 신뢰도다. 섞지 않는다.
# ===========================================================================

#: OCR 처리 상태. COMPLETE=전부 읽음, PARTIAL=일부만 읽음, UNREADABLE=읽을 수 없음.
OcrStatus = Literal["COMPLETE", "PARTIAL", "UNREADABLE"]

#: region 종류 — 인쇄(문제·조건) / 손글씨(풀이).
OcrRegionKind = Literal["printed", "handwritten"]

#: 읽을 수 없는 항목을 표시하는 표식. AI¹ 텍스트 파이프라인에서도 같은 표식을 쓴다.
UNREADABLE_MARKER = "???"


class OcrBBox(BaseModel):
    """영역 좌표. provider 가 정한 좌표계(픽셀 또는 정규화)를 그대로 쓴다."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=0, description="영역 왼쪽 좌표")
    y: float = Field(..., ge=0, description="영역 위쪽 좌표")
    width: float = Field(..., ge=0, description="영역 너비")
    height: float = Field(..., ge=0, description="영역 높이")


class OcrRawTextLine(BaseModel):
    """Raw text OCR의 줄 미리보기 (문제/풀이 의미를 나누기 전)."""

    model_config = ConfigDict(extra="forbid")

    line_index: int = Field(..., ge=0, description="OCR 결과 내 0-based 파생 줄 인덱스")
    page_index: int | None = Field(None, ge=0, description="Backend 요청의 페이지 인덱스")
    text: str = Field(..., description="OCR이 인식한 텍스트; 문제/풀이 의미는 미분류")
    confidence: float | None = Field(None, ge=0.0, le=1.0, description="OCR 신뢰도 (없으면 null)")
    bbox: OcrBBox | None = Field(None, description="원본 이미지 좌표의 축 정렬 bounding box")
    bounding_polygon: list[list[float]] = Field(
        default_factory=list, description="단어 좌표를 합쳐 파생한 줄 bounding polygon [[x, y], ...]"
    )
    derived: bool = Field(True, description="Vision word geometry에서 파생한 줄 여부")


class _OcrReadable(BaseModel):
    """읽을 수 없는 항목의 내용 복원·LaTeX 조작을 막는 공통 규칙.

    계약(문서 §14.17 과 동일)
      - ``unreadable=True`` 면 ``text`` 는 **반드시** ``"???"`` 다.
        ``None`` 이나 빈 문자열도 허용하지 않는다 — 표시 표식이 하나로 고정돼야
        AI¹ 텍스트 파이프라인(``???`` → unrecognized 단계)으로 그대로 이어진다.
      - ``unreadable=True`` 면 ``latex_text`` 는 **반드시** ``None`` 다.
        읽을 수 없는 내용을 임의로 복원하거나 LaTeX 를 만들어내지 않는다.
    """

    text: str | None = Field(None, description="읽힌 원문 (표시용)")
    latex_text: str | None = Field(None, description="검증용 LaTeX (없으면 null)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="OCR 신뢰도 (0~1)")
    unreadable: bool = Field(False, description="읽을 수 없으면 true")

    @model_validator(mode="after")
    def _no_restored_content(self):
        if not self.unreadable:
            return self
        if self.latex_text is not None:
            raise ValueError(
                "읽을 수 없는 항목에 LaTeX 를 만들어낼 수 없습니다 (latex_text 는 null 이어야 한다)"
            )
        if self.text != UNREADABLE_MARKER:
            raise ValueError(
                f"읽을 수 없는 항목의 text 는 '{UNREADABLE_MARKER}' 이어야 합니다 "
                f"(받은 값: {self.text!r})"
            )
        return self


class _OcrContentRequired(_OcrReadable):
    """내용이 반드시 있어야 하는 항목(problem / step).

    읽을 수 있는(unreadable=false) 항목은 ``text`` 또는 ``latex_text`` 중 하나에
    **비어 있지 않은 실제 내용**이 있어야 한다. 둘 다 없거나 공백뿐이면
    provider 계약 위반이다 — 빈 문제를 '읽혔다'로 취급하면 안 된다.

    (``OcrRegion`` 은 여기에 속하지 않는다. 영역은 bbox 만 있어도 유효하다 —
    좌표만 있고 읽힌 텍스트가 없는 region 은 영역 메타데이터일 뿐이다.)
    """

    @model_validator(mode="after")
    def _has_readable_content(self):
        if self.unreadable:
            return self
        has_text = bool(self.text and self.text.strip())
        has_latex = bool(self.latex_text and self.latex_text.strip())
        if not has_text and not has_latex:
            raise ValueError(
                "읽을 수 있는 항목은 text 또는 latex_text 중 하나에 내용이 있어야 합니다 "
                "(둘 다 비어 있으면 provider 계약 위반이다)"
            )
        return self


class OcrRegion(_OcrReadable):
    """인쇄/손글씨 영역 1개.

    bbox 만 있어도 된다 — provider 가 영역 정보만 주는 경우도 허용한다.
    """

    model_config = ConfigDict(extra="forbid")

    kind: OcrRegionKind = Field(..., description="printed | handwritten")
    bbox: OcrBBox = Field(..., description="영역 좌표")


class OcrStep(_OcrContentRequired):
    """풀이 단계 1개. 배열 순서가 곧 위에서 아래 순서다."""

    model_config = ConfigDict(extra="forbid")

    step_no: int = Field(..., ge=1, description="1-based 단계 번호")
    region_id: str | None = Field(None, description="이 단계가 속한 region 식별자")


class OcrProblem(_OcrContentRequired):
    """문제 1개 — 표시 텍스트와 검증용 LaTeX."""

    model_config = ConfigDict(extra="forbid")


class OcrAnalyzeRequest(BaseModel):
    """POST /api/v1/attempts/{attempt_id}/ocr 요청.

    사진 bytes 를 받지 않는다. Backend 가 보관한 이미지의 참조만 받는다.
    """

    model_config = ConfigDict(extra="forbid")

    image_reference: str = Field(..., min_length=1, description="Backend 가 보관한 이미지 참조")
    page_index: int | None = Field(None, ge=0, description="다중 페이지 중 몇 번째 페이지인지")

    @field_validator("image_reference")
    @classmethod
    def _not_blank(cls, v):
        if not v.strip():
            raise ValueError("image_reference 는 비어 있을 수 없습니다")
        return v.strip()


class OcrResponse(BaseModel):
    """POST /api/v1/attempts/{attempt_id}/ocr 응답.

    OCR 결과만 담는다. Knowledge State 를 갱신하지 않고, AI② 를 호출하지 않으며,
    Attempt/분석 이력도 남기지 않는다(분석은 `/analyze` 경로의 몫).
    """

    model_config = ConfigDict(extra="forbid")

    ocr_result_id: str = Field(..., description="OCR 결과 식별자")
    attempt_id: str
    status: OcrStatus = Field(..., description="COMPLETE | PARTIAL | UNREADABLE")
    model_version: str = Field(..., description="OCR 모델 버전")
    representation: Literal["structured", "raw_text"] = Field(
        "structured", description="structured provider 결과 또는 의미 분리 전 raw OCR 미리보기"
    )
    ocr_confidence: float | None = Field(
        None, ge=0.0, le=1.0, description="전체 OCR 신뢰도. provider가 제공하지 않으면 null"
    )
    problem: OcrProblem | None = Field(
        None, description="구조화 OCR 결과. raw_text 모드에서는 null이며 추정하지 않는다"
    )
    regions: list[OcrRegion] = Field(default_factory=list)
    solution_steps: list[OcrStep] = Field(default_factory=list)
    raw_text: str | None = Field(None, description="raw_text 모드에서만 제공하는 전체 OCR 텍스트")
    raw_lines: list[OcrRawTextLine] = Field(
        default_factory=list, description="raw_text 모드의 순서·좌표·confidence 미리보기"
    )
    page_index: int | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _steps_are_top_to_bottom(self):
        if self.representation == "structured" and self.problem is None:
            raise ValueError("structured OCR 응답에는 problem 이 있어야 합니다")
        if self.representation == "raw_text":
            if self.problem is not None or self.regions or self.solution_steps:
                raise ValueError(
                    "raw_text OCR은 problem/regions/solution_steps 를 추정해 넣지 않습니다"
                )
            if self.raw_text is None:
                raise ValueError("raw_text 표현에는 raw_text 필드가 필요합니다")
        elif self.raw_text is not None or self.raw_lines:
            raise ValueError("structured OCR 응답에는 raw_text 필드를 넣지 않습니다")
        nos = [step.step_no for step in self.solution_steps]
        if any(b <= a for a, b in zip(nos, nos[1:])):
            raise ValueError("풀이 단계는 위에서 아래 순서(번호 증가)여야 합니다")
        return self
