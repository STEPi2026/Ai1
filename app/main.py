"""AI① Learning Intelligence — FastAPI 엔트리포인트."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query

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
    StepView,
    StudentHistoryItem,
    StudentHistoryResponse,
)
from app.taxonomy import misconception_by_id

app = FastAPI(
    title="AI1 Learning Intelligence",
    version="0.1.0",
    description="풀이 분석 파이프라인: 오류 단계 특정 → 오개념 분류 → Knowledge State",
)

# AnalysisError 코드 → HTTP status 매핑 (S11)
# 매핑에 없는 코드는 422를 사용한다(기존 동작 유지).
#   - E_INPUT_TOO_LARGE: 본문 크기 제한 위반이므로 413 Payload Too Large가 적절
#   - 나머지: 요청 내용 자체가 계약에 맞지 않으므로 422 Unprocessable Entity
_ERROR_STATUS: dict[str, int] = {
    "E_INPUT_TOO_LARGE": 413,
}
_DEFAULT_ERROR_STATUS = 422


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze-solution", response_model=AnalyzeSolutionResponse)
def analyze_solution(req: AnalyzeSolutionRequest) -> AnalyzeSolutionResponse:
    try:
        result = analyze(
            problem_latex=req.problem_latex,
            solution_text=req.solution_text,
            student_id=req.student_id,
            engine=req.engine,
            problem_skill=req.problem_skill,
        )
    except AnalysisError as exc:
        status = _ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS)
        # 기존 detail.code / detail.message 계약은 그대로 두고, 분석을 수행했는데
        # 신뢰 가능한 단계 판정이 없었던 경우에만 analysis_status 를 덧붙인다.
        # 요청/입력 오류에는 붙지 않는다 (UNKNOWN 으로 섞지 않는다).
        detail = {"code": exc.code, "message": str(exc)}
        if exc.code in _ANALYSIS_STATUS_BY_ERROR:
            detail["analysis_status"] = _ANALYSIS_STATUS_BY_ERROR[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return AnalyzeSolutionResponse(**result)


# ---------------------------------------------------------------------------
# attempt 단위 분석 (Phase 8-3)
# ---------------------------------------------------------------------------

_REPOSITORY_ERROR_STATUS: dict[str, int] = {
    "E_REFERENCE_INTEGRITY": 422,
    "E_DUPLICATE_ID": 409,
    "E_ATTEMPT_CONFLICT": 409,
}


def _resolve_problem(req: AnalyzeAttemptRequest) -> Problem:
    """문제를 조회하고, 없으면 요청 정보로 최초 등록한다.

    등록되어 있으면 저장본을 반환한다(요청 본문으로 덮어쓰지 않는다).
    표시 메타데이터(question_text/concept_name/unit_name)도 최초 등록 때만 저장된다.
    """
    repo = get_repository()
    try:
        return repo.get_problem(req.problem_id)
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
        try:
            repo.save_problem(problem)
        except RepositoryError as exc:
            if exc.code != "E_DUPLICATE_ID":
                raise
            # 동시 요청이 먼저 등록했다면 그 저장본이 source of truth 다
            return repo.get_problem(req.problem_id)
        return problem


def _resolve_correct_solution(req: AnalyzeAttemptRequest):
    """정답 풀이를 조회하고, 없으면 요청 정보로 최초 등록한다. 미제공 시 None."""
    if req.correct_solution is None:
        return None
    repo = get_repository()
    try:
        return repo.get_correct_solution(req.correct_solution.solution_id)
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
        try:
            repo.save_correct_solution(solution)
        except RepositoryError as exc:
            if exc.code != "E_DUPLICATE_ID":
                raise
            return repo.get_correct_solution(payload.solution_id)
        return solution


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
    )


def _peek_attempt(attempt_id: str) -> StudentAttempt | None:
    """저장된 attempt 를 조회한다. 없으면 None."""
    try:
        return get_repository().get_attempt(attempt_id)
    except RepositoryError:
        return None


def _assert_same_attempt(stored: StudentAttempt, req: AnalyzeAttemptRequest) -> None:
    """저장본 attempt 의 본문이 요청과 다르면 덮어쓰지 않고 충돌로 거절한다."""
    if (stored.problem_id, stored.student_id, stored.solution_text) != (
        req.problem_id,
        req.student_id,
        req.solution_text,
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
        step_list=_build_step_list(analysis),
        errors=[e.model_dump() for e in analysis.errors],
        observations=[o.model_dump() for o in analysis.observations],
        misconception_tags=sorted({m for e in analysis.errors for m in e.misconception_ids}),
        states=dict(analysis.states),
        mastery_by_skill=dict(analysis.mastery),
    )


def _raise_http(exc: AnalysisError) -> None:
    status = _ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS)
    detail = {"code": exc.code, "message": str(exc)}
    if exc.code in _ANALYSIS_STATUS_BY_ERROR:
        detail["analysis_status"] = _ANALYSIS_STATUS_BY_ERROR[exc.code]
    raise HTTPException(status_code=status, detail=detail)


def _http_from_repository_error(exc: RepositoryError) -> HTTPException:
    return HTTPException(
        status_code=_REPOSITORY_ERROR_STATUS.get(exc.code, _DEFAULT_ERROR_STATUS),
        detail={"code": exc.code, "message": str(exc)},
    )


@app.post("/api/v1/attempts/{attempt_id}/analyze", response_model=AnalyzeAttemptResponse)
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
    """
    received_at = utc_now()  # 정책 1: 요청 접수 시각
    repo = get_repository()
    try:
        problem = _resolve_problem(req)
        solution = _resolve_correct_solution(req)
        stored = _peek_attempt(attempt_id)
        if stored is not None:
            _assert_same_attempt(stored, req)
    except RepositoryError as exc:
        raise _http_from_repository_error(exc) from None

    # 여기부터는 attempt 단위로 직렬화한다 (같은 attempt 의 동시 요청만 대기)
    repo.claim_analysis(attempt_id)
    try:
        if repo.has_analysis(attempt_id):
            # 이미 성공 분석된 제출 — 재분석하지 않고 기존 이력을 반환한다
            existing = repo.get_latest_analysis(attempt_id)
            return _to_attempt_response(
                existing.analysis,
                existing.analysis_id,
                len(repo.list_analyses(attempt_id)),
            )

        attempt = _build_attempt(attempt_id, req, received_at)
        try:
            analysis = analyze_attempt(problem, attempt, solution=solution, engine=req.engine)
        except AnalysisError as exc:
            _raise_http(exc)

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


@app.get("/api/v1/students/{student_id}/history", response_model=StudentHistoryResponse)
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
