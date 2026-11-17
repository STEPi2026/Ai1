"""Google Vision app provider tests. RPCs are faked; tests never contact Cloud."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integration.google_vision import ALLOWED_HOSTS_ENV, GoogleVisionProvider
from app.integration.google_vision_budget import BudgetExceeded
from app.integration.ocr import (
    OcrApiDisabledError,
    OcrAuthenticationError,
    OcrBillingRequiredError,
    OcrImageFetchError,
    OcrNotConfiguredError,
    OcrProviderError,
    OcrProviderRequest,
    OcrRawTextResult,
    OcrUnsupportedImageError,
    OcrUsageLimitError,
    register_ocr_provider,
    reset_ocr_provider,
)
from app.main import app

IMAGE_URI = "https://backend.example.invalid/files/demo.png?exp=900&sig=FAKE-SIGNATURE"


def _vertices(x, y, width, height):
    return [
        SimpleNamespace(x=x, y=y),
        SimpleNamespace(x=x + width, y=y),
        SimpleNamespace(x=x + width, y=y + height),
        SimpleNamespace(x=x, y=y + height),
    ]


def _word(text, x, y, confidence=0.9):
    symbols = [
        SimpleNamespace(text=character)
        for character in text
    ]
    return SimpleNamespace(
        symbols=symbols,
        confidence=confidence,
        bounding_box=SimpleNamespace(vertices=_vertices(x, y, max(12, len(text) * 12), 24)),
    )


def _vision_annotation():
    """Synthetic Vision TextAnnotation: Korean prompt and math expressions."""
    words = [
        _word("다음", 20, 20, 0.98),
        _word("식을", 70, 20, 0.97),
        _word("풀어라", 120, 20, 0.95),
        _word("2(x-3)=6", 20, 70, 0.91),
        _word("2x-6=6", 20, 120, 0.89),
    ]
    paragraph = SimpleNamespace(words=words)
    block = SimpleNamespace(paragraphs=[paragraph])
    page = SimpleNamespace(width=1200, height=1600, confidence=0.96, blocks=[block])
    return SimpleNamespace(
        text="다음 식을 풀어라\n2(x-3)=6\n2x-6=6",
        pages=[page],
    )


class FakeVisionTypes:
    class ImageSource:
        def __init__(self, *, image_uri):
            self.image_uri = image_uri

    class Image:
        def __init__(self, *, source):
            self.source = source

    class ImageContext:
        def __init__(self, *, language_hints):
            self.language_hints = language_hints


class FakeVisionClient:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else _vision_annotation()
        self.error = error
        self.calls = []

    def document_text_detection(self, *, image, image_context, timeout, retry):
        self.calls.append({
            "image": image,
            "image_context": image_context,
            "timeout": timeout,
            "retry": retry,
        })
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    monkeypatch.delenv("AI1_OCR_PROVIDER", raising=False)
    reset_ocr_provider()
    yield
    reset_ocr_provider()


def _provider(client=None, *, language_hint="ko", reserve_slot=None,
             allowed_hosts=None):
    return GoogleVisionProvider(
        client or FakeVisionClient(),
        FakeVisionTypes,
        language_hint=language_hint,
        allowed_hosts=allowed_hosts,
        reserve_slot=reserve_slot or (lambda *args, **kwargs: 1),
    )


def test_google_vision_returns_raw_korean_text_and_math_lines():
    client = FakeVisionClient()
    reservations = []
    provider = _provider(
        client,
        reserve_slot=lambda run_id, image_id, **kwargs: reservations.append(
            (run_id, image_id, kwargs["cap"])
        ) or 1,
    )

    result = provider.recognize(
        OcrProviderRequest(attempt_id="G01", image_reference=IMAGE_URI, page_index=0)
    )

    assert isinstance(result, OcrRawTextResult)
    assert result.full_text == "다음 식을 풀어라\n2(x-3)=6\n2x-6=6"
    assert [line.text for line in result.lines] == [
        "다음 식을 풀어라", "2(x-3)=6", "2x-6=6"
    ]
    assert result.lines[1].confidence == pytest.approx(0.91)
    assert result.confidence == pytest.approx(0.89)
    assert result.page_index == 0
    assert result.lines[1].bbox.x == pytest.approx(20)
    assert reservations and reservations[0][2] == 40
    assert client.calls[0]["image"].source.image_uri == IMAGE_URI
    assert client.calls[0]["image_context"].language_hints == ["ko"]
    assert client.calls[0]["retry"] is None


def test_google_vision_request_can_omit_language_hint():
    client = FakeVisionClient()
    _provider(client, language_hint=None).recognize(
        OcrProviderRequest(attempt_id="G02", image_reference=IMAGE_URI)
    )
    assert client.calls[0]["image_context"] is None


def test_single_provider_call_per_request_with_default_timeout():
    """한 AI¹ 요청 → provider 호출 정확히 1회다 (PHASE 9-9C.7).

    - AI¹ 은 자동 재시도 루프를 갖지 않고, SDK retry 도 `None` 이다
      (재시도 정책 소유는 Backend).
    - `GOOGLE_VISION_TIMEOUT_SECONDS` 미설정 시 기본 timeout 8초가
      실제 SDK 호출에 전달된다.
    """
    client = FakeVisionClient()
    _provider(client, language_hint=None).recognize(
        OcrProviderRequest(attempt_id="G07", image_reference=IMAGE_URI)
    )
    assert len(client.calls) == 1
    assert client.calls[0]["retry"] is None
    assert client.calls[0]["timeout"] == 8.0


def test_official_sdk_request_types_and_method_signature():
    """Check installed SDK message types/signature without constructing a client."""
    from inspect import Parameter, signature
    from google.cloud import vision

    request = vision.AnnotateImageRequest(
        image=vision.Image(source=vision.ImageSource(image_uri=IMAGE_URI)),
        features=[vision.Feature(type=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
        image_context=vision.ImageContext(language_hints=["ko"]),
    )
    parameters = signature(vision.ImageAnnotatorClient.document_text_detection).parameters

    assert request.image.source.image_uri == IMAGE_URI
    assert request.features[0].type == vision.Feature.Type.DOCUMENT_TEXT_DETECTION
    assert request.image_context.language_hints == ["ko"]
    assert {"image", "retry", "timeout"} <= set(parameters)
    assert any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values())


def test_provider_missing_google_application_credentials_is_clear():
    with pytest.raises(OcrNotConfiguredError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        GoogleVisionProvider.from_environment(env={})


def test_provider_missing_credentials_file_is_clear(tmp_path):
    with pytest.raises(OcrNotConfiguredError, match="파일이 없습니다"):
        GoogleVisionProvider.from_environment(
            env={"GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "missing.json")},
            client_factory=lambda: FakeVisionClient(),
            vision_module=FakeVisionTypes,
        )


def test_provider_uses_google_application_credentials_path_without_reading_key(tmp_path):
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")
    created = []
    fake_client = FakeVisionClient()

    provider = GoogleVisionProvider.from_environment(
        env={
            "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
            "GOOGLE_VISION_LANGUAGE_HINT": "ko",
        },
        client_factory=lambda: created.append("constructed") or fake_client,
        vision_module=FakeVisionTypes,
        reserve_slot=lambda *args, **kwargs: 1,
    )

    assert created == ["constructed"]
    assert provider.client is fake_client
    assert provider.language_hint == "ko"
    assert provider.timeout == 8.0


def test_provider_maps_client_initialization_authentication_error(tmp_path):
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")

    def fail_factory():
        raise RuntimeError("DefaultCredentialsError: invalid service account")

    with pytest.raises(OcrAuthenticationError, match="service-account JSON"):
        GoogleVisionProvider.from_environment(
            env={"GOOGLE_APPLICATION_CREDENTIALS": str(credential_path)},
            client_factory=fail_factory,
            vision_module=FakeVisionTypes,
        )


@pytest.mark.parametrize(
    "error,expected",
    [
        (RuntimeError("SERVICE_DISABLED: Vision API has not been used in project"), OcrApiDisabledError),
        (RuntimeError("billing account is not enabled"), OcrBillingRequiredError),
        # 실제 Google 403 원문 (#10) — "requires billing to be enabled"
        (
            RuntimeError(
                "PermissionDenied: 403 This API method requires billing to be "
                "enabled. Please enable billing on project #783665453751"
            ),
            OcrBillingRequiredError,
        ),
        (RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"), OcrUsageLimitError),
        (RuntimeError("PERMISSION_DENIED"), OcrAuthenticationError),
        (RuntimeError("image fetch failed: requested entity was not found"), OcrImageFetchError),
        (RuntimeError("Unsupported image format: wrong MIME"), OcrUnsupportedImageError),
        (RuntimeError("Bad image data."), OcrUnsupportedImageError),
        (RuntimeError("upstream timeout"), OcrProviderError),
    ],
)
def test_google_errors_have_actionable_types_and_redact_signed_url(error, expected):
    client = FakeVisionClient(error=error)
    provider = _provider(client)

    with pytest.raises(expected) as excinfo:
        provider.recognize(
            OcrProviderRequest(attempt_id="G03", image_reference=IMAGE_URI)
        )

    assert IMAGE_URI not in str(excinfo.value)
    assert "FAKE-SIGNATURE" not in str(excinfo.value)
    # 실패 시에도 재시도하지 않는다 — provider 호출은 정확히 1회다
    assert len(client.calls) == 1


def test_usage_cap_denies_before_google_request():
    client = FakeVisionClient()
    provider = _provider(
        client,
        reserve_slot=lambda *args, **kwargs: (_ for _ in ()).throw(BudgetExceeded("cap")),
    )

    with pytest.raises(OcrUsageLimitError):
        provider.recognize(
            OcrProviderRequest(attempt_id="G04", image_reference=IMAGE_URI)
        )

    assert client.calls == []


def test_unsupported_image_scheme_is_rejected_before_budget_or_google_call():
    """협의서 §3.3 #10: HTTPS 서명 URL만 받는다 — gs:// 도 사용하지 않는다."""
    client = FakeVisionClient()
    reserved = []
    provider = _provider(client, reserve_slot=lambda *args, **kwargs: reserved.append(args))

    with pytest.raises(OcrUnsupportedImageError, match="must be an HTTPS signed URL"):
        provider.recognize(
            OcrProviderRequest(attempt_id="G05", image_reference="http://bad.example/x.png")
        )
    with pytest.raises(OcrUnsupportedImageError, match="must be an HTTPS signed URL"):
        provider.recognize(
            OcrProviderRequest(attempt_id="G05b", image_reference="gs://bucket/demo.png")
        )

    assert client.calls == []
    assert reserved == []


def test_allowed_hosts_env_is_parsed_from_environment(tmp_path):
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")
    provider = GoogleVisionProvider.from_environment(
        env={
            "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
            ALLOWED_HOSTS_ENV: " backend.example.invalid , other.example ,",
            "GOOGLE_VISION_TIMEOUT_SECONDS": "25",
        },
        client_factory=lambda: FakeVisionClient(),
        vision_module=FakeVisionTypes,
        reserve_slot=lambda *args, **kwargs: 1,
    )

    assert provider.allowed_hosts == ("backend.example.invalid", "other.example")
    # 협의서 §3.2 #7: 설정 상한은 25초 — 25.0 을 경곗값으로 수용한다
    assert provider.timeout == 25.0


def test_allowed_hosts_unset_means_https_only_without_host_filter():
    provider = _provider(FakeVisionClient())
    assert provider.allowed_hosts is None
    provider.recognize(OcrProviderRequest(attempt_id="G08", image_reference=IMAGE_URI))
    assert len(provider.client.calls) == 1


@pytest.mark.parametrize(
    "value",
    ["https://backend.example", "backend.example/path", "backend.example:443"],
)
def test_invalid_allowed_hosts_fail_closed_at_configuration(tmp_path, value):
    """스킴/경로/포트가 붙은 항목은 hostname exact match 와 어긋난다 → 503."""
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")

    with pytest.raises(OcrNotConfiguredError, match="bare host names"):
        GoogleVisionProvider.from_environment(
            env={
                "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
                ALLOWED_HOSTS_ENV: value,
            },
            client_factory=lambda: FakeVisionClient(),
            vision_module=FakeVisionTypes,
            reserve_slot=lambda *args, **kwargs: 1,
        )


def test_allowed_hosts_rejects_foreign_host_before_budget_or_google_call():
    client = FakeVisionClient()
    reserved = []
    provider = _provider(
        client,
        allowed_hosts=("storage-backend.example",),
        reserve_slot=lambda *args, **kwargs: reserved.append(args),
    )

    with pytest.raises(OcrUnsupportedImageError, match="allowed OCR host"):
        provider.recognize(
            OcrProviderRequest(attempt_id="G09", image_reference=IMAGE_URI)
        )

    assert client.calls == []
    assert reserved == []


def test_timeout_above_25_seconds_fails_closed(tmp_path):
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")

    with pytest.raises(OcrNotConfiguredError, match="25 seconds"):
        GoogleVisionProvider.from_environment(
            env={
                "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
                "GOOGLE_VISION_TIMEOUT_SECONDS": "30",
            },
            client_factory=lambda: FakeVisionClient(),
            vision_module=FakeVisionTypes,
            reserve_slot=lambda *args, **kwargs: 1,
        )


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_finite_timeout_fails_closed(tmp_path, value):
    """NaN/무한대는 0<v<=25 비교를 통과해 버리므로 명시적으로 거부한다."""
    credential_path = tmp_path / "fake-service-account.json"
    credential_path.write_text("{}", encoding="utf-8")

    with pytest.raises(OcrNotConfiguredError, match="positive finite number"):
        GoogleVisionProvider.from_environment(
            env={
                "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
                "GOOGLE_VISION_TIMEOUT_SECONDS": value,
            },
            client_factory=lambda: FakeVisionClient(),
            vision_module=FakeVisionTypes,
            reserve_slot=lambda *args, **kwargs: 1,
        )


def test_api_provider_selection_requires_explicit_setting(monkeypatch):
    from app.integration import ocr

    ocr.reset_ocr_provider()
    monkeypatch.delenv("AI1_OCR_PROVIDER", raising=False)
    with pytest.raises(OcrNotConfiguredError):
        ocr.require_ocr_provider()

    monkeypatch.setenv("AI1_OCR_PROVIDER", "unknown-provider")
    with pytest.raises(OcrNotConfiguredError, match="지원하지 않는 OCR provider"):
        ocr.require_ocr_provider()

    ocr.reset_ocr_provider()


def test_ocr_route_returns_raw_text_preview_without_semantic_guessing():
    client = FakeVisionClient()
    register_ocr_provider(_provider(client))
    try:
        response = TestClient(app).post(
            "/api/v1/attempts/G06/ocr",
            json={"image_reference": IMAGE_URI, "page_index": 0},
        )
    finally:
        reset_ocr_provider()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["representation"] == "raw_text"
    assert body["status"] == "PARTIAL"
    assert body["problem"] is None
    assert body["regions"] == []
    assert body["solution_steps"] == []
    assert body["raw_text"].startswith("다음 식을 풀어라")
    # 응답에 이미지 참조 필드 자체가 없다 (URL/서명/hash fingerprint 모두 제외)
    assert "image_reference" not in body
    assert IMAGE_URI not in response.text


@pytest.mark.parametrize(
    "error,expected_status,expected_code",
    [
        (RuntimeError("SERVICE_DISABLED"), 503, "E_OCR_API_DISABLED"),
        (RuntimeError("PERMISSION_DENIED"), 503, "E_OCR_AUTH_FAILED"),
        (RuntimeError("billing account is not enabled"), 503, "E_OCR_BILLING_REQUIRED"),
        # 실제 Google 403 원문 (#10) — "requires billing to be enabled"
        (
            RuntimeError(
                "PermissionDenied: 403 This API method requires billing to be "
                "enabled. Please enable billing on project #783665453751"
            ),
            503,
            "E_OCR_BILLING_REQUIRED",
        ),
        # 협의서 §4.6.3: 로컬 가드·quota 상한은 429 (재시도 대상 아님)
        (RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"), 429, "E_OCR_USAGE_LIMIT_REACHED"),
        (RuntimeError("requested entity was not found"), 422, "E_OCR_IMAGE_FETCH_FAILED"),
        (RuntimeError("Unsupported image format"), 422, "E_OCR_UNSUPPORTED_IMAGE"),
        # Vision 의 손상 바이트 응답 (INVALID_ARGUMENT: Bad image data.)
        (RuntimeError("Bad image data."), 422, "E_OCR_UNSUPPORTED_IMAGE"),
    ],
)
def test_ocr_route_returns_clear_non_retryable_setup_errors(
    error, expected_status, expected_code
):
    register_ocr_provider(_provider(FakeVisionClient(error=error)))
    try:
        response = TestClient(app).post(
            "/api/v1/attempts/G07/ocr",
            json={"image_reference": IMAGE_URI},
        )
    finally:
        reset_ocr_provider()

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert IMAGE_URI not in response.text


def test_vision_billing_403_is_not_reported_as_retryable_provider_error():
    """#10 회귀: 결제 미설정 403 이 502 E_OCR_PROVIDER_ERROR 로 나가면 Backend 가 재시도한다."""
    message = (
        "PermissionDenied: 403 This API method requires billing to be enabled. "
        "Please enable billing on project #783665453751 by visiting "
        "https://console.developers.google.com/billing/enable?project=783665453751 "
        "then retry."
    )
    register_ocr_provider(_provider(FakeVisionClient(error=RuntimeError(message))))
    try:
        response = TestClient(app).post(
            "/api/v1/attempts/G08/ocr",
            json={"image_reference": IMAGE_URI},
        )
    finally:
        reset_ocr_provider()

    # 502 는 Backend 가 일시 오류로 보고 재시도하는 코드다 — 503 fail-closed 여야 한다.
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "E_OCR_BILLING_REQUIRED"
    assert IMAGE_URI not in response.text


def test_raw_text_response_is_not_sent_to_analysis_automatically():
    from tests.test_ocr_analysis_handoff import ocr_to_analysis_request

    body = {
        "representation": "raw_text",
        "status": "PARTIAL",
        "problem": None,
        "solution_steps": [],
        "raw_text": "문제와 풀이의 OCR text",
        "raw_lines": [],
    }
    assert ocr_to_analysis_request(body) is None
