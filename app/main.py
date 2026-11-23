"""AI① Learning Intelligence — FastAPI 엔트리포인트."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query

from app.integration.backend_adapter import (
    BackendIdError,
    NoTrackingSkillError,
    UnknownSkillCodeError,
    UnknownSkillIdError,
    normalize_backend_id,
    require_known_skills,
    resolve_problem_skills,
    text_analysis_view,
)
from app.integration.internal_auth import require_internal_auth
from app.integration.ocr import (
    OcrNotConfiguredError,
    OcrProviderError,
    OcrProviderRequest,
    OcrRawTextResult,
    OcrResult,
    derive_ocr_confidence,
    derive_ocr_result_id,
    derive_ocr_status,
    require_ocr_provider,
)
from app.pipeline.analyzer import (
    _ANALYSIS_STATUS_BY_ERROR,
    AnalysisError,
    analyze,
)
from app.pipeline.knowledge import InvalidStudentIdError, validate_student_id
from app.pipeline.repository import AnalysisRecord, RepositoryError, get_repository
from app.pipeline.session import (
    CorrectSolution,
    Problem,
    SolutionStep,
    StudentAttempt,
    analyze_attempt,
    utc_now,
)
from app.schemas import (
    AnalysisDetailResponse,
    AnalysisListResponse,
    AnalysisSummaryItem,
    AnalyzeAttemptRequest,
    AnalyzeAttemptResponse,
    AnalyzeSolutionRequest,
    AnalyzeSolutionResponse,
    MisconceptionTopItem,
    MisconceptionTopResponse,
    OcrAnalyzeRequest,
    OcrResponse,
    StepView,
    StudentHistoryItem,
    StudentHistoryResponse,
)
from app.taxonomy import misconception_by_id

# ---------------------------------------------------------------------------
# 문서 노출 (PHASE 9-9C.8 후속)
#
# `/docs`·`/redoc`·`/openapi.json` 은 인증 guard 밖의 FastAPI 기본 라우트라
# 키 없이 전체 스키마가 노출됐다. 내부 서비스 계약상 데이터 라우트 7개만
# 보호하면 충분하지만, 스키마 자체도 불필요한 정보다. 기본은 비노출로 두고
# 로컬 개발에서만 ``AI1_ENABLE_DOCS=1`` 로 켠다. 값은 import 시 1회 읽는다.
# ---------------------------------------------------------------------------
_DOCS_ENV = "AI1_ENABLE_DOCS"
_TRUTHY = {"1", "true", "yes", "on"}


def _docs_routes_enabled() -> bool:
    return os.environ.get(_DOCS_ENV, "").strip().lower() in _TRUTHY


def _docs_route_kwargs() -> dict:
    if _docs_routes_enabled():
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(
    title="AI1 Learning Intelligence",
    version="0.1.0",
    description="풀이 분석 파이프라인: 오류 단계 특정 → 오개념 분류 → Knowledge State",
    **_docs_route_kwargs(),
)

# ---------------------------------------------------------------------------
# 내부 서비스 간 인증 (PHASE 9-9C.8 — 협의서 §3.1)
#
# `/health` 를 제외한 전부(``/analyze-solution`` legacy 포함)가 아래 dependency
# 를 거친다. dependency 는 endpoint 본문보다 먼저 실행되므로 provider 초기화·
# 호출·budget 예약보다 인증이 선행한다. 누락/불일치 401 E_AUTH_INVALID_KEY,
# 키 미설정/빈 설정 503 E_AUTH_NOT_CONFIGURED (fail-closed).
# ---------------------------------------------------------------------------
_INTERNAL_AUTH_DEPS = [Depends(require_internal_auth)]

# AnalysisError 코드 → HTTP status 매핑 (S11)
# 매핑에 없는 코드는 422를 사용한다(기존 동작 유지).
#   - E_INPUT_TOO_LARGE: 본문 크기 제한 위반이므로 413 Payload Too Large가 적절
#   - 나머지: 요청 내용 자체가 계약에 맞지 않으므로 422 Unprocessable Entity
_ERROR_STATUS: dict[str, int] = {
    "E_INPUT_TOO_LARGE": 413,
}
_DEFAULT_ERROR_STATUS = 422

#: APP 재시도를 유도하면 안 되는 계약/범위 오류 (A6 P1).
#: Backend 는 code 기준으로 판단하지만, 참고 필드로 retryable=false 를 함께 보낸다.
_NON_RETRYABLE_CODES = frozenset(
    {
        "E_INVALID_TARGET_FORM",
        "E_UNSUPPORTED_INEQUALITY",
        "E_UNSUPPORTED_FUNCTION",
        "E_INVALID_PROBLEM_TYPE",
    }
)


def _error_detail(exc: AnalysisError) -> dict:
    detail = {"code": exc.code, "message": str(exc)}
    if exc.code in _ANALYSIS_STATUS_BY_ERROR:
        detail["analysis_status"] = _ANALYSIS_STATUS_BY_ERROR[exc.code]
    if exc.code in _NON_RETRYABLE_CODES:
        detail["retryable"] = False
    return detail


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze-solution", response_model=AnalyzeSolutionResponse, dependencies=_INTERNAL_AUTH_DEPS)
def analyze_solution(req: AnalyzeSolutionRequest) -> AnalyzeSolutionResponse:
    try:
        result = analyze(
            problem_latex=req.problem_latex,
            solution_text=req.solution_text,
            student_id=req.student_id,
            engine=req.engine,
            problem_skill=req.problem_skill,
            problem_type=req.problem_type,
            target_form=req.target_form,
        )
    except AnalysisError as exc:
        status = _ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS)
        # 기존 detail.code / detail.message 계약은 그대로 두고, 분석을 수행했는데
        # 신뢰 가능한 단계 판정이 없었던 경우에만 analysis_status 를 덧붙인다.
        # 요청/입력 오류에는 붙지 않는다 (UNKNOWN 으로 섞지 않는다).
        raise HTTPException(status_code=status, detail=_error_detail(exc))
    return AnalyzeSolutionResponse(**result)


# ---------------------------------------------------------------------------
# attempt 단위 분석 (Phase 8-3)
# ---------------------------------------------------------------------------

_REPOSITORY_ERROR_STATUS: dict[str, int] = {
    "E_REFERENCE_INTEGRITY": 422,
    "E_DUPLICATE_ID": 409,
    "E_ATTEMPT_CONFLICT": 409,
    # PHASE 9-7.3: 이미 등록된 리소스가 분석에 쓴 후보와 다르면 충돌이다.
    "E_RESOURCE_CONFLICT": 409,
}


@contextmanager
def _resource_locks(repo, *resources):
    """리소스 ID 단위 락을 정해진 순서로 획득하고 역순으로 해제한다.

    같은 ``problem_id`` / ``solution_id`` 로 동시에 들어온 요청이 서로 다른 후보
    Problem/CorrectSolution 을 분석·저장하지 않게 한다 (PHASE 9-7.3).

    **항상 같은 순서로 획득해야 한다** — 그래야 교착이 생기지 않는다.
    호출부는 항상 ``problem`` → ``solution`` 순서로 넘긴다.
    """
    acquired: list[tuple[str, str]] = []
    try:
        for kind, resource_id in resources:
            if resource_id is None:
                continue
            repo.claim_resource(kind, resource_id)
            acquired.append((kind, resource_id))
        yield
    finally:
        for kind, resource_id in reversed(acquired):
            repo.release_resource(kind, resource_id)


def _peek_problem(problem_id: str) -> Problem | None:
    """저장된 problem 을 조회한다. 없으면 None."""
    try:
        return get_repository().get_problem(problem_id)
    except RepositoryError:
        return None


def _resolve_problem(req: AnalyzeAttemptRequest) -> tuple[Problem, bool]:
    """문제를 조회하고, 없으면 요청 정보로 **메모리에만** 만든다.

    반환값은 ``(problem, is_new)`` 이다.

    등록되어 있으면 저장본을 반환한다(요청 본문으로 덮어쓰지 않는다).
    표시 메타데이터(question_text/concept_name/unit_name)도 최초 등록 때만 저장된다.

    **저장을 하지 않는 이유 (PHASE 9-7.2)**: 예전에는 여기서 곧바로 저장했는데,
    그러면 tracking Skill 이 없어 거절된 요청이 불완전한 Problem(``skills=[]``)을
    저장소에 남겼다. 그 뒤 같은 ``problem_id`` 로 올바른 tracking Skill 을 넣어
    다시 요청해도 저장본이 source of truth 라 계속 거절되어 **복구할 수 없다**.
    저장은 분석 결과가 나온 뒤로 미룬다 — 거절된 요청은 아무것도 남기지 않는다.
    """
    repo = get_repository()
    try:
        return repo.get_problem(req.problem_id), False
    except RepositoryError:
        if not req.problem_latex:
            raise RepositoryError(
                "E_REFERENCE_INTEGRITY",
                f"등록되지 않은 problem_id: '{req.problem_id}' (problem_latex 필요)",
            ) from None
        problem = Problem(
            problem_id=req.problem_id,
            problem_latex=req.problem_latex,
            skills=list(req.problem_skills),
            question_text=req.problem_question_text,
            concept_name=req.problem_concept_name,
            unit_name=req.problem_unit_name,
        )
        return problem, True


def _resolve_correct_solution(
    req: AnalyzeAttemptRequest,
) -> tuple[CorrectSolution | None, bool]:
    """정답 풀이를 조회하고, 없으면 요청 정보로 **메모리에만** 만든다.

    반환값은 ``(solution, is_new)`` 이다. 미제공 시 ``(None, False)``.

    저장은 문제와 같이 분석 뒤로 미룬다 — 정답 풀이는 문제 FK 를 참조하므로
    문제 저장 뒤에 저장해야 한다.
    """
    if req.correct_solution is None:
        return None, False
    repo = get_repository()
    try:
        return repo.get_correct_solution(req.correct_solution.solution_id), False
    except RepositoryError:
        payload = req.correct_solution
        solution = CorrectSolution(
            solution_id=payload.solution_id,
            problem_id=req.problem_id,
            steps=[
                SolutionStep(step_no=i, latex_text=s.latex_text, skill_ids=list(s.skill_ids))
                for i, s in enumerate(payload.steps, start=1)
            ],
            answer_latex=payload.answer_latex,
            is_primary=payload.is_primary,
        )
        return solution, True


def _persist_new_records(
    repo,
    problem: Problem,
    problem_is_new: bool,
    solution,
    solution_is_new: bool,
) -> None:
    """문제 → 정답 풀이 순서로 저장한다 (기존 저장 순서 유지).

    이미 등록된 항목은 건드리지 않는다(저장본이 source of truth).
    ``E_DUPLICATE_ID`` 는 **단순히 무시하지 않는다** (PHASE 9-7.3) — 저장된
    레코드와 분석에 사용한 레코드가 같은지 확인한다.

      - 같으면 분석 근거와 저장본이 일치하므로 그대로 진행한다.
      - 다르면 **명확한 충돌(409)** 로 거절한다. Knowledge State 를 이미 반영했을
        수 있으므로 Attempt/분석은 저장하지 않는다.

    정상 경로에서는 리소스 락이 같은 ID 의 생성을 직렬화하므로 ``E_DUPLICATE_ID`` 는
    저장소 밖에서 직접 등록한 경우에만 발생한다.
    """
    if problem_is_new:
        try:
            repo.save_problem(problem)
        except RepositoryError as exc:
            if exc.code != "E_DUPLICATE_ID":
                raise
            stored = repo.get_problem(problem.problem_id)
            if stored != problem:
                raise RepositoryError(
                    "E_RESOURCE_CONFLICT",
                    f"문제 '{problem.problem_id}' 가 다른 내용으로 이미 등록되어 있습니다. "
                    "분석에 사용한 문제와 저장본이 달라 Knowledge State 를 반영하지 않습니다.",
                ) from None
    if solution_is_new:
        try:
            repo.save_correct_solution(solution)
        except RepositoryError as exc:
            if exc.code != "E_DUPLICATE_ID":
                raise
            stored = repo.get_correct_solution(solution.solution_id)
            if stored != solution:
                raise RepositoryError(
                    "E_RESOURCE_CONFLICT",
                    f"정답 풀이 '{solution.solution_id}' 가 다른 내용으로 이미 등록되어 있습니다. "
                    "분석에 사용한 정답 풀이와 저장본이 달라 Knowledge State 를 반영하지 않습니다.",
                ) from None


def _build_attempt(
    attempt_id: str, req: AnalyzeAttemptRequest, attempted_at: datetime
) -> StudentAttempt:
    """요청 본문으로 attempt 객체를 만든다 (저장은 분석 성공 후).

    attempted_at 은 엔드포인트가 요청 접수 시점에 넘긴다. 여기서 새로 만들면
    게이트 대기 시간만큼 시각이 밀린다.
    """
    return StudentAttempt(
        attempt_id=attempt_id,
        student_id=req.student_id,
        problem_id=req.problem_id,
        solution_text=req.solution_text,
        attempted_at=attempted_at,
        problem_type=req.problem_type,
        target_form=req.target_form,
    )


def _peek_attempt(attempt_id: str) -> StudentAttempt | None:
    """저장된 attempt 를 조회한다. 없으면 None."""
    try:
        return get_repository().get_attempt(attempt_id)
    except RepositoryError:
        return None


def _assert_same_attempt(stored: StudentAttempt, req: AnalyzeAttemptRequest) -> None:
    """저장본 attempt 의 본문이 요청과 다르면 덮어쓰지 않고 충돌로 거절한다.

    A6 P1: problem_type/target_form 도 분석 결과를 바꾸므로 비교 대상에 넣는다.
    """
    if (
        stored.problem_id,
        stored.student_id,
        stored.solution_text,
        stored.problem_type,
        stored.target_form,
    ) != (
        req.problem_id,
        req.student_id,
        req.solution_text,
        req.problem_type,
        req.target_form,
    ):
        raise RepositoryError(
            "E_ATTEMPT_CONFLICT",
            f"attempt '{stored.attempt_id}' 가 이미 다른 내용으로 등록되어 있습니다.",
        )


def _build_step_list(analysis) -> list[StepView]:
    valid = analysis.api_response["valid"]
    views: list[StepView] = []
    for step in analysis.attempt.steps:
        judgment = valid[step.step_no - 1] if step.step_no - 1 < len(valid) else None
        views.append(
            StepView(
                step_no=step.step_no,
                latex_text=step.latex_text,
                is_correct=judgment,
                status=(
                    "correct" if judgment is True
                    else "error" if judgment is False
                    else "unreviewable"
                ),
                skill_ids=list(step.skill_ids),
                aligned_solution_step_no=step.aligned_step_no,
                unrecognized=step.unrecognized,
            )
        )
    return views


def _to_attempt_response(analysis, analysis_id: str, analysis_count: int) -> AnalyzeAttemptResponse:
    api = analysis.api_response
    # Backend 전달용 코드 배열은 저장된 AttemptAnalysis 에서 매번 재생성한다(저장하지
    # 않는다). POST / 멱등 재응답 / 상세 GET 이 같은 분석을 받으므로 세 경로가 같다.
    backend_view = text_analysis_view(analysis)
    return AnalyzeAttemptResponse(
        analysis_id=analysis_id,
        attempt_id=analysis.attempt_id,
        problem_id=analysis.problem_id,
        student_id=analysis.student_id,
        attempted_at=analysis.attempt.attempted_at,
        analysis_status=api["analysis_status"],
        analysis_count=analysis_count,
        correct=api["correct"],
        error_step=api["error_step"],
        first_error_step_index=api["error_step"],
        error_type=api["error_type"],
        error_subtype=api["error_subtype"],
        skill=api["skill"],
        state=api["state"],
        mastery=api["mastery"],
        misconception_id=api["misconception_id"],
        confidence=api["confidence"],
        steps_latex=api["steps_latex"],
        valid=api["valid"],
        unrecognized=api["unrecognized"],
        final_answer_present=api.get("final_answer_present"),
        step_list=_build_step_list(analysis),
        errors=[e.model_dump() for e in analysis.errors],
        observations=[o.model_dump() for o in analysis.observations],
        misconception_tags=sorted({m for e in analysis.errors for m in e.misconception_ids}),
        states=dict(analysis.states),
        mastery_by_skill=dict(analysis.mastery),
        **backend_view,
    )


def _raise_http(exc: AnalysisError) -> None:
    status = _ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS)
    raise HTTPException(status_code=status, detail=_error_detail(exc))


def _http_from_repository_error(exc: RepositoryError) -> HTTPException:
    return HTTPException(
        status_code=_REPOSITORY_ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS),
        detail={"code": exc.code, "message": str(exc)},
    )


def _normalize_backend_request(
    attempt_id: str, req: AnalyzeAttemptRequest
) -> tuple[str, AnalyzeAttemptRequest]:
    """Backend 전달 경계에서 식별자와 Skill 코드를 AI¹ 내부 값으로 정규화한다.

    잠정 정책 (PHASE 9-7)
      - ``attempt_id`` / ``student_id``: 정수 입력은 십진 문자열로 정규화하고
        접두사를 붙이지 않는다. 문자열은 앞뒤 공백만 제거하고 그대로 쓴다
        (선행 0 제거는 서로 다른 ID 를 합치므로 하지 않는다).
      - ``problem_skills``: Backend Skill 코드(``SKL-*``)를 crosswalk 에 있는
        코드만 역변환한다. 모르는 코드는 **기본 Skill 으로 치환하지 않고** 422.
      - ``problem_id``: 불투명 문자열을 그대로 보존한다
        (``captured:<id>`` / ``PRB-*`` 를 추측·병합하지 않는다).

    정규화 후에는 ``model_validate`` 로 다시 검증해 조용히 무시되는 값을 막는다.
    """
    try:
        normalized_attempt_id = normalize_backend_id(attempt_id, "attempt_id")
    except BackendIdError as exc:
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    try:
        normalized_student_id = normalize_backend_id(req.student_id, "student_id")
        normalized_skills = resolve_problem_skills(req.problem_skills)
    except BackendIdError as exc:
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except UnknownSkillCodeError as exc:
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except UnknownSkillIdError as exc:
        # taxonomy 에 없는 내부 Skill ID 는 Problem 생성 전에 거절한다
        # (그대로 두면 pydantic ValidationError 가 500 으로 새어 나간다)
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    payload = req.model_dump()
    payload["student_id"] = normalized_student_id
    payload["problem_skills"] = normalized_skills
    return normalized_attempt_id, AnalyzeAttemptRequest.model_validate(payload)


def _validate_new_problem_skills(req: AnalyzeAttemptRequest) -> None:
    """미등록 문제의 요청 선언분을 Problem 생성 전에 검증한다.

    여기서는 **taxonomy 등록 여부만** 확인한다. tracking Skill 부재는 그대로 두는데,
    파싱·검증 오류(``E_UNRECOGNIZED`` / ``E_UNVERIFIABLE`` / ``E_EMPTY_SOLUTION`` /
    ``E_INPUT_TOO_LARGE``) 가 먼저 와야 하는 기존 오류 우선순위를 바꾸지 않기 위해서다.
    같은 사실에 대해 같은 코드(``E_SKILL_NOT_TRACKABLE``) 로 거절되는데 그 시점은
    ``primary_tracking_skill()`` 이다.

    미등록 문제의 ``problem_skills`` 는 최초 등록에 그대로 쓰이므로 여기서 막는다.
    이미 등록된 문제는 저장본이 source of truth 라 검증하지 않는다(멱등 재요청이
    ``problem_skills`` 를 생략해도 정상 동작해야 한다).
    """
    if _peek_problem(req.problem_id) is not None:
        return
    try:
        require_known_skills(req.problem_skills)
    except UnknownSkillIdError as exc:
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None


def _assert_tracking_skill(problem: Problem) -> None:
    """문제에 Knowledge State 관측용 tracking Skill 이 최소 1개 있는지 확인한다.

    Backend 요청 계약 (PHASE 9-7.1)
      - ``problem_skills`` 누락·빈 목록, 또는 group node 만 있으면 422.
      - group node 는 함께 선언해도 되지만 관측 대상이 아니므로 tracking Skill 이
        최소 1개는 있어야 한다.
      - 기본 Skill 을 추측해서 채우지 않는다.

    **검증 시점** — 입력 오류보다 뒤에 와야 한다. 이 헬퍼는 분석 **이후**(관측 결과가
    만들어진 뒤) 호출해 계약 위반을 방어한다. 최초 분석에서 추적 Skill 이 없으면
    ``primary_tracking_skill()`` 이 같은 코드 ``E_SKILL_NOT_TRACKABLE`` 로 먼저
    거절하므로, ``E_UNRECOGNIZED`` / ``E_UNVERIFIABLE`` / ``E_EMPTY_SOLUTION`` /
    ``E_INPUT_TOO_LARGE`` 보다 앞서는 일이 없다(기존 오류 우선순위 유지).

    **저장된 문제 기준**으로 검사한다. 이미 등록된 문제는 요청의 ``problem_skills`` 로
    덮어쓰지 않으므로(저장본이 source of truth), 재요청에서 ``problem_skills`` 를
    생략해도 정상 동작해야 한다. 그래서 검증은 저장본 기준이어야 한다.
    """
    if problem.tracking_skill_ids():
        return
    raise HTTPException(
        status_code=_DEFAULT_ERROR_STATUS,
        detail={
            "code": NoTrackingSkillError.code,
            "message": (
                f"문제 '{problem.problem_id}' 에 Knowledge State 관측용 tracking Skill 이 없습니다. "
                f"problem_skills={problem.skills or '(빈 목록)'} — 기본 Skill 은 추측하지 않습니다"
            ),
        },
    )


@app.post("/api/v1/attempts/{attempt_id}/analyze", response_model=AnalyzeAttemptResponse, dependencies=_INTERNAL_AUTH_DEPS)
def analyze_attempt_endpoint(
    attempt_id: str, req: AnalyzeAttemptRequest
) -> AnalyzeAttemptResponse:
    """attempt 1건을 분석해 저장하고, 분석 이력을 남긴다.

    멱등성: 같은 attempt_id + 같은 제출 내용이 이미 분석되었으면 그 최신
    AnalysisRecord 를 그대로 반환한다. 새 analysis_id 를 만들지 않고
    analyze_attempt() 도 다시 호출하지 않으므로 Knowledge Observation 도
    중복 반영되지 않는다 (네트워크 재전송 안전). 내용이 다른 동일 attempt_id
    는 409 로 거절한다.

    원자성: `이력 확인 → 분석 → 저장` 구간을 저장소의 attempt 단위 락으로
    직렬화해 동시 중복 요청도 분석과 상태 갱신이 한 번만 일어난다.

    등록 시점: attempt 는 분석에 성공한 뒤에 등록한다. 실패한 분석이
    attempt_id 를 다른 내용으로 잠가 버려 재시도가 409 되는 것을 막는다.

    attempted_at: 요청 접수 시점에 한 번 기록해 attempt 에 실어 보낸다(게이트 대기
    시간은 제외). 멱등 재요청·충돌 거절은 저장된 값을 그대로 쓰므로 시각이 변하지
    않는다. 클라이언트가 시각을 보내게 하지는 않는다.

    UNKNOWN(E_UNRECOGNIZED/E_UNVERIFIABLE)은 기존과 동일하게 422 + detail.status 로
    표현하고, 요청/입력 오류에는 status 를 붙이지 않는다.

    Backend 전달 경계(PHASE 9-7): attempt_id/student_id 정규화와 Backend Skill
    코드 역변환을 먼저 수행하고, `state`/`mastery`/`observations` 는 AI¹ 로컬 엔진
    결과로만 보낸다(Backend 가 최종 Student State 를 소유).
    """
    attempt_id, req = _normalize_backend_request(attempt_id, req)
    _validate_new_problem_skills(req)
    received_at = utc_now()  # 정책 1: 요청 접수 시각
    repo = get_repository()
    solution_id = req.correct_solution.solution_id if req.correct_solution else None

    # 리소스 ID 단위 직렬화 (PHASE 9-7.3).
    # 같은 problem_id / solution_id 로 들어온 동시 요청이 서로 다른 후보 레코드를
    # 분석·저장하지 않게 한다. 항상 problem → solution 순서로 획득하므로 교착이 없다.
    with _resource_locks(repo, ("problem", req.problem_id), ("solution", solution_id)):
        try:
            # 문제/정답 풀이는 **아직 저장하지 않는다** (PHASE 9-7.2).
            # 거절된 요청이 불완전한 Problem 을 저장소에 남기지 않도록 저장 시점을
            # 분석 뒤로 미룬다. 저장 순서(문제 → 정답 풀이 → Attempt → 분석)는 유지한다.
            problem, problem_is_new = _resolve_problem(req)
            solution, solution_is_new = _resolve_correct_solution(req)
            # 정답 풀이가 다른 Problem 소속이면 충돌 — 성공 응답으로 나가지 않는다.
            if solution is not None and not solution.belongs_to(problem):
                raise HTTPException(
                    status_code=_REPOSITORY_ERROR_STATUS["E_RESOURCE_CONFLICT"],
                    detail={
                        "code": "E_SOLUTION_PROBLEM_MISMATCH",
                        "message": (
                            f"정답 풀이 '{solution.solution_id}' 는 문제 "
                            f"'{solution.problem_id}' 소속입니다 "
                            f"(요청 문제: '{problem.problem_id}')"
                        ),
                    },
                )
            stored = _peek_attempt(attempt_id)
            if stored is not None:
                _assert_same_attempt(stored, req)
        except RepositoryError as exc:
            raise _http_from_repository_error(exc) from None

        # 여기부터는 attempt 단위로 직렬화한다 (같은 attempt 의 동시 요청만 대기)
        repo.claim_analysis(attempt_id)
        try:
            if repo.has_analysis(attempt_id):
                # 이미 성공 분석된 제출 — 재분석하지 않고 기존 이력을 반환한다.
                #
                # 반환 **직전에** 저장본과 요청을 다시 비교한다 (PHASE 9-7.4).
                # 분석 전 비교는 락 밖이라, 같은 attempt_id 로 본문이 다른 요청이
                # 동시에 들어오면 둘 다 사전 검사를 통과할 수 있다. 그 경우 나중
                # 요청이 여기서 409 로 막히지 않으면 **다른 제출의 분석을 그대로
                # 돌려주게 된다** (잘못된 200). 저장된 `problem_id`/`student_id`/
                # `solution_text` 중 하나라도 다르면 409 E_ATTEMPT_CONFLICT 다.
                stored_attempt = _peek_attempt(attempt_id)
                if stored_attempt is not None:
                    _assert_same_attempt(stored_attempt, req)
                existing = repo.get_latest_analysis(attempt_id)
                return _to_attempt_response(
                    existing.analysis,
                    existing.analysis_id,
                    len(repo.list_analyses(attempt_id)),
                )

            # 분석 직전에 다시 확인한다 — 같은 problem_id 로 먼저 등록된 요청이
            # 있으면 그 저장본이 source of truth 다 (리소스 락 안이라 확정적이다).
            latest_problem = _peek_problem(req.problem_id)
            if latest_problem is not None:
                problem = latest_problem
                problem_is_new = False

            attempt = _build_attempt(attempt_id, req, received_at)
            try:
                analysis = analyze_attempt(
                    problem,
                    attempt,
                    solution=solution,
                    engine=req.engine,
                    problem_type=req.problem_type,
                    target_form=req.target_form,
                )
            except AnalysisError as exc:
                # 파싱·검증 오류(E_UNRECOGNIZED/E_UNVERIFIABLE/E_EMPTY_SOLUTION/
                # E_INPUT_TOO_LARGE)는 기존처럼 문제를 등록한 상태로 남긴다(재사용 가능).
                # Skill 계약 오류(E_SKILL_NOT_TRACKABLE)는 **아무것도 저장하지 않는다** —
                # 불완전한 Problem 이 남으면 같은 problem_id 로 복구 요청을 보낼 수 없다.
                if exc.code != NoTrackingSkillError.code:
                    _persist_new_records(
                        repo, problem, problem_is_new, solution, solution_is_new
                    )
                _raise_http(exc)

            # 분석이 끝난 뒤 계약 재확인 (관측 결과가 만들어졌으므로 안전한 지점이다).
            # 최초 분석에서 추적 Skill 이 없으면 analyze_attempt 가 같은 코드로 먼저 거절한다.
            _assert_tracking_skill(problem)

            # 분석에 성공한 뒤 문제/정답 풀이를 저장한다 (기존 저장 순서 유지)
            _persist_new_records(repo, problem, problem_is_new, solution, solution_is_new)

            # 분석에 성공한 뒤 attempt 를 등록한다 (참조 무결성 선행 조건)
            if _peek_attempt(attempt_id) is None:
                try:
                    repo.save_attempt(attempt)
                except RepositoryError as exc:
                    if exc.code != "E_DUPLICATE_ID":
                        raise
                    _assert_same_attempt(repo.get_attempt(attempt_id), req)
            record = repo.save_analysis(analysis)
            return _to_attempt_response(
                analysis, record.analysis_id, len(repo.list_analyses(attempt_id))
            )
        except RepositoryError as exc:
            raise _http_from_repository_error(exc) from None
        finally:
            repo.release_analysis(attempt_id)


# ===========================================================================
# OCR 경계 (PHASE 9-8A / 앱 r36~r43)
#
# 사진 원본 저장은 Backend 책임. AI¹ 은 `image_reference` 만 받고 사진 bytes 는
# 받지 않는다(업로드 경로 중복 구현 금지). 실제 OCR 업체는 미확정이므로 provider
# 인터페이스 뒤에 두고, provider 가 없으면 결과를 꾸며내지 않고 503 을 돌려준다.
# ===========================================================================


def _to_ocr_response(
    attempt_id: str, req: OcrAnalyzeRequest, result: OcrResult | OcrRawTextResult
) -> OcrResponse:
    """provider 결과를 API 응답으로 바꾼다.

    ``status`` 와 ``ocr_confidence`` 는 경계가 파생한다 — provider 가 말한 값에
    의존하지 않아 응답 상태가 내용과 어긋나지 않는다.

    provider 의 값을 **dict 로 넘겨 재검증**한다 — provider 가 검증을 우회한 값을
    그대로 응답에 흘려보내지 않게 하기 위해서다(범위·미인식 규칙이 응답 단계에서
    다시 적용된다).

    Backend 가 준 image_reference 는 서명 URL일 수 있으므로 **응답에 어떤 형태로도
    되돌려 보내지 않는다** — 원본 URL/서명뿐 아니라 hash fingerprint 필드도 두지
    않는다(PHASE 9-9C.7). 참조 자체는 provider 호출 입력으로만 쓴다.
    """
    page_index = result.page_index if result.page_index is not None else req.page_index
    if isinstance(result, OcrRawTextResult):
        return OcrResponse(
            ocr_result_id=derive_ocr_result_id(attempt_id, result, page_index),
            attempt_id=attempt_id,
            status=derive_ocr_status(result),
            model_version=result.model_version,
            representation="raw_text",
            ocr_confidence=derive_ocr_confidence(result),
            problem=None,
            regions=[],
            solution_steps=[],
            raw_text=result.full_text,
            raw_lines=[line.model_dump() for line in result.lines],
            page_index=page_index,
            warnings=list(result.warnings),
        )

    return OcrResponse(
        ocr_result_id=derive_ocr_result_id(attempt_id, result, page_index),
        attempt_id=attempt_id,
        status=derive_ocr_status(result),
        model_version=result.model_version,
        representation="structured",
        ocr_confidence=derive_ocr_confidence(result),
        problem=result.problem.model_dump(),
        regions=[region.model_dump() for region in result.regions],
        solution_steps=[step.model_dump() for step in result.steps],
        raw_text=None,
        raw_lines=[],
        page_index=page_index,
        warnings=list(result.warnings),
    )


@app.post("/api/v1/attempts/{attempt_id}/ocr", response_model=OcrResponse, dependencies=_INTERNAL_AUTH_DEPS)
def ocr_attempt_endpoint(attempt_id: str, req: OcrAnalyzeRequest) -> OcrResponse:
    """문제집 사진에서 문제/풀이를 인식한다 (잠정 경로).

    - **provider 미설정 → 503** ``E_OCR_NOT_CONFIGURED``. 결과를 지어내지 않는다.
    - API 비활성화·인증·billing 오류는 503의 구체적인 OCR 오류 코드로 반환한다.
      quota/사용량 상한(``E_OCR_USAGE_LIMIT_REACHED``)은 429, 이미지 다운로드 실패
      (``E_OCR_IMAGE_FETCH_FAILED``)·지원하지 않는 이미지(``E_OCR_UNSUPPORTED_IMAGE``)
      는 422, 그 외 provider 호출 실패는 502 ``E_OCR_PROVIDER_ERROR`` (협의서 §4.6.3).
    - **부작용 없음**: Knowledge State 를 갱신하지 않고, AI② 를 호출하지 않고,
      Attempt/분석 이력도 남기지 않는다(분석은 ``/analyze`` 경로의 몫).
    - **OCR confidence 와 수학 분석 confidence 는 별개 값**이다. 이 응답에는
      ``ocr_confidence`` 가 있고, 분류 신뢰도인 ``confidence`` 필드는 없다.
    """
    try:
        normalized_attempt_id = normalize_backend_id(attempt_id, "attempt_id")
    except BackendIdError as exc:
        raise HTTPException(
            status_code=_DEFAULT_ERROR_STATUS,
            detail={"code": exc.code, "message": str(exc)},
        ) from None

    try:
        provider = require_ocr_provider()
    except OcrNotConfiguredError as exc:
        # http_status 가 정의되어 있으면 그 값을 쓴다 (예: OcrUsageLimitError → 429).
        raise HTTPException(
            status_code=getattr(exc, "http_status", 503),
            detail={"code": exc.code, "message": str(exc)},
        ) from None

    provider_request = OcrProviderRequest(
        attempt_id=normalized_attempt_id,
        image_reference=req.image_reference,
        page_index=req.page_index,
    )
    try:
        result = provider.recognize(provider_request)
        if not isinstance(result, (OcrResult, OcrRawTextResult)):
            raise OcrProviderError(
                "provider 가 OcrResult/OcrRawTextResult 가 아닌 값을 돌려주었습니다: "
                f"{type(result).__name__}"
            )
        return _to_ocr_response(normalized_attempt_id, req, result)
    except OcrNotConfiguredError as exc:
        # OcrUsageLimitError 도 이 계열이다 — 429 로 등장한다 (협의서 §4.6.3).
        raise HTTPException(
            status_code=getattr(exc, "http_status", 503),
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except OcrProviderError as exc:
        raise HTTPException(
            status_code=getattr(exc, "http_status", 502),
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except Exception as exc:  # 외부 서비스 경계 — 내부 오류가 밖으로 새지 않게 한다
        raise HTTPException(
            status_code=502,
            detail={
                "code": "E_OCR_PROVIDER_ERROR",
                "message": f"OCR provider 호출에 실패했습니다: {type(exc).__name__}",
            },
        ) from None


# ===========================================================================
# 분석 이력 조회 (PHASE 8-5 / 앱 r61·r62)
# 읽기 전용: analyze_attempt() 를 호출하지 않고 Knowledge State 를 건드리지
# 않는다. 응답은 저장 시점의 AnalysisRecord 스냅샷이며 그때의 상태를 담는다.
# ===========================================================================

_ATTEMPT_LIST_ORDER = "created_asc"


def _require_attempt(attempt_id: str) -> StudentAttempt:
    """저장된 attempt 를 요구한다. 없으면 404 E_ATTEMPT_NOT_FOUND."""
    try:
        return get_repository().get_attempt(attempt_id)
    except RepositoryError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "E_ATTEMPT_NOT_FOUND",
                "message": f"attempt '{attempt_id}' 를 찾을 수 없습니다.",
            },
        ) from None


def _get_record_in_attempt(attempt_id: str, analysis_id: str) -> AnalysisRecord:
    """analysis_id 가 attempt_id 에 속하는지 검증해 반환한다.

    다른 attempt 의 analysis_id 를 넣은 경우에도 404 로 거절한다. 존재 여부를
    새지 않기 위해 '이 attempt 의 이력에 없다' 는 동일하게 표현한다.
    """
    try:
        record = get_repository().get_analysis(analysis_id)
    except RepositoryError:
        record = None
    if record is None or record.attempt_id != attempt_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "E_ANALYSIS_NOT_FOUND",
                "message": f"attempt '{attempt_id}' 에 analysis '{analysis_id}' 이(가) 없습니다.",
            },
        )
    return record


def _to_summary(record: AnalysisRecord) -> AnalysisSummaryItem:
    api = record.analysis.api_response
    return AnalysisSummaryItem(
        analysis_id=record.analysis_id,
        attempt_id=record.attempt_id,
        problem_id=record.problem_id,
        student_id=record.student_id,
        analysis_status=api["analysis_status"],
        correct=api["correct"],
        error_type=api["error_type"],
        error_subtype=api["error_subtype"],
        skill=api["skill"],
        state=api["state"],
        mastery=api["mastery"],
        misconception_id=api["misconception_id"],
        first_error_step_index=api["error_step"],
        confidence=api["confidence"],
        error_count=len(record.analysis.errors),
        observation_count=len(record.analysis.observations),
    )


@app.get(
    "/api/v1/attempts/{attempt_id}/analyses",
    response_model=AnalysisListResponse,
    dependencies=_INTERNAL_AUTH_DEPS,
)
def list_attempt_analyses(
    attempt_id: str,
    reverse: bool = Query(False, description="true 면 최신순으로 뒤집어 반환"),
) -> AnalysisListResponse:
    """attempt 의 분석 이력 목록.

    - attempt 가 없으면 404 `E_ATTEMPT_NOT_FOUND`
    - attempt 는 있으나 이력이 0건이면 200 + 빈 목록 (0건과 미존재를 구분)
    - 정렬은 저장소 생성 순서(오래된→최신) 오름차순이며 reverse=true 로 뒤집는다.
    """
    _require_attempt(attempt_id)
    records = get_repository().list_analyses(attempt_id)  # 저장 순(오래된→최신)
    latest = records[-1].analysis_id if records else None
    return AnalysisListResponse(
        attempt_id=attempt_id,
        analysis_count=len(records),
        latest_analysis_id=latest,
        order=_ATTEMPT_LIST_ORDER,
        analyses=[_to_summary(r) for r in (reversed(records) if reverse else records)],
    )


@app.get(
    "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
    response_model=AnalysisDetailResponse,
    dependencies=_INTERNAL_AUTH_DEPS,
)
def get_attempt_analysis(attempt_id: str, analysis_id: str) -> AnalysisDetailResponse:
    """저장된 분석 1건의 상세 결과 (저장 시점 스냅샷)."""
    _require_attempt(attempt_id)
    record = _get_record_in_attempt(attempt_id, analysis_id)
    base = _to_attempt_response(
        record.analysis, record.analysis_id, len(get_repository().list_analyses(attempt_id))
    )
    return AnalysisDetailResponse(
        **base.model_dump(), raw_result=record.raw_result
    )


# ===========================================================================
# 학생 학습 이력 조회 (PHASE 8-6 / 앱 r34)
# 읽기 전용: 분석 실행·저장·Knowledge State 갱신을 하지 않는다.
# ===========================================================================

_STUDENT_HISTORY_ORDER = "attempted_at_desc"
_DETAIL_ROUTE = "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}"


def _parse_instant(value: str | None, field: str) -> datetime | None:
    """timezone offset 이 포함된 ISO 8601 datetime 을 UTC 로 정규화한다.

    offset 이 없으면(naive) 422 로 거절한다 — 비교 기준이 모호하므로 추측하지 않는다.
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "E_INVALID_INSTANT",
                "message": f"{field} 는 timezone offset 이 포함된 ISO 8601 datetime 이어야 합니다: '{value}'",
            },
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "E_INVALID_INSTANT",
                "message": f"{field} 에 timezone offset 이 없습니다 (예: 2026-09-28T10:00:00Z): '{value}'",
            },
        )
    return parsed.astimezone(timezone.utc)


def _resolve_time_range(
    from_: str | None, to: str | None
) -> tuple[datetime | None, datetime | None]:
    """공통 기간 계약: [from, to) — from 포함, to 제외. 미지정은 전체 기간."""
    start = _parse_instant(from_, "from")
    end = _parse_instant(to, "to")
    if start is not None and end is not None and start >= end:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "E_INVALID_TIME_RANGE",
                "message": (
                    "from 은 to 보다 작아야 합니다 (구간은 [from, to) ): "
                    f"from='{from_}', to='{to}'"
                ),
            },
        )
    return start, end


def _in_range(
    attempted_at: datetime, start: datetime | None, end: datetime | None
) -> bool:
    """[from, to) 포함 판정. 경계가 None 이면 그쪽은 제한하지 않는다."""
    if start is not None and attempted_at < start:
        return False
    if end is not None and attempted_at >= end:
        return False
    return True


def _validated_student_id(student_id: str) -> str:
    """이력/TOP 공통 student_id 검증 (이력 API 와 동일한 정책)."""
    try:
        return validate_student_id(student_id)
    except InvalidStudentIdError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "E_INVALID_STUDENT_ID", "message": str(exc)},
        ) from None


def _student_attempts(
    student_id: str, start: datetime | None, end: datetime | None
) -> list[StudentAttempt]:
    """학생의 Attempt 중 기간에 속하는 것만 반환 (저장 순서 유지).

    이력/TOP 두 엔드포인트가 같은 필터 의미를 쓰도록 한 곳에 둔다.
    """
    return [
        attempt
        for attempt in get_repository().list_attempts(student_id=student_id)
        if _in_range(attempt.attempted_at, start, end)
    ]


def _analysis_detail_path(attempt_id: str, analysis_id: str | None) -> str | None:
    """상세 조회 경로를 만든다. '#' 은 URL fragment 로 잘리므로 %23 으로 인코딩한다."""
    if analysis_id is None:
        return None
    return _DETAIL_ROUTE.format(attempt_id=attempt_id, analysis_id=quote(analysis_id, safe=""))


def _to_history_item(attempt: StudentAttempt) -> StudentHistoryItem:
    """Attempt + 그 attempt 의 최신 분석 스냅샷 + 문제 표시 메타데이터를 변환한다."""
    repo = get_repository()
    # save_attempt 가 problem 존재를 보장하므로 항상 조회된다.
    problem = repo.get_problem(attempt.problem_id)
    records = repo.list_analyses(attempt.attempt_id)  # 저장 순서
    latest: AnalysisRecord | None = records[-1] if records else None
    display = {
        "problem_text": problem.question_text,
        "concept_name": problem.concept_name,
        "unit_name": problem.unit_name,
    }
    if latest is None:
        # 저장소에 attempt 만 있고 분석이 없는 경우 (저장 정책상 드묾)
        return StudentHistoryItem(
            attempt_id=attempt.attempt_id,
            problem_id=attempt.problem_id,
            attempted_at=attempt.attempted_at,
            latest_analysis_id=None,
            detail_path=None,
            analysis_count=0,
            error_count=0,
            observation_count=0,
            step_count=len(attempt.steps),
            **display,
        )
    api = latest.analysis.api_response
    return StudentHistoryItem(
        attempt_id=attempt.attempt_id,
        problem_id=attempt.problem_id,
        attempted_at=attempt.attempted_at,
        latest_analysis_id=latest.analysis_id,
        detail_path=_analysis_detail_path(attempt.attempt_id, latest.analysis_id),
        analysis_count=len(records),
        analysis_status=api["analysis_status"],
        correct=api["correct"],
        error_step=api["error_step"],
        first_error_step_index=api["error_step"],
        error_type=api["error_type"],
        error_subtype=api["error_subtype"],
        skill=api["skill"],
        state=api["state"],
        mastery=api["mastery"],
        misconception_id=api["misconception_id"],
        misconception_tags=sorted(
            {m for e in latest.analysis.errors for m in e.misconception_ids}
        ),
        error_count=len(latest.analysis.errors),
        observation_count=len(latest.analysis.observations),
        step_count=len(attempt.steps),
        **display,
    )


@app.get("/api/v1/students/{student_id}/history", response_model=StudentHistoryResponse, dependencies=_INTERNAL_AUTH_DEPS)
def get_student_history(
    student_id: str,
    page: int = Query(1, ge=1, description="1부터 시작하는 페이지 번호"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수 (1~100)"),
    from_: str | None = Query(
        None,
        alias="from",
        description=(
            "기간 시작 (포함). timezone offset 이 포함된 ISO 8601 datetime. "
            "누락 시 제한 없음. attempted_at 은 서버가 첫 요청을 받은 시각이다."
        ),
    ),
    to: str | None = Query(
        None,
        description="기간 끝 (제외). timezone offset 이 포함된 ISO 8601 datetime. 누락 시 제한 없음",
    ),
) -> StudentHistoryResponse:
    """학생의 Attempt 목록 + 각 Attempt 의 최신 분석 요약.

    - 다른 학생의 기록은 절대 포함하지 않는다 (저장소 student_id 필터).
    - 기록이 없는 학생은 200 + 빈 목록 (total=0) 이다.
    - page/page_size 범위 위반은 422 (page ≥ 1, 1 ≤ page_size ≤ 100).
    - attempted_at(명세 r49) 최신순, 동률은 나중에 저장된 기록 우선.
      attempted_at 은 **서버가 첫 분석 요청을 받은 시각**이며 클라이언트 풀이 시각이 아니다.
    - 적용 순서: 기간 필터([from, to)) → 정렬 → total 계산 → 페이지네이션.
    """
    start, end = _resolve_time_range(from_, to)
    normalized = _validated_student_id(student_id)
    # attempted_at 최신순(명세 r49). 시각이 같으면 나중에 저장된 기록이 먼저 온다.
    # (정렬 키를 명시하므로 Python 의 reverse 안정성에 의존하지 않는다)
    indexed = list(enumerate(_student_attempts(normalized, start, end)))
    indexed.sort(key=lambda pair: (pair[1].attempted_at, pair[0]), reverse=True)
    total = len(indexed)
    offset = (page - 1) * page_size
    window = indexed[offset : offset + page_size]
    return StudentHistoryResponse(
        student_id=normalized,
        items=[_to_history_item(attempt) for _, attempt in window],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
        order=_STUDENT_HISTORY_ORDER,
    )


# ===========================================================================
# 반복 오개념 TOP (PHASE 8-10 / 웹 r30, 앱 r64)
# 읽기 전용. concept_id / Skill↔Concept 매핑은 사용하지 않는다(Phase 8-9 보류).
# ===========================================================================

_MISCONCEPTION_ORDER = "attempt_count_desc"
_MISCONCEPTION_TOP_DEFAULT = 5


@app.get(
    "/api/v1/students/{student_id}/misconceptions/top",
    response_model=MisconceptionTopResponse,
    dependencies=_INTERNAL_AUTH_DEPS,
)
def get_student_top_misconceptions(
    student_id: str,
    limit: int = Query(
        _MISCONCEPTION_TOP_DEFAULT, ge=1, le=20, description="반환할 최대 항목 수 (1~20)"
    ),
    from_: str | None = Query(
        None,
        alias="from",
        description=(
            "기간 시작 (포함). timezone offset 이 포함된 ISO 8601 datetime. "
            "누락 시 제한 없음. attempted_at 은 서버가 첫 요청을 받은 시각이다."
        ),
    ),
    to: str | None = Query(
        None,
        description="기간 끝 (제외). timezone offset 이 포함된 ISO 8601 datetime. 누락 시 제한 없음",
    ),
) -> MisconceptionTopResponse:
    """학생의 반복 오개-concept 상위 N개 (명세 r30 TOP 5, r64 취약 개념 우선순위 카드).

    집계 기준
      - 원본은 저장된 ErrorRecord.misconception_ids (AI① frozen taxonomy ID 만 사용).
        이름·설명·관련 skill 은 taxonomy 조회로 붙인다.
      - 한 Attempt 안의 중복 오류는 attempt_count 1회로 센다.
      - 같은 Attempt 의 분석 이력이 여러 건이면 **최신 AnalysisRecord 1건만** 쓴다.
      - REVIEW_REQUIRED 도 확정된 ErrorRecord 에 taxonomy ID 가 있으면 집계한다.
        UNKNOWN 은 애초에 저장되지 않으며, 근거 없는 계산 오류는 오개념 ID 가 없다.
      - concept_id · SIGN_ERROR 등 AI② 전용 별칭은 쓰지 않는다.
      - 적용 순서: 기간 필터([from, to)) → attempt별 최신 분석 → distinct 집계 → 정렬 → limit.
    """
    start, end = _resolve_time_range(from_, to)
    normalized = _validated_student_id(student_id)

    repo = get_repository()
    counts: dict[str, int] = {}
    for attempt in _student_attempts(normalized, start, end):
        records = repo.list_analyses(attempt.attempt_id)  # 저장 순서
        if not records:
            continue
        latest = records[-1]  # 정책 4: 최신 분석 1건만
        # 정책 2: 같은 Attempt 안의 중복 오개념은 1회만
        seen = {
            mid
            for error in latest.analysis.errors
            for mid in error.misconception_ids
        }
        for mid in seen:
            counts[mid] = counts.get(mid, 0) + 1

    # 정책 6: attempt_count 내림차순, 동률은 misconception_id 오름차순
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    items = []
    for rank, (mid, count) in enumerate(ranked[:limit], start=1):
        entry = misconception_by_id(mid)  # taxonomy 조회 (이름·설명)
        items.append(
            MisconceptionTopItem(
                rank=rank,
                misconception_id=mid,
                name=entry.name,
                description=entry.description,
                attempt_count=count,
                related_skill_ids=list(entry.related_skill_ids),
            )
        )
    return MisconceptionTopResponse(
        student_id=normalized,
        limit=limit,
        order=_MISCONCEPTION_ORDER,
        distinct_misconception_count=len(ranked),
        items=items,
    )
