"""OCR structured 레이아웃 계약 (capture_mode/layout_detected/layout_mismatch/region_id).

provider는 fake이며 실제 네트워크/Google 호출은 없다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integration.ocr import (
    OcrProvider,
    OcrProviderRequest,
    OcrRawTextResult,
    OcrResult,
    register_ocr_provider,
    reset_ocr_provider,
)
from app.main import app
from app.schemas import OcrBBox, OcrProblem, OcrRegion, OcrStep

client = TestClient(app)
URL = "/api/v1/attempts/{}/ocr"
IMAGE = "https://backend.example.invalid/files/demo.png"


class FakeProvider(OcrProvider):
    def __init__(self, result):
        self.result = result
        self.calls: list[OcrProviderRequest] = []

    def recognize(self, request: OcrProviderRequest):
        self.calls.append(request)
        return self.result


def structured_result(*, steps=True, layout_detected=None):
    return OcrResult(
        model_version="fake-structured-1.0",
        problem=OcrProblem(
            text="2x+3=7", latex_text="2x+3=7", confidence=0.97
        ),
        regions=[
            OcrRegion(
                kind="handwritten",
                bbox=OcrBBox(x=0, y=0, width=100, height=40),
                region_id="R1",
                text="x=2",
                latex_text="x=2",
                confidence=0.9,
            )
        ],
        steps=(
            [
                OcrStep(
                    step_no=1,
                    region_id="R1",
                    text="x=2",
                    latex_text="x=2",
                    confidence=0.9,
                )
            ]
            if steps
            else []
        ),
        layout_detected=layout_detected,
    )


@pytest.fixture(autouse=True)
def _fresh():
    reset_ocr_provider()
    yield
    reset_ocr_provider()


def _post(capture_mode=None, **body):
    payload = {"image_reference": IMAGE}
    if capture_mode is not None:
        payload["capture_mode"] = capture_mode
    payload.update(body)
    return client.post(URL.format("OCR-LAYOUT"), json=payload)


def test_structured_layout_matches_capture_mode():
    register_ocr_provider(FakeProvider(structured_result()))
    r = _post("problem_and_solution")
    assert r.status_code == 200
    body = r.json()
    assert body["capture_mode"] == "problem_and_solution"
    assert body["layout_detected"] == "problem_and_solution"
    assert body["layout_mismatch"] is False
    assert body["regions"][0]["region_id"] == "R1"
    assert body["solution_steps"][0]["region_id"] == "R1"


def test_structured_layout_mismatch_true():
    register_ocr_provider(FakeProvider(structured_result()))
    body = _post("problem_only").json()
    assert body["layout_detected"] == "problem_and_solution"
    assert body["layout_mismatch"] is True


def test_structured_without_capture_mode_has_unknown_mismatch():
    register_ocr_provider(FakeProvider(structured_result()))
    body = _post().json()
    assert body["capture_mode"] is None
    assert body["layout_detected"] == "problem_and_solution"
    assert body["layout_mismatch"] is None


def test_structured_provider_unknown_layout_is_null_mismatch():
    register_ocr_provider(
        FakeProvider(structured_result(layout_detected="unknown"))
    )
    body = _post("problem_and_solution").json()
    assert body["layout_detected"] == "unknown"
    assert body["layout_mismatch"] is None


def test_raw_text_layout_is_unknown_and_echoes_capture_mode():
    register_ocr_provider(
        FakeProvider(
            OcrRawTextResult(
                model_version="fake-raw",
                full_text="2x+3=7",
                confidence=0.9,
            )
        )
    )
    body = _post("problem_only").json()
    assert body["representation"] == "raw_text"
    assert body["capture_mode"] == "problem_only"
    assert body["layout_detected"] == "unknown"
    assert body["layout_mismatch"] is None


def test_invalid_capture_mode_is_422():
    r = _post("problem_and_solution_and_more")
    assert r.status_code == 422
