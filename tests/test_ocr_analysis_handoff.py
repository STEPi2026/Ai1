"""PHASE 9-8B — OCR 결과 → 텍스트 분석 Backend handoff 검증.

목적
    Backend 가 AI① OCR 응답을 **기존 Attempt 분석 요청**으로 변환하는 2단계 흐름을
    fake provider 로 검증한다. AI① 은 두 단계를 서로 호출하지 않는다 —
    이 테스트가 두 API 를 순서대로 호출하는 것은 **Backend orchestration 을 모사**하기
    위한 것이며, 프로덕션 코드에 `/ocr` → `/analyze` 호출은 없다.

변환 규칙 (Backend 가 지켜야 할 계약)
    ``problem_latex``   OCR ``problem.latex_text``
    ``solution_text``   OCR ``solution_steps`` 순서대로 줄바꿈 연결
    읽힌 step          ``latex_text`` 우선, 없으면 읽힌 ``text``
    unreadable step    정확히 ``"???"``
    ``student_id`` / ``problem_id`` / tracking ``problem_skills``
                         **Backend 가 제공** (AI¹ 이 추측하지 않는다)
    호출 금지 조건      OCR 결과가 ``UNREADABLE`` 이거나 검증용 problem LaTeX 가 없으면
                         ``/analyze`` 를 호출하지 않는다. 임의의 문제식·Skill 을
                         채우지 않는다.

검증 범위
    - COMPLETE OCR → ``/analyze`` → ``ANALYZED``
    - PARTIAL OCR → ``???`` 가 unrecognized step 으로 보존되고 판정 가능한 오류와
      혼동되지 않는다
    - OCR 전체 unreadable / 검증용 problem LaTeX 누락 → 분석 호출·Attempt 저장 없음
    - ``/ocr`` 자체는 Knowledge State·Attempt·분석 이력을 변경하지 않는다
    - ``ocr_confidence`` 와 수학 분석 ``confidence`` 가 섞이지 않는다
    - 기존 OCR 결과 ID/page_index 규칙과 분석 멱등성이 유지된다
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integration.ocr import (
    OcrProvider,
    OcrProviderRequest,
    OcrResult,
    register_ocr_provider,
    reset_ocr_provider,
)
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.schemas import UNREADABLE_MARKER, OcrBBox, OcrProblem, OcrRegion, OcrStep

client = TestClient(app)

IMAGE = "https://backend.example/attempts/H1/page1.jpg"

#: Backend 가 제공해야 하는 문맥 (AI¹ 이 만들지 않는다)
BACKEND_STUDENT_ID = "S-H1"
BACKEND_PROBLEM_ID = "P-H1"
BACKEND_PROBLEM_SKILLS = ["linear_equation", "distribution"]


# ===========================================================================
# fake provider (결정적)
# ===========================================================================

class FakeOcrProvider(OcrProvider):
    def __init__(self, result):
        self.result = result
        self.calls: list[OcrProviderRequest] = []

    def recognize(self, request: OcrProviderRequest) -> OcrResult:
        self.calls.append(request)
        return self.result


def complete_result() -> OcrResult:
    """전부 읽힌 결과 — 검증용 problem LaTeX 가 있다."""
    return OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.97),
        regions=[
            OcrRegion(
                kind="printed",
                bbox=OcrBBox(x=40, y=20, width=960, height=120),
                text="2(x-3)=6",
                latex_text="2(x-3)=6",
                confidence=0.97,
            ),
            OcrRegion(
                kind="handwritten",
                bbox=OcrBBox(x=40, y=130, width=900, height=1400),
                confidence=0.95,
            ),
        ],
        steps=[
            OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.96),
            OcrStep(step_no=2, text="2x-6=6", latex_text="2x-6=6", confidence=0.93),
        ],
    )


def partial_result() -> OcrResult:
    """한 단계가 읽히지 않은 결과."""
    return OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.97),
        regions=[],
        steps=[
            OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.96),
            OcrStep(step_no=2, text="2x-3=6", latex_text="2x-3=6", confidence=0.93),
            OcrStep(
                step_no=3,
                text=UNREADABLE_MARKER,
                latex_text=None,
                confidence=0.21,
                unreadable=True,
            ),
        ],
    )


def unreadable_result() -> OcrResult:
    """아무것도 읽히지 않은 결과."""
    return OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.05, unreadable=True),
        regions=[
            OcrRegion(
                kind="handwritten",
                bbox=OcrBBox(x=0, y=0, width=100, height=100),
                text=UNREADABLE_MARKER,
                confidence=0.05,
                unreadable=True,
            )
        ],
        steps=[],
    )


def result_without_problem_latex() -> OcrResult:
    """읽힌 문제가 있지만 **검증용 LaTeX 가 없는** 결과 (status 는 COMPLETE)."""
    return OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text="2(x-3)=6", latex_text=None, confidence=0.9),
        regions=[],
        steps=[
            OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.9),
        ],
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("AI1_OCR_PROVIDER", raising=False)
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()
    yield
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()


# ===========================================================================
# Backend orchestration 모사 (테스트 helper)
# ===========================================================================

def ocr_to_analysis_request(
    ocr_body: dict,
    *,
    student_id: str = BACKEND_STUDENT_ID,
    problem_id: str = BACKEND_PROBLEM_ID,
    problem_skills: list[str] | None = None,
) -> dict | None:
    """OCR 응답 → Attempt 분석 요청 변환 규칙 (Backend orchestration).

    ``None`` 을 반환하면 ``/analyze`` 를 **호출하지 않는다**.

    - 검증용 problem LaTeX 가 없거나 OCR 결과가 ``UNREADABLE`` 이면 호출 금지다.
      임의의 문제식·Skill 을 채우지 않는다.
    - **풀이 단계가 0개여도 호출 금지**다. ``solution_text=""`` 인 요청을 만들어
      보내지 않는다 (AI¹ 은 422 ``E_EMPTY_SOLUTION`` 으로 거절하지만, 의미 없는
      요청이므로 만들지 않는다).
    - 단계가 1개 이상이고 전부 ``"???"`` 면 **호출한다** — 판정 가능한 단계가
      하나도 없어 AI¹ 이 422 ``E_UNRECOGNIZED`` / ``UNKNOWN`` 을 돌려주도록 한다.
    - ``student_id`` / ``problem_id`` / ``problem_skills`` 는 Backend 문맥으로
      주입한다(AI¹ 이 추측하지 않는다).
    - raw_text 표현은 semantic 분리가 아직 안 되었으므로 학생/Backend 보정 전
      ``/analyze`` 를 호출하지 않는다.
    """
    if ocr_body.get("representation") == "raw_text":
        return None
    if ocr_body["status"] == "UNREADABLE":
        return None
    problem_latex = ocr_body["problem"]["latex_text"]
    if not problem_latex:
        return None
    if not ocr_body["solution_steps"]:
        # 풀이 단계 0개 — 빈 풀이로 분석을 시도하지 않는다
        return None

    lines: list[str] = []
    for step in ocr_body["solution_steps"]:
        if step["unreadable"]:
            lines.append(UNREADABLE_MARKER)
        else:
            lines.append(step["latex_text"] or step["text"])

    return {
        "problem_id": problem_id,
        "problem_latex": problem_latex,
        "problem_skills": list(
            problem_skills if problem_skills is not None else BACKEND_PROBLEM_SKILLS
        ),
        "student_id": student_id,
        "solution_text": "\n".join(lines),
    }


def call_ocr(attempt_id: str = "H1", **body):
    payload = {"image_reference": IMAGE}
    payload.update(body)
    return client.post(f"/api/v1/attempts/{attempt_id}/ocr", json=payload)


def run_handoff(provider, *, attempt_id: str = "H1", page_index: int | None = None,
                **context):
    """Backend orchestration 모사 — OCR 호출 후 (가능하면) 분석 호출.

    반환값: ``(ocr_response, analysis_response | None)``
    """
    register_ocr_provider(provider)
    ocr_response = call_ocr(attempt_id, **({"page_index": page_index} if page_index is not None else {}))
    assert ocr_response.status_code == 200, ocr_response.text
    ocr_body = ocr_response.json()
    request = ocr_to_analysis_request(ocr_body, **context)
    if request is None:
        return ocr_response, None
    analysis_response = client.post(
        f"/api/v1/attempts/{attempt_id}/analyze", json=request
    )
    return ocr_response, analysis_response


# ===========================================================================
# 1. COMPLETE OCR → ANALYZED
# ===========================================================================

def test_complete_ocr_is_analyzed_normally():
    ocr_response, analysis = run_handoff(FakeOcrProvider(complete_result()))
    assert ocr_response.json()["status"] == "COMPLETE"
    assert analysis is not None
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["analysis_status"] == "ANALYZED"
    assert body["correct"] is True
    assert body["unrecognized"] == []
    assert body["errors"] == []


def test_complete_ocr_request_uses_ocr_problem_latex_and_step_order():
    _, analysis = run_handoff(FakeOcrProvider(complete_result()))
    assert analysis.status_code == 200
    # 문제식은 OCR 의 problem.latex_text 그대로
    assert analysis.json()["problem_id"] == BACKEND_PROBLEM_ID
    assert analysis.json()["student_id"] == BACKEND_STUDENT_ID
    # 단계 순서가 OCR 배열 순서 그대로다
    assert analysis.json()["steps_latex"] == ["2(x-3)=6", "2x-6=6"]


def test_readable_step_without_latex_falls_back_to_text():
    result = complete_result()
    result.steps = [OcrStep(step_no=1, text="2x=6", latex_text=None, confidence=0.9)]
    _, analysis = run_handoff(FakeOcrProvider(result))
    assert analysis.status_code == 200
    assert analysis.json()["steps_latex"] == ["2x=6"]


# ===========================================================================
# 2. PARTIAL OCR — "???" 보존, 다른 오류와 혼동 없음
# ===========================================================================

def test_partial_ocr_keeps_unreadable_marker_as_unrecognized_step():
    ocr_response, analysis = run_handoff(FakeOcrProvider(partial_result()))
    assert ocr_response.json()["status"] == "PARTIAL"
    assert analysis is not None
    body = analysis.json()
    assert analysis.status_code == 200, analysis.text
    # "???" 는 판정 불가 단계다 — 오답으로 단정되지 않는다
    assert body["analysis_status"] == "REVIEW_REQUIRED"
    assert body["unrecognized"] == [3]
    assert body["valid"] == [True, False, None]


def test_unreadable_marker_is_not_recorded_as_an_error():
    """??? 로 인한 오류 레코드가 새로 만들어지지 않는다."""
    _, analysis = run_handoff(FakeOcrProvider(partial_result()))
    body = analysis.json()
    # 판정 가능한 오류는 2단계(분배법칙) 하나뿐이다
    assert [(e["step_no"], e["error_type"]) for e in body["errors"]] == [
        (2, "concept_error")
    ]
    assert body["error_step"] == 2
    # ??? 때문에 오개념이 새로 붙지 않는다 — 판정 가능한 오류의 오개념만 남는다
    assert body["misconception_id"] == "2.1"
    assert body["misconception_tags"] == ["2.1"]
    assert body["misconception_tag"] == "2.1"
    assert body["official_misconception_codes"] == ["MIS-1-004"]
    # ??? 단계는 unreadable 로 표시된다
    step3 = [s for s in body["step_list"] if s["step_no"] == 3][0]
    assert step3["status"] == "unreviewable"
    assert step3["is_correct"] is None
    assert step3["unrecognized"] is True


def test_only_unreadable_line_is_not_an_error():
    """읽힌 단계 옆의 ??? 는 오류가 아니라 판정 보류다."""
    result = complete_result()
    result.steps = [
        OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.9),
        OcrStep(step_no=2, text=UNREADABLE_MARKER, confidence=0.1, unreadable=True),
    ]
    _, analysis = run_handoff(FakeOcrProvider(result))
    body = analysis.json()
    assert body["errors"] == []
    assert body["unrecognized"] == [2]
    assert body["correct"] is True
    assert body["analysis_status"] == "REVIEW_REQUIRED"


def test_solution_without_any_readable_step_is_rejected_as_unknown():
    """읽힌 단계가 하나도 없으면 `/analyze` 가 422 UNKNOWN 이 된다.

    Backend handoff 규칙의 실제 결과다 — OCR 로 인식된 단계가 전부 `???` 이면
    판정 가능한 단계가 하나도 없어 분석이 성립하지 않는다. Backend 는 이
    응답(422 E_UNRECOGNIZED)을 "분석 실패"로 다루어야 한다.
    """
    result = complete_result()
    result.steps = [
        OcrStep(step_no=1, text=UNREADABLE_MARKER, confidence=0.1, unreadable=True)
    ]
    ocr_response, analysis = run_handoff(FakeOcrProvider(result))
    assert ocr_response.json()["status"] == "PARTIAL"
    assert analysis is not None
    assert analysis.status_code == 422
    detail = analysis.json()["detail"]
    assert detail["code"] == "E_UNRECOGNIZED"
    assert detail["analysis_status"] == "UNKNOWN"
    # UNKNOWN 은 Attempt/분석 이력을 남기지 않는다
    repo = get_repository()
    assert repo.has_analysis("H1") is False
    assert repo.list_attempts() == []


# ===========================================================================
# 3. 분석 호출 금지 조건
# ===========================================================================

def test_unreadable_ocr_does_not_call_analyze():
    ocr_response, analysis = run_handoff(FakeOcrProvider(unreadable_result()))
    assert ocr_response.json()["status"] == "UNREADABLE"
    assert analysis is None, "UNREADABLE 인데 분석을 호출했다"
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.list_problems() == []
    assert repo.has_analysis("H1") is False
    assert client.get("/api/v1/students/S-H1/history").json()["total"] == 0


def test_missing_problem_latex_does_not_call_analyze():
    """검증용 problem LaTeX 가 없으면 분석하지 않는다 (임의 문제식 금지)."""
    ocr_response, analysis = run_handoff(
        FakeOcrProvider(result_without_problem_latex())
    )
    # OCR 자체는 성공이고 status 도 COMPLETE 다 — LaTeX 부재가 호출 금지 사유다
    assert ocr_response.json()["status"] == "COMPLETE"
    assert ocr_response.json()["problem"]["latex_text"] is None
    assert analysis is None
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.has_analysis("H1") is False


def test_empty_solution_steps_does_not_call_analyze():
    """풀이 단계 0개 — 분석 요청을 만들지 않고 아무것도 남기지 않는다.

    OCR status 는 PARTIAL 이고 검증용 problem LaTeX 도 있지만, 풀이 단계가 0개면
    ``solution_text=""`` 요청을 만들지 않는다.
    """
    result = complete_result()
    result.steps = []
    ocr_response, analysis = run_handoff(FakeOcrProvider(result))
    assert ocr_response.json()["status"] == "PARTIAL"
    assert ocr_response.json()["problem"]["latex_text"] == "2(x-3)=6"
    assert ocr_response.json()["solution_steps"] == []
    assert analysis is None
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.list_problems() == []
    assert repo.has_analysis("H1") is False
    # Knowledge State 도 갱신되지 않는다
    rule = analyzer.get_engine("rule")
    assert rule.state(BACKEND_STUDENT_ID, "linear_equation") == "learning"


def test_empty_solution_steps_never_builds_empty_solution_text():
    """빈 풀이 요청을 만들어 보내지 않는다 (방어적 확인)."""
    body = {
        "status": "PARTIAL",
        "problem": {"latex_text": "2(x-3)=6"},
        "solution_steps": [],
    }
    assert ocr_to_analysis_request(body) is None
    # 단계가 하나라도 있으면 요청이 만들어진다 (빈 문자열이 아니다)
    with_step = ocr_to_analysis_request(
        {
            "status": "PARTIAL",
            "problem": {"latex_text": "2(x-3)=6"},
            "solution_steps": [
                {
                    "step_no": 1,
                    "text": UNREADABLE_MARKER,
                    "latex_text": None,
                    "unreadable": True,
                }
            ],
        }
    )
    assert with_step["solution_text"] == UNREADABLE_MARKER


def test_build_request_returns_none_for_guards():
    unreadable_body = {"status": "UNREADABLE", "problem": {"latex_text": None}, "solution_steps": []}
    assert ocr_to_analysis_request(unreadable_body) is None
    no_latex_body = {"status": "COMPLETE", "problem": {"latex_text": None}, "solution_steps": []}
    assert ocr_to_analysis_request(no_latex_body) is None


def test_build_request_returns_none_for_empty_solution_steps():
    """풀이 단계 0개 — ``solution_text=""`` 요청을 만들지 않는다."""
    body = {
        "status": "PARTIAL",
        "problem": {"latex_text": "2(x-3)=6"},
        "solution_steps": [],
    }
    assert ocr_to_analysis_request(body) is None


def test_build_request_keeps_all_unreadable_steps():
    """단계가 1개 이상이고 전부 "???" 면 요청을 만든다 (AI¹ 이 422 UNKNOWN 을 준다)."""
    body = {
        "status": "PARTIAL",
        "problem": {"latex_text": "2(x-3)=6"},
        "solution_steps": [
            {"step_no": 1, "text": UNREADABLE_MARKER, "latex_text": None, "unreadable": True}
        ],
    }
    request = ocr_to_analysis_request(body)
    assert request is not None
    assert request["solution_text"] == "???"


def test_build_request_does_not_invent_problem_or_skills():
    body = {
        "status": "COMPLETE",
        "problem": {"latex_text": "2(x-3)=6"},
        "solution_steps": [
            {"step_no": 1, "text": "2x=6", "latex_text": "2x=6", "unreadable": False}
        ],
    }
    request = ocr_to_analysis_request(body)
    assert request["problem_latex"] == "2(x-3)=6"
    assert request["problem_skills"] == BACKEND_PROBLEM_SKILLS
    assert request["student_id"] == BACKEND_STUDENT_ID
    assert request["problem_id"] == BACKEND_PROBLEM_ID
    # Backend 문맥으로 주입된 값이 그대로 실린다 (추측 없음)
    custom = ocr_to_analysis_request(
        body, student_id="S-OTHER", problem_id="P-OTHER",
        problem_skills=["arithmetic"],
    )
    assert custom["student_id"] == "S-OTHER"
    assert custom["problem_id"] == "P-OTHER"
    assert custom["problem_skills"] == ["arithmetic"]


# ===========================================================================
# 4. OCR endpoint 의 부작용 없음
# ===========================================================================

def test_ocr_endpoint_changes_nothing():
    before_state = analyzer.get_engine("rule").state(BACKEND_STUDENT_ID, "linear_equation")
    before_mastery = analyzer.get_engine("rule").mastery(
        BACKEND_STUDENT_ID, "linear_equation"
    )
    register_ocr_provider(FakeOcrProvider(complete_result()))
    ocr_response = call_ocr("H1")
    assert ocr_response.status_code == 200
    assert analyzer.get_engine("rule").state(
        BACKEND_STUDENT_ID, "linear_equation"
    ) == before_state
    assert analyzer.get_engine("rule").mastery(
        BACKEND_STUDENT_ID, "linear_equation"
    ) == before_mastery
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.list_problems() == []
    assert repo.has_analysis("H1") is False
    assert client.get("/api/v1/students/S-H1/history").json()["total"] == 0


# ===========================================================================
# 5. confidence 분리
# ===========================================================================

def test_ocr_confidence_and_analysis_confidence_are_not_mixed():
    ocr_response, analysis = run_handoff(FakeOcrProvider(partial_result()))
    ocr_body = ocr_response.json()
    analysis_body = analysis.json()

    # OCR 쪽은 ocr_confidence 만 있고 분류 신뢰도 필드가 없다
    assert "ocr_confidence" in ocr_body
    assert "confidence" not in ocr_body
    # 분석 쪽은 분류 confidence 만 있고 OCR 필드가 없다
    assert "confidence" in analysis_body
    assert "ocr_confidence" not in analysis_body
    # 값도 서로 독립이다 (OCR 0.21 / 분류 0.9)
    assert ocr_body["ocr_confidence"] == 0.21
    assert analysis_body["confidence"] == 0.9
    assert ocr_body["ocr_confidence"] != analysis_body["confidence"]


def test_analysis_confidence_absent_when_no_error():
    """오류가 없으면 분류 confidence 도 null 이다 — OCR 값이 대신 들어가지 않는다."""
    _, analysis = run_handoff(FakeOcrProvider(complete_result()))
    body = analysis.json()
    assert body["confidence"] is None
    assert "ocr_confidence" not in body


# ===========================================================================
# 6. 기존 OCR 결과 ID / page_index 규칙 + 분석 멱등성 유지
# ===========================================================================

def test_ocr_result_id_and_page_index_rules_preserved_in_handoff():
    ocr_response, analysis = run_handoff(
        FakeOcrProvider(complete_result()), attempt_id="H2", page_index=0
    )
    body = ocr_response.json()
    assert body["ocr_result_id"] == "H2#ocr-p0"
    assert body["page_index"] == 0
    # 분석 요청은 같은 attempt_id 를 쓴다
    assert analysis.json()["attempt_id"] == "H2"


def test_analysis_idempotency_preserved_through_handoff():
    provider = FakeOcrProvider(complete_result())
    ocr_response, analysis = run_handoff(provider, attempt_id="H3")
    assert analysis.status_code == 200
    first = analysis.json()

    request = ocr_to_analysis_request(ocr_response.json())
    repeat = client.post("/api/v1/attempts/H3/analyze", json=request)
    assert repeat.status_code == 200
    second = repeat.json()

    assert second["analysis_id"] == first["analysis_id"]
    assert second["analysis_count"] == 1
    assert len(get_repository().list_analyses("H3")) == 1
    # 관측은 1회만
    rule = analyzer.get_engine("rule")
    assert rule._streak[(BACKEND_STUDENT_ID, "linear_equation")] == 1


def test_two_handoffs_on_same_attempt_do_not_double_observe():
    provider = FakeOcrProvider(complete_result())
    for _ in range(3):
        _, analysis = run_handoff(provider, attempt_id="H4")
        assert analysis.status_code == 200
    rule = analyzer.get_engine("rule")
    assert rule._streak[(BACKEND_STUDENT_ID, "linear_equation")] == 1


# ===========================================================================
# 7. 기존 계약 불변
# ===========================================================================

def test_ocr_without_provider_still_503():
    reset_ocr_provider()
    r = call_ocr("H5")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "E_OCR_NOT_CONFIGURED"
    assert get_repository().list_attempts() == []


def test_legacy_text_api_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "2(x-3)=6",
            "solution_text": "2(x-3)=6\n2x-3=6",
            "student_id": "legacy",
            "problem_skill": "linear_equation",
        },
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert body["confidence"] == 0.9
    assert "ocr_confidence" not in body


def test_attempt_analysis_response_fields_unchanged_by_handoff():
    _, analysis = run_handoff(FakeOcrProvider(complete_result()))
    body = analysis.json()
    assert len(body) == 31
    assert "ocr_confidence" not in body
