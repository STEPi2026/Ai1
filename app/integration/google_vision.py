"""Google Cloud Vision provider using Application Default Credentials.

Google Vision does not identify the semantic boundary between a printed
problem and a student's handwritten solution. This adapter therefore returns
raw OCR text/line preview data only; it never fabricates problem/step
segmentation, printed/handwritten labels, or LaTeX.

Activation is explicit: ``AI1_OCR_PROVIDER=google_vision``. The Google SDK
loads its service-account JSON through ``GOOGLE_APPLICATION_CREDENTIALS``.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import uuid
from pathlib import Path
from statistics import median
from typing import Any, Callable
from urllib.parse import urlsplit

from app.integration.google_vision_budget import (
    BILLING_UNIT_DOCUMENT_TEXT_DETECTION,
    CUMULATIVE_CALL_CAP,
    BudgetExceeded,
    BudgetLedgerCorrupt,
    BudgetUnverified,
    MonthlyQuotaPolicy,
    record_outcome as record_call_outcome,
    reserve_call_slot,
)
from app.integration.host_policy import split_allowed_hosts
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
)
from app.schemas import OcrBBox, OcrRawTextLine

_URL_QUERY_VALUE = re.compile(r"([?&][^=&#]+)=([^&#]+)")

# 협의서 §3.2 #7: AI1 의 Vision timeout 은 25초 이하(설정값도 마찬가지).
# Backend 가 OCR 요청 전체에 30초를 기다리므로 그 안에서 끝나야 한다.
_MAX_TIMEOUT_SECONDS = 25.0

# 협의서 §3.3 #10/#11: 이미지 링크는 Backend PUBLIC_BASE_URL 의 HTTPS 서명 URL.
# 개발 기간 주소 변동이 있으므로 호스트를 코드에 고정하지 않고 환경변수로
# 허용한다. 값 자체는 운영자가 Backend 공지로 설정(AI1이 임의로 정하지 않음).
ALLOWED_HOSTS_ENV = "AI1_OCR_ALLOWED_HOSTS"


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _vertices(box: Any) -> list[list[float]]:
    out: list[list[float]] = []
    for vertex in _field(box, "vertices", []) or []:
        x = _float_or_none(_field(vertex, "x", 0))
        y = _float_or_none(_field(vertex, "y", 0))
        if x is not None and y is not None:
            out.append([x, y])
    return out


def _safe_error(message: str, image_reference: str | None) -> str:
    """Remove the presigned image URL and query credentials from provider errors."""
    text = str(message or "")
    if image_reference:
        text = text.replace(image_reference, "<redacted-image-reference>")
        query = urlsplit(image_reference).query
        if query:
            text = text.replace(query, "<redacted-query>")
            for pair in query.split("&"):
                if not pair:
                    continue
                text = text.replace(pair, "<redacted-query-item>")
                _, _, value = pair.partition("=")
                if len(value) >= 6:
                    text = text.replace(value, "<redacted-query-value>")
    return text[:400]


def _word_text(word: Any) -> str:
    symbols = _field(word, "symbols", []) or []
    text = "".join(str(_field(symbol, "text", "") or "") for symbol in symbols)
    if text:
        return text
    # Compatibility path for test doubles/older JSON fixtures; official Vision
    # Word messages represent text via symbols[].text.
    return str(_field(word, "text", "") or "")


def _page_words(page: Any) -> list[dict]:
    words: list[dict] = []
    for block in _field(page, "blocks", []) or []:
        for paragraph in _field(block, "paragraphs", []) or []:
            for word in _field(paragraph, "words", []) or []:
                text = _word_text(word)
                if not text:
                    continue
                polygon = _vertices(_field(word, "bounding_box", _field(word, "boundingBox")))
                bbox = None
                if polygon:
                    xs = [point[0] for point in polygon]
                    ys = [point[1] for point in polygon]
                    bbox = OcrBBox(
                        x=min(xs), y=min(ys),
                        width=max(xs) - min(xs),
                        height=max(ys) - min(ys),
                    )
                words.append(
                    {
                        "text": text,
                        "bbox": bbox,
                        "confidence": _float_or_none(_field(word, "confidence")),
                    }
                )
    return words


def _raw_lines(page: Any, page_index: int, offset: int) -> list[OcrRawTextLine]:
    """Derive readable line previews from Vision word geometry (not semantics)."""
    words = _page_words(page)
    positioned = [word for word in words if word["bbox"] is not None]
    if not positioned:
        return [
            OcrRawTextLine(
                line_index=offset + index,
                page_index=page_index,
                text=word["text"],
                confidence=word["confidence"],
                derived=True,
            )
            for index, word in enumerate(words)
        ]

    heights = sorted(
        word["bbox"].height for word in positioned if word["bbox"].height > 0
    )
    tolerance = (median(heights) if heights else 0.0) * 0.6
    ordered = sorted(positioned, key=lambda word: (word["bbox"].y, word["bbox"].x))
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_top: float | None = None
    for word in ordered:
        top = word["bbox"].y
        if current_top is None or top - current_top <= tolerance:
            current.append(word)
            if current_top is None:
                current_top = top
        else:
            groups.append(current)
            current = [word]
            current_top = top
    if current:
        groups.append(current)

    lines: list[OcrRawTextLine] = []
    for index, group in enumerate(groups):
        group.sort(key=lambda word: word["bbox"].x)
        boxes = [word["bbox"] for word in group]
        left = min(box.x for box in boxes)
        top = min(box.y for box in boxes)
        right = max(box.x + box.width for box in boxes)
        bottom = max(box.y + box.height for box in boxes)
        confidences = [
            word["confidence"] for word in group
            if word["confidence"] is not None
        ]
        lines.append(
            OcrRawTextLine(
                line_index=offset + index,
                page_index=page_index,
                text=" ".join(word["text"] for word in group),
                confidence=min(confidences) if confidences else None,
                bbox=OcrBBox(
                    x=left, y=top, width=right - left, height=bottom - top
                ),
                bounding_polygon=[
                    [left, top], [right, top], [right, bottom], [left, bottom]
                ],
                derived=True,
            )
        )
    return lines


def _to_raw_result(response: Any, request: OcrProviderRequest) -> OcrRawTextResult:
    error = _field(response, "error")
    if error and _field(error, "message"):
        raise OcrProviderError(
            "Google Vision response error (code={}): {}".format(
                _field(error, "code"), _field(error, "message")
            )
        )
    annotation = _field(response, "full_text_annotation")
    if annotation is None:
        annotation = _field(response, "fullTextAnnotation")
    if annotation is None and _field(response, "pages") is not None:
        # ImageAnnotatorClient.document_text_detection returns TextAnnotation
        # directly; REST images:annotate wraps it in fullTextAnnotation.
        annotation = response
    if annotation is None:
        raise OcrProviderError(
            "Google Cloud Vision returned no fullTextAnnotation for DOCUMENT_TEXT_DETECTION."
        )

    pages = _field(annotation, "pages", []) or []
    lines: list[OcrRawTextLine] = []
    page_confidences: list[float] = []
    line_offset = 0
    for detected_index, page in enumerate(pages):
        page_index = request.page_index if request.page_index is not None else detected_index
        page_confidence = _float_or_none(_field(page, "confidence"))
        if page_confidence is not None:
            page_confidences.append(page_confidence)
        page_lines = _raw_lines(page, page_index, line_offset)
        lines.extend(page_lines)
        line_offset += len(page_lines)

    full_text = str(_field(annotation, "text", "") or "")
    if not full_text and lines:
        full_text = "\n".join(line.text for line in lines)
    line_confidences = [line.confidence for line in lines if line.confidence is not None]
    available_confidences = line_confidences or page_confidences
    warnings = [
        "Raw OCR preview only: problem/solution segmentation, printed/handwritten labels, and LaTeX are not inferred.",
        "Google Vision does not expose a stable model version in the response.",
    ]
    if not full_text.strip() and not lines:
        warnings.append("Google Vision returned no readable text.")
    return OcrRawTextResult(
        model_version="Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
        full_text=full_text,
        lines=lines,
        confidence=min(available_confidences) if available_confidences else None,
        page_index=request.page_index,
        warnings=warnings,
    )


class GoogleVisionProvider:
    """Google Vision OCR provider; emits raw OCR for student/Backend review."""

    def __init__(self, client, vision_module, *, language_hint="ko", timeout=8.0,
                 allowed_hosts: tuple[str, ...] | None = None,
                 reserve_slot: Callable[..., int] = reserve_call_slot,
                 call_cap: int = CUMULATIVE_CALL_CAP,
                 monthly_policy: MonthlyQuotaPolicy | None = None,
                 record_call: Callable[..., None] | None = record_call_outcome):
        self.client = client
        self.vision = vision_module
        self.language_hint = language_hint
        self.timeout = timeout
        self.allowed_hosts = allowed_hosts
        self.reserve_slot = reserve_slot
        self.call_cap = call_cap
        self.monthly_policy = monthly_policy
        self.record_call = record_call

    @classmethod
    def from_environment(cls, *, env=None, client_factory=None, vision_module=None,
                         reserve_slot: Callable[..., int] = reserve_call_slot,
                         call_cap: int = CUMULATIVE_CALL_CAP):
        env = os.environ if env is None else env
        credential_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not credential_path:
            raise OcrNotConfiguredError(
                "GOOGLE_APPLICATION_CREDENTIALS가 설정되지 않았습니다. "
                "서비스 계정 JSON 경로를 환경변수로 지정하십시오."
            )
        if not Path(credential_path).expanduser().is_file():
            raise OcrNotConfiguredError(
                "GOOGLE_APPLICATION_CREDENTIALS가 가리키는 파일이 없습니다. 경로를 확인하십시오."
            )
        # 월 무료 한도가 **확인되지 않았으면 provider 를 만들지 않는다** (fail-closed).
        # 값은 코드에 하드코딩하지 않고 운영자가 확인해 환경변수로 주입한다.
        try:
            monthly_policy = MonthlyQuotaPolicy.from_environment(
                env, required_unit=BILLING_UNIT_DOCUMENT_TEXT_DETECTION
            )
        except BudgetUnverified as exc:
            raise OcrNotConfiguredError(str(exc)) from None
        try:
            if vision_module is None:
                from google.cloud import vision as vision_module
            factory = client_factory or vision_module.ImageAnnotatorClient
            client = factory()
        except ImportError:
            raise OcrNotConfiguredError(
                "google-cloud-vision is not installed; install the declared project requirements."
            ) from None
        except Exception as exc:
            raise OcrAuthenticationError(
                "Google Cloud Vision client initialization failed ({}); verify the service-account JSON and IAM permissions."
                .format(type(exc).__name__)
            ) from None

        hint = env.get("GOOGLE_VISION_LANGUAGE_HINT", "ko").strip() or None
        try:
            timeout = float(env.get("GOOGLE_VISION_TIMEOUT_SECONDS", "8"))
        except (TypeError, ValueError):
            raise OcrNotConfiguredError(
                "GOOGLE_VISION_TIMEOUT_SECONDS must be a positive number."
            ) from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise OcrNotConfiguredError(
                "GOOGLE_VISION_TIMEOUT_SECONDS must be a positive finite number."
            )
        if timeout > _MAX_TIMEOUT_SECONDS:
            raise OcrNotConfiguredError(
                "GOOGLE_VISION_TIMEOUT_SECONDS must not exceed 25 seconds; "
                "the Backend waits at most 30 s for the whole OCR request "
                "(협의서 §3.2 #7)."
            )
        hosts, invalid_count = split_allowed_hosts(env.get(ALLOWED_HOSTS_ENV, ""))
        if invalid_count:
            # 스킴/경로/포트가 붙은 항목은 hostname exact match 와 어긋나
            # 모든 요청을 422 로 떨어뜨린다. 설정 오류로 보고 fail-closed 한다.
            raise OcrNotConfiguredError(
                "AI1_OCR_ALLOWED_HOSTS must contain bare host names only "
                "(no scheme, path, or port); invalid entries are rejected."
            )
        allowed_hosts = hosts or None
        return cls(
            client,
            vision_module,
            language_hint=hint,
            timeout=timeout,
            allowed_hosts=allowed_hosts,
            reserve_slot=reserve_slot,
            call_cap=call_cap,
            monthly_policy=monthly_policy,
        )

    def recognize(self, request: OcrProviderRequest) -> OcrRawTextResult:
        uri = request.image_reference.strip()
        parts = urlsplit(uri)
        # 협의서 §3.3 #10·§4.2: HTTPS 서명 URL만 받는다. gs:// 도 사용하지 않는다.
        # 검사는 budget 예약과 Vision 호출보다 선행 — 거부가 사용량을 소진하지 않는다.
        if parts.scheme != "https" or not parts.netloc:
            raise OcrUnsupportedImageError(
                "image_reference must be an HTTPS signed URL; "
                "other schemes (including gs://) are not accepted."
            )
        host = (parts.hostname or "").lower()
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise OcrUnsupportedImageError(
                "image_reference host is not configured as an allowed OCR host; "
                f"check {ALLOWED_HOSTS_ENV}."
            )

        image_ref = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
        run_id = "app-{}".format(uuid.uuid4().hex[:12])
        call_id = uuid.uuid4().hex
        try:
            # 누적 40회 + 월별 내부 상한을 **잠금 안에서 함께** 검사한다.
            self.reserve_slot(
                run_id,
                image_ref,
                cap=self.call_cap,
                policy=self.monthly_policy,
                call_id=call_id,
            )
        except BudgetExceeded as exc:
            raise OcrUsageLimitError(str(exc)) from exc
        except (BudgetUnverified, BudgetLedgerCorrupt) as exc:
            # 무료 한도 미확인 또는 원장 손상 — 결과를 만들지 않고 차단한다.
            raise OcrNotConfiguredError(str(exc)) from None
        except Exception as exc:
            raise OcrNotConfiguredError(
                "Google Vision free-tier usage guard is unavailable; request refused ({})."
                .format(type(exc).__name__)
            ) from None

        try:
            image = self.vision.Image(
                source=self.vision.ImageSource(image_uri=uri)
            )
            image_context = (
                self.vision.ImageContext(language_hints=[self.language_hint])
                if self.language_hint else None
            )
            # Disable SDK retries: Backend owns retry policy and every attempt
            # consumes a reserved free-tier unit.
            response = self.client.document_text_detection(
                image=image,
                image_context=image_context,
                timeout=self.timeout,
                retry=None,
            )
            result = _to_raw_result(response, request)
        except (OcrAuthenticationError, OcrBillingRequiredError, OcrApiDisabledError) as exc:
            self._record_outcome(call_id, "failed", exc)
            raise
        except OcrNotConfiguredError as exc:
            self._record_outcome(call_id, "failed", exc)
            raise
        except OcrProviderError as exc:
            self._record_outcome(call_id, "failed", exc)
            raise _classify_google_error(exc, uri) from None
        except Exception as exc:
            self._record_outcome(call_id, "failed", exc)
            raise _classify_google_error(exc, uri) from None
        else:
            self._record_outcome(call_id, "succeeded")
            return result

    def _record_outcome(self, call_id: str, outcome: str, error: BaseException | None = None):
        """결과를 원장에 남긴다. 실패해도 예약 차감은 이미 끝났으므로 요청은 계속한다."""
        if self.record_call is None:
            return
        try:
            self.record_call(
                call_id,
                outcome,
                reason=type(error).__name__ if error is not None else None,
            )
        except Exception:
            # 예약 기록이 과금 안전을 담당한다 — 결과 기록 실패로 응답을 바꾸지 않는다.
            pass


def _classify_google_error(error: BaseException, image_reference: str):
    message = _safe_error(str(error), image_reference)
    lower = message.lower()
    if any(marker in lower for marker in (
        "service_disabled", "service disabled", "has not been used in project", "api is disabled",
        "vision.googleapis.com has not been enabled",
    )):
        return OcrApiDisabledError(
            "Google Cloud Vision API is disabled for this GCP project; enable vision.googleapis.com."
        )
    if any(marker in lower for marker in (
        "billing account", "billing is disabled", "billing not enabled",
        "billing has not been enabled", "billing account is not enabled",
        # 실제 Google 원문: "403 This API method requires billing to be enabled."
        # marker 가 없으면 403 이 502 E_OCR_PROVIDER_ERROR 로 fall through 한다 (#10).
        "requires billing", "billing to be enabled", "enable billing on project",
    )):
        return OcrBillingRequiredError(
            "Google Cloud Vision requires an active billing account; check free-tier eligibility and billing setup."
        )
    if any(marker in lower for marker in (
        "resource_exhausted", "quota exceeded", "quota has been exceeded",
    )):
        return OcrUsageLimitError(
            "Google Cloud Vision quota was exhausted; stop requests and verify the billing account's remaining free units."
        )
    if any(marker in lower for marker in (
        "permission_denied", "permission denied", "unauthenticated",
        "invalid_grant", "defaultcredentials", "credential", "unauthorized",
    )):
        return OcrAuthenticationError(
            "Google Cloud Vision authentication/permission failed; verify GOOGLE_APPLICATION_CREDENTIALS and service-account IAM roles."
        )
    # 협의서 §4.6.3: 링크 다운로드 실패(만료·404 등)는 422 E_OCR_IMAGE_FETCH_FAILED.
    # Backend 가 만료·서명 오류·파일 없음을 404 로 답하므로 Vision 의
    # "Requested entity was not found" 가 대표 사례다.
    if any(marker in lower for marker in (
        "image fetch", "image download", "fetch failed",
        "requested entity was not found", "requested image was not found",
        "image not found", "not_found",
    )):
        return OcrImageFetchError(
            "The OCR provider could not download the image reference "
            "(expired or missing link); issue a new signed URL and a new attempt."
        )
    # 형식·MIME 문제는 422 E_OCR_UNSUPPORTED_IMAGE (협의서 §4.6.3).
    # Vision 은 손상/깨진 바이트에 "Bad image data." (INVALID_ARGUMENT)를 준다.
    if any(marker in lower for marker in (
        "unsupported image", "image format", "invalid image", "corrupt image",
        "bad image data", "bad image", "unsupported mime", "unsupported file",
    )):
        return OcrUnsupportedImageError(
            "The OCR provider rejected the image as unsupported (format/MIME)."
        )
    return OcrProviderError(
        "Google Cloud Vision request failed ({}): {}".format(
            type(error).__name__, message[:300]
        )
    )
