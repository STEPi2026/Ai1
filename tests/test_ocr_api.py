"""PHASE 9-8A — OCR API 경계 및 provider 인터페이스 테스트.

검증 범위
  - provider 미설정 → 503 ``E_OCR_NOT_CONFIGURED`` (결과를 꾸며내지 않는다)
  - fake provider 의 COMPLETE / PARTIAL / UNREADABLE 응답 계약
  - bbox·confidence 범위 검사 (모델 단계에서 거부)
  - 읽을 수 없는 항목을 임의 수식으로 바꾸지 않는다
  - OCR confidence 와 수학 분석 confidence 는 별개 값
  - OCR 호출은 Knowledge State·저장소를 건드리지 않는다
  - 기존 텍스트 분석 응답·Legacy API 는 불변
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.integration import ocr
from app.integration.ocr import (
    OcrImageFetchError,
    OcrProvider,
    OcrProviderError,
    OcrProviderRequest,
    OcrRawTextResult,
    OcrResult,
    OcrUnsupportedImageError,
    OcrUsageLimitError,
    derive_ocr_confidence,
    derive_ocr_status,
    get_ocr_provider,
    register_ocr_provider,
    require_ocr_provider,
    reset_ocr_provider,
)
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.schemas import (
    UNREADABLE_MARKER,
    OcrBBox,
    OcrProblem,
    OcrRegion,
    OcrRawTextLine,
    OcrStep,
    OcrStatus,
)

client = TestClient(app)

IMAGE = "https://backend.example/attempts/A1/page1.jpg"


# ===========================================================================
# fake provider (결정적)
# ===========================================================================

class FakeOcrProvider(OcrProvider):
    """결과를 미리 정해 놓는 결정적 provider."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[OcrProviderRequest] = []

    def recognize(self, request: OcrProviderRequest) -> OcrResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class FakeRawTextOcrProvider(OcrProvider):
    """Google Vision-like raw OCR response for offline contract tests."""

    def __init__(self, result: OcrRawTextResult):
        self.result = result
        self.calls: list[OcrProviderRequest] = []

    def recognize(self, request: OcrProviderRequest) -> OcrRawTextResult:
        self.calls.append(request)
        return self.result


def complete_result() -> OcrResult:
    """전부 읽힌 결과."""
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
            OcrStep(step_no=2, text="2x-3=6", latex_text="2x-3=6", confidence=0.93),
        ],
    )


def partial_result() -> OcrResult:
    """한 단계가 읽히지 않은 결과."""
    result = complete_result()
    result.steps = [
        OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.96),
        OcrStep(step_no=2, text="2x-3=6", latex_text="2x-3=6", confidence=0.93),
        OcrStep(
            step_no=3,
            text=UNREADABLE_MARKER,
            latex_text=None,
            confidence=0.21,
            unreadable=True,
        ),
    ]
    return result


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


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Never let a developer's shell setting auto-enable a real cloud provider in tests.
    monkeypatch.delenv("AI1_OCR_PROVIDER", raising=False)
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()
    yield
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()


def ocr_post(attempt_id="A1", **body):
    payload = {"image_reference": IMAGE}
    payload.update(body)
    return client.post(f"/api/v1/attempts/{attempt_id}/ocr", json=payload)


# ===========================================================================
# 1. provider 미설정 → 503
# ===========================================================================

def test_ocr_without_provider_returns_503():
    r = ocr_post()
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "E_OCR_NOT_CONFIGURED"
    assert set(detail) == {"code", "message"}
    assert isinstance(detail["message"], str) and detail["message"]


def test_ocr_without_provider_fabricates_nothing():
    """provider 가 없으면 결과 본문이 없어야 한다 (응답에 OCR 결과가 없어야 한다)."""
    body = ocr_post().json()
    assert "problem" not in body
    assert "solution_steps" not in body
    assert "status" not in body


def test_ocr_without_provider_stores_nothing_and_keeps_state():
    before = analyzer.get_engine("rule").state("S1", "linear_equation")
    ocr_post()
    assert analyzer.get_engine("rule").state("S1", "linear_equation") == before
    assert get_repository().list_problems() == []
    assert get_repository().list_attempts() == []


def test_require_ocr_provider_raises_when_missing():
    with pytest.raises(ocr.OcrNotConfiguredError):
        require_ocr_provider()
    assert get_ocr_provider() is None


def test_register_and_reset_provider():
    provider = FakeOcrProvider(complete_result())
    register_ocr_provider(provider)
    assert get_ocr_provider() is provider
    reset_ocr_provider()
    assert get_ocr_provider() is None


# ===========================================================================
# 2. COMPLETE / PARTIAL / UNREADABLE 계약
# ===========================================================================

def test_complete_ocr_response_contract():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    r = ocr_post()
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETE"
    assert body["model_version"] == "fake-ocr-1.0"
    assert body["attempt_id"] == "A1"
    # 응답에 이미지 참조 필드가 아예 없다 — URL/서명/hash fingerprint 모두 제외
    assert "image_reference" not in body
    assert IMAGE not in r.text
    assert body["ocr_result_id"]
    assert body["problem"]["text"] == "2(x-3)=6"
    assert body["problem"]["latex_text"] == "2(x-3)=6"
    assert body["problem"]["confidence"] == 0.97
    assert [reg["kind"] for reg in body["regions"]] == ["printed", "handwritten"]
    assert body["regions"][0]["bbox"] == {
        "x": 40.0, "y": 20.0, "width": 960.0, "height": 120.0
    }
    assert [step["step_no"] for step in body["solution_steps"]] == [1, 2]
    assert body["solution_steps"][1]["latex_text"] == "2x-3=6"
    # 전체 신뢰도는 최솟값
    assert body["ocr_confidence"] == min(0.97, 0.97, 0.95, 0.96, 0.93)


def test_partial_ocr_response_contract():
    register_ocr_provider(FakeOcrProvider(partial_result()))
    body = ocr_post().json()
    assert body["status"] == "PARTIAL"
    steps = body["solution_steps"]
    assert [step["step_no"] for step in steps] == [1, 2, 3]
    unreadable = steps[2]
    assert unreadable["unreadable"] is True
    assert unreadable["text"] == UNREADABLE_MARKER
    assert unreadable["latex_text"] is None
    assert unreadable["confidence"] == 0.21
    assert body["ocr_confidence"] == 0.21


def test_unreadable_ocr_response_contract():
    register_ocr_provider(FakeOcrProvider(unreadable_result()))
    body = ocr_post().json()
    assert body["status"] == "UNREADABLE"
    assert body["problem"]["unreadable"] is True
    assert body["problem"]["latex_text"] is None
    assert body["solution_steps"] == []


def test_raw_text_ocr_response_is_partial_and_does_not_guess_semantics():
    """Raw Vision OCR is a review preview, not fabricated problem/solution data."""
    raw = OcrRawTextResult(
        model_version="Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
        full_text="다음 식을 풀어라\n2(x-3)=6\n2x-6=6",
        lines=[
            OcrRawTextLine(
                line_index=0,
                page_index=0,
                text="다음 식을 풀어라",
                confidence=0.96,
                bbox=OcrBBox(x=20, y=10, width=300, height=30),
            ),
            OcrRawTextLine(
                line_index=1,
                page_index=0,
                text="2(x-3)=6",
                confidence=0.91,
                bbox=OcrBBox(x=20, y=50, width=240, height=35),
                bounding_polygon=[[20, 50], [260, 50], [260, 85], [20, 85]],
            ),
        ],
        confidence=0.91,
        page_index=0,
        warnings=["student review required"],
    )
    provider = FakeRawTextOcrProvider(raw)
    register_ocr_provider(provider)

    response = ocr_post(attempt_id="RAW1", page_index=0)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["representation"] == "raw_text"
    assert body["status"] == "PARTIAL"
    assert body["problem"] is None
    assert body["regions"] == []
    assert body["solution_steps"] == []
    assert body["raw_text"] == raw.full_text
    assert body["raw_lines"][1]["text"] == "2(x-3)=6"
    assert body["raw_lines"][1]["bounding_polygon"] == [
        [20.0, 50.0], [260.0, 50.0], [260.0, 85.0], [20.0, 85.0]
    ]
    assert body["ocr_confidence"] == 0.91
    assert provider.calls[0].attempt_id == "RAW1"
    assert IMAGE not in response.text


def test_raw_text_without_recognized_text_is_unreadable():
    provider = FakeRawTextOcrProvider(
        OcrRawTextResult(
            model_version="Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
            full_text="",
            lines=[],
        )
    )
    register_ocr_provider(provider)
    body = ocr_post(attempt_id="RAW2").json()
    assert body["representation"] == "raw_text"
    assert body["status"] == "UNREADABLE"
    assert body["problem"] is None
    assert body["solution_steps"] == []
    assert body["ocr_confidence"] is None


def test_status_is_derived_not_taken_from_provider():
    """provider 가 status 를 말하지 않아도 내용이 맞으면 상태가 맞는다."""
    assert derive_ocr_status(complete_result()) == "COMPLETE"
    assert derive_ocr_status(partial_result()) == "PARTIAL"
    assert derive_ocr_status(unreadable_result()) == "UNREADABLE"
    empty = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.0, unreadable=True),
    )
    assert derive_ocr_status(empty) == "UNREADABLE"
    assert derive_ocr_confidence(empty) == 0.0


# ===========================================================================
# 2-1. status 파생 회귀 (PHASE 9-8A.1)
# ===========================================================================

def test_status_readable_problem_without_steps_is_partial():
    """읽힌 problem 만 있고 풀이 단계가 없으면 PARTIAL (필수 요소 누락)."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.9),
        regions=[],
        steps=[],
    )
    assert derive_ocr_status(result) == "PARTIAL"


def test_status_unreadable_problem_with_readable_steps_is_partial():
    """문제만 못 읽고 풀이 단계는 읽혔으면 PARTIAL (문제가 필수 요소다)."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.1, unreadable=True),
        regions=[],
        steps=[OcrStep(step_no=1, text="2x=6", latex_text="2x=6", confidence=0.8)],
    )
    assert derive_ocr_status(result) == "PARTIAL"


def test_status_complete_even_when_regions_empty():
    """regions 가 비어 있어도(선택 메타데이터) COMPLETE 이다."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.9),
        regions=[],
        steps=[
            OcrStep(step_no=1, text="2(x-3)=6", latex_text="2(x-3)=6", confidence=0.9),
            OcrStep(step_no=2, text="2x-6=6", latex_text="2x-6=6", confidence=0.88),
        ],
    )
    assert derive_ocr_status(result) == "COMPLETE"


def test_status_unreadable_when_everything_unreadable_or_absent():
    """모든 내용이 unreadable 이거나 없으면 UNREADABLE."""
    nothing = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.0, unreadable=True),
    )
    assert derive_ocr_status(nothing) == "UNREADABLE"

    all_unreadable = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.05, unreadable=True),
        regions=[
            OcrRegion(
                kind="handwritten",
                bbox=OcrBBox(x=0, y=0, width=10, height=10),
                text=UNREADABLE_MARKER,
                confidence=0.05,
                unreadable=True,
            )
        ],
        steps=[
            OcrStep(
                step_no=1, text=UNREADABLE_MARKER, confidence=0.05, unreadable=True
            )
        ],
    )
    assert derive_ocr_status(all_unreadable) == "UNREADABLE"


def test_status_partial_when_one_step_unreadable():
    """한 단계만 못 읽으면 PARTIAL."""
    assert derive_ocr_status(partial_result()) == "PARTIAL"


def test_status_partial_when_a_region_is_unreadable():
    """영역 하나가 unreadable 여도 PARTIAL (unreadable 항목이 있으면 COMPLETE 불가)."""
    result = complete_result()
    result.regions.append(
        OcrRegion(
            kind="printed",
            bbox=OcrBBox(x=0, y=0, width=10, height=10),
            text=UNREADABLE_MARKER,
            confidence=0.1,
            unreadable=True,
        )
    )
    assert derive_ocr_status(result) == "PARTIAL"


def test_status_readable_region_only_is_partial():
    """읽을 수 있는 영역만 있고 문제가 읽히지 않았으면 PARTIAL (UNREADABLE 아님)."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.1, unreadable=True),
        regions=[
            OcrRegion(
                kind="printed",
                bbox=OcrBBox(x=0, y=0, width=10, height=10),
                text="2(x-3)=6",
                confidence=0.7,
            )
        ],
        steps=[],
    )
    assert derive_ocr_status(result) == "PARTIAL"


# ===========================================================================
# 2-1-1. readable-content 검증 (PHASE 9-8A.2)
# ===========================================================================

@pytest.mark.parametrize("text,latex_text", [(None, None), ("", None), ("   ", None),
                                            (None, ""), (None, "   "), ("  ", "  ")])
def test_readable_problem_without_any_content_rejected(text, latex_text):
    """읽을 수 있는(unreadable=false) problem 에 내용이 없으면 provider 계약 위반."""
    with pytest.raises(ValidationError):
        OcrProblem(text=text, latex_text=latex_text, confidence=0.5)


@pytest.mark.parametrize("text,latex_text", [(None, None), ("", None), ("   ", None),
                                            (None, ""), (None, "   "), ("  ", "  ")])
def test_readable_step_without_any_content_rejected(text, latex_text):
    with pytest.raises(ValidationError):
        OcrStep(step_no=1, text=text, latex_text=latex_text, confidence=0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "2(x-3)=6", "latex_text": None},
        {"text": None, "latex_text": "2(x-3)=6"},
        {"text": "  ", "latex_text": "2(x-3)=6"},
        {"text": "2(x-3)=6", "latex_text": "2(x-3)=6"},
    ],
)
def test_readable_problem_with_one_of_text_or_latex_accepted(kwargs):
    problem = OcrProblem(confidence=0.5, **kwargs)
    assert problem.text is not None or problem.latex_text is not None


def test_readable_step_with_one_of_text_or_latex_accepted():
    step = OcrStep(step_no=1, text=None, latex_text="2x=6", confidence=0.5)
    assert step.latex_text == "2x=6"


def test_bbox_only_region_is_still_valid():
    """region 은 bbox 만 있어도 유효하다 (내용 요구는 problem/step 에만 있다)."""
    region = OcrRegion(
        kind="printed",
        bbox=OcrBBox(x=0, y=0, width=10, height=10),
        confidence=0.5,
    )
    assert region.text is None and region.latex_text is None


def test_empty_content_problem_rejected_through_api_as_502():
    """provider 가 규칙을 어긴 problem 을 내면 502 (500 이 아니다)."""
    good = complete_result()
    object.__setattr__(good.problem, "text", None)
    object.__setattr__(good.problem, "latex_text", None)
    register_ocr_provider(FakeOcrProvider(good))
    r = ocr_post()
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "E_OCR_PROVIDER_ERROR"


# ===========================================================================
# 2-1-2. bbox-only region 은 readable content 가 아니다 (PHASE 9-8A.2)
# ===========================================================================

def test_status_bbox_only_region_only_is_unreadable():
    """bbox 만 있는 region 은 읽힌 텍스트가 아니다 → UNREADABLE."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.1, unreadable=True),
        regions=[
            OcrRegion(
                kind="printed",
                bbox=OcrBBox(x=0, y=0, width=10, height=10),
                confidence=0.5,
            )
        ],
        steps=[],
    )
    assert derive_ocr_status(result) == "UNREADABLE"


def test_status_region_with_text_is_readable_content():
    """텍스트가 있는 region 은 readable content 로 센다."""
    result = OcrResult(
        model_version="fake-ocr-1.0",
        problem=OcrProblem(text=UNREADABLE_MARKER, confidence=0.1, unreadable=True),
        regions=[
            OcrRegion(
                kind="printed",
                bbox=OcrBBox(x=0, y=0, width=10, height=10),
                text="2(x-3)=6",
                confidence=0.7,
            )
        ],
        steps=[],
    )
    assert derive_ocr_status(result) == "PARTIAL"


# ===========================================================================
# 2-2. ocr_result_id 규칙 (PHASE 9-8A.1)
# ===========================================================================

def test_result_id_default_for_single_page():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    assert ocr_post(attempt_id="B1").json()["ocr_result_id"] == "B1#ocr"


def test_result_id_is_page_scoped_when_page_index_present():
    """page_index 가 있으면 페이지별 ID — 같은 attempt 안에서 중복되지 않는다."""
    register_ocr_provider(FakeOcrProvider(complete_result()))
    first = ocr_post(attempt_id="B2", page_index=0).json()
    second = ocr_post(attempt_id="B2", page_index=1).json()
    assert first["ocr_result_id"] == "B2#ocr-p0"
    assert second["ocr_result_id"] == "B2#ocr-p1"
    assert first["ocr_result_id"] != second["ocr_result_id"]


def test_result_id_unique_across_pages_of_same_attempt():
    result = complete_result()
    result.page_index = 3
    register_ocr_provider(FakeOcrProvider(result))
    body = ocr_post(attempt_id="B3", page_index=3).json()
    assert body["ocr_result_id"] == "B3#ocr-p3"
    assert body["page_index"] == 3


def test_result_id_keeps_provider_supplied_value_even_with_page_index():
    result = complete_result()
    result.ocr_result_id = "ocr-7f3a"
    result.page_index = 2
    register_ocr_provider(FakeOcrProvider(result))
    body = ocr_post(attempt_id="B4", page_index=2).json()
    assert body["ocr_result_id"] == "ocr-7f3a"


def test_single_page_id_does_not_change_with_request_page_index_absent():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    body = ocr_post(attempt_id="B5").json()
    assert body["ocr_result_id"] == "B5#ocr"
    assert body["page_index"] is None


def test_ocr_result_id_derived_when_provider_omits_it():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    body = ocr_post(attempt_id="A77").json()
    assert body["ocr_result_id"] == "A77#ocr"


def test_provider_result_id_kept_when_supplied():
    result = complete_result()
    result.ocr_result_id = "ocr-7f3a"
    register_ocr_provider(FakeOcrProvider(result))
    assert ocr_post().json()["ocr_result_id"] == "ocr-7f3a"


# ===========================================================================
# 3. bbox / confidence 범위 검사
# ===========================================================================

@pytest.mark.parametrize(
    "bbox",
    [
        {"x": -1, "y": 0, "width": 10, "height": 10},
        {"x": 0, "y": -1, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": -10, "height": 10},
        {"x": 0, "y": 0, "width": 10, "height": -10},
    ],
)
def test_negative_bbox_rejected(bbox):
    with pytest.raises(ValidationError):
        OcrBBox(**bbox)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 2.0, -1.0])
def test_out_of_range_confidence_rejected(confidence):
    with pytest.raises(ValidationError):
        OcrStep(step_no=1, text="x", confidence=confidence)
    with pytest.raises(ValidationError):
        OcrRegion(
            kind="printed",
            bbox=OcrBBox(x=0, y=0, width=1, height=1),
            confidence=confidence,
        )


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_boundary_confidence_accepted(confidence):
    step = OcrStep(step_no=1, text="x", confidence=confidence)
    assert step.confidence == confidence


def test_invalid_provider_result_becomes_502():
    """provider 가 규칙을 어긴 결과를 돌리면 502 다 (500 이 아니다)."""
    bad = complete_result()
    object.__setattr__(bad.problem, "confidence", 5.0)  # 검증 우회
    register_ocr_provider(FakeOcrProvider(bad))
    r = ocr_post()
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "E_OCR_PROVIDER_ERROR"


# ===========================================================================
# 4. 읽을 수 없는 항목을 임의 수식으로 바꾸지 않는다
# ===========================================================================

def test_unreadable_step_cannot_carry_latex():
    with pytest.raises(ValidationError):
        OcrStep(
            step_no=1,
            text=UNREADABLE_MARKER,
            latex_text="2x=6",
            confidence=0.1,
            unreadable=True,
        )


def test_unreadable_region_cannot_carry_latex():
    with pytest.raises(ValidationError):
        OcrRegion(
            kind="handwritten",
            bbox=OcrBBox(x=0, y=0, width=10, height=10),
            latex_text="x=3",
            confidence=0.1,
            unreadable=True,
        )


def test_unreadable_problem_cannot_carry_latex():
    with pytest.raises(ValidationError):
        OcrProblem(text=UNREADABLE_MARKER, latex_text="2(x-3)=6", confidence=0.1,
                   unreadable=True)


def test_unreadable_item_cannot_carry_restored_content():
    """읽을 수 없는 항목에 임의 내용을 채워 넣는 것도 금지된다."""
    with pytest.raises(ValidationError):
        OcrStep(step_no=1, text="2x=6", confidence=0.1, unreadable=True)


@pytest.mark.parametrize("text", [None, "", "   ", "2x=6"])
def test_unreadable_item_text_must_be_exact_marker(text):
    """문서 계약(`text="???"`)과 스키마를 일치시킨다 — 표식은 정확히 "???" 뿐이다."""
    with pytest.raises(ValidationError):
        OcrStep(
            step_no=1, text=text, confidence=0.1, unreadable=True
        )
    with pytest.raises(ValidationError):
        OcrRegion(
            kind="printed",
            bbox=OcrBBox(x=0, y=0, width=10, height=10),
            text=text,
            confidence=0.1,
            unreadable=True,
        )
    with pytest.raises(ValidationError):
        OcrProblem(text=text, confidence=0.1, unreadable=True)


def test_unreadable_item_accepts_exact_marker():
    step = OcrStep(step_no=1, text=UNREADABLE_MARKER, confidence=0.1, unreadable=True)
    assert step.text == "???"


def test_unreadable_marker_is_preserved_verbatim_in_response():
    register_ocr_provider(FakeOcrProvider(partial_result()))
    body = ocr_post().json()
    unreadable = body["solution_steps"][2]
    assert unreadable["text"] == "???"
    assert unreadable["latex_text"] is None
    # 임의의 식이 붙지 않는다
    assert "2x=6" not in unreadable["text"]
    assert unreadable["latex_text"] is None


def test_no_fabricated_latex_anywhere_in_unreadable_response():
    register_ocr_provider(FakeOcrProvider(unreadable_result()))
    body = ocr_post().json()
    assert body["problem"]["latex_text"] is None
    for region in body["regions"]:
        assert region["latex_text"] is None
    assert body["solution_steps"] == []


# ===========================================================================
# 5. confidence 분리 / 부작용 없음
# ===========================================================================

def test_ocr_confidence_is_separate_from_analysis_confidence():
    """OCR 응답에는 분류 신뢰도인 `confidence` 필드가 없다."""
    register_ocr_provider(FakeOcrProvider(complete_result()))
    body = ocr_post().json()
    assert "ocr_confidence" in body
    assert "confidence" not in body
    # 항목별 신뢰도는 OCR 신뢰도다
    assert "confidence" in body["problem"]
    assert "confidence" in body["regions"][0]
    assert "confidence" in body["solution_steps"][0]


def test_text_analysis_response_still_uses_plain_confidence():
    r = client.post(
        "/api/v1/attempts/A2/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": "2(x-3)=6",
            "problem_skills": ["linear_equation", "distribution"],
            "student_id": "S1",
            "solution_text": "2(x-3)=6\n2x-3=6",
        },
    )
    body = r.json()
    assert r.status_code == 200
    assert body["confidence"] == 0.9
    assert "ocr_confidence" not in body


def test_ocr_does_not_update_knowledge_state():
    before_state = analyzer.get_engine("rule").state("S1", "linear_equation")
    before_mastery = analyzer.get_engine("rule").mastery("S1", "linear_equation")
    register_ocr_provider(FakeOcrProvider(complete_result()))
    ocr_post()
    assert analyzer.get_engine("rule").state("S1", "linear_equation") == before_state
    assert analyzer.get_engine("rule").mastery("S1", "linear_equation") == before_mastery


def test_ocr_stores_no_attempt_or_analysis():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    ocr_post(attempt_id="A3")
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.list_problems() == []
    assert repo.has_analysis("A3") is False


def test_ocr_does_not_create_history_entry():
    register_ocr_provider(FakeOcrProvider(complete_result()))
    ocr_post()
    history = client.get("/api/v1/students/S1/history").json()
    assert history["total"] == 0


# ===========================================================================
# 6. provider 호출 계약 / 오류
# ===========================================================================

def test_provider_receives_image_reference_and_attempt_id():
    provider = FakeOcrProvider(complete_result())
    register_ocr_provider(provider)
    ocr_post(attempt_id="A9", image_reference=IMAGE, page_index=1)
    assert len(provider.calls) == 1
    request = provider.calls[0]
    assert request.attempt_id == "A9"
    assert request.image_reference == IMAGE
    assert request.page_index == 1


def test_provider_failure_returns_502():
    register_ocr_provider(FakeOcrProvider(error=OcrProviderError("vendor timeout")))
    r = ocr_post()
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["code"] == "E_OCR_PROVIDER_ERROR"
    assert "vendor timeout" in detail["message"]


def test_unexpected_provider_exception_returns_502_not_500():
    register_ocr_provider(FakeOcrProvider(error=RuntimeError("boom")))
    r = ocr_post()
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "E_OCR_PROVIDER_ERROR"


@pytest.mark.parametrize(
    "error",
    [OcrProviderError("vendor timeout"), RuntimeError("boom")],
)
def test_provider_failure_causes_exactly_one_call_without_retry(error):
    """PHASE 9-9C.9A 갭 보강 (A-10) — provider 오류·예외가 재호출을 만들지 않는다.

    AI¹ 은 어떤 오류도 재시도하지 않는다(협의서 §3.2 #8·§4.6). provider 가
    호출마다 예외를 던지는 fake 라면 재시도 구현이 있으면 calls 가 2 이상 된다.
    """
    provider = FakeOcrProvider(error=error)
    register_ocr_provider(provider)
    r = ocr_post()
    assert r.status_code == 502
    assert len(provider.calls) == 1


def test_provider_returning_wrong_type_returns_502():
    class BadProvider(OcrProvider):
        def recognize(self, request):
            return {"status": "COMPLETE"}

    register_ocr_provider(BadProvider())
    r = ocr_post()
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "E_OCR_PROVIDER_ERROR"


def test_usage_limit_error_maps_to_429():
    """협의서 §4.6.3: 로컬 가드·quota 상한은 429 (재시도 대상 아님)."""
    register_ocr_provider(FakeOcrProvider(error=OcrUsageLimitError("local cap reached")))
    r = ocr_post()
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "E_OCR_USAGE_LIMIT_REACHED"
    assert set(detail) == {"code", "message"}
    assert IMAGE not in r.text


@pytest.mark.parametrize(
    ("error_class", "expected_code"),
    [
        (OcrImageFetchError, "E_OCR_IMAGE_FETCH_FAILED"),
        (OcrUnsupportedImageError, "E_OCR_UNSUPPORTED_IMAGE"),
    ],
)
def test_ocr_422_error_mapping(error_class, expected_code):
    """이미지 다운로드 실패·지원하지 않는 이미지는 422 (협의서 §4.6.3)."""
    register_ocr_provider(FakeOcrProvider(error=error_class("payload problem")))
    r = ocr_post()
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == expected_code
    assert IMAGE not in r.text


def test_phase_one_single_page_contract():
    """1단계 OCR 계약: 이미지 1장, page_index=0 (협의서 §3.3 #12·§4.2)."""
    provider = FakeOcrProvider(complete_result())
    register_ocr_provider(provider)
    r = ocr_post(page_index=0)
    assert r.status_code == 200
    body = r.json()
    assert body["page_index"] == 0
    assert provider.calls[0].page_index == 0


# ===========================================================================
# 7. 요청 검증
# ===========================================================================

def test_blank_image_reference_rejected():
    r = client.post("/api/v1/attempts/A1/ocr", json={"image_reference": "   "})
    assert r.status_code == 422


def test_missing_image_reference_rejected():
    r = client.post("/api/v1/attempts/A1/ocr", json={})
    assert r.status_code == 422


def test_unknown_request_field_rejected():
    r = client.post(
        "/api/v1/attempts/A1/ocr",
        json={"image_reference": IMAGE, "image_bytes": "base64..."},
    )
    assert r.status_code == 422


def test_no_multipart_upload_accepted():
    """사진 bytes 업로드 경로는 만들지 않는다 (Backend 가 보관·전달)."""
    r = client.post(
        "/api/v1/attempts/A1/ocr",
        files={"file": ("a.jpg", b"binary", "image/jpeg")},
    )
    assert r.status_code == 422


# ===========================================================================
# 8. 기존 계약 불변
# ===========================================================================

def test_legacy_analyze_solution_unchanged():
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


def test_ocr_route_added_without_changing_existing_routes():
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "")
        not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
    }
    assert paths == {
        "/health",
        "/analyze-solution",
        "/api/v1/attempts/{attempt_id}/analyze",
        "/api/v1/attempts/{attempt_id}/ocr",
        "/api/v1/attempts/{attempt_id}/analyses",
        "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
        "/api/v1/students/{student_id}/history",
        "/api/v1/students/{student_id}/misconceptions/top",
    }


def test_ocr_status_type_is_closed():
    assert OcrStatus.__args__ == ("COMPLETE", "PARTIAL", "UNREADABLE")
