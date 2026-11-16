"""PHASE 9-8A — OCR API 경계 및 provider 인터페이스.

역할
    사진에서 문제/풀이를 인식하는 OCR 처리를 **교체 가능한 provider** 뒤에 두고,
    AI¹ 은 어떤 OCR 엔진을 쓰는지 알지 않는다. 테스트는 결정적인 fake provider 로
    검증한다.

책임 경계
    - **이미지 원본 저장은 Backend 책임**이다. AI¹ 은 Backend 가 준
      ``image_reference`` 를 입력으로 받기만 하고 사진 bytes 를 직접 받지 않는다
      (multipart 업로드 경로를 중복 구현하지 않는다).
    - OCR 결과만으로 Knowledge State 를 갱신하지 않고, AI② 를 직접 호출하지 않는다.
      분석은 기존 ``/analyze`` 경로의 몫이다.
    - **OCR confidence 와 AI¹ 수학 분석 confidence 는 별개 값**이다. OCR 신뢰도는
      문자 인식에 대한 신뢰도이고, 분석 ``confidence`` 는 분류 신뢰도다.

상태 파생 규칙 (provider 가 아니라 **경계**가 계산한다)
    ``UNREADABLE``  읽을 수 있는 내용이 하나도 없음 (region/step 이 없거나 전부 unreadable)
    ``PARTIAL``     일부만 읽음 (unreadable 항목이 하나라도 있음)
    ``COMPLETE``    전부 읽음

    provider 가 status 를 직접 말하지 않아도 응답 상태가 내용과 어긋나지 않는다.

provider 선택
    현재 Google Cloud Vision provider가 선택적으로 연결되어 있다. 자동 활성화하지
    않고 ``AI1_OCR_PROVIDER=google_vision`` 에서만 선택한다. Vision은 문제/풀이
    의미를 분리하지 않으므로 raw-text preview만 반환한다. provider 미설정 시 경계는
    503 을 돌려주고 결과를 꾸며내지 않는다.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import OcrProblem, OcrRawTextLine, OcrRegion, OcrStatus, OcrStep

__all__ = [
    "OcrProvider",
    "OcrProviderRequest",
    "OcrResult",
    "OcrRawTextResult",
    "OcrNotConfiguredError",
    "OcrAuthenticationError",
    "OcrBillingRequiredError",
    "OcrApiDisabledError",
    "OcrUsageLimitError",
    "OcrImageFetchError",
    "OcrUnsupportedImageError",
    "OcrProviderError",
    "derive_ocr_confidence",
    "derive_ocr_result_id",
    "derive_ocr_status",
    "get_ocr_provider",
    "register_ocr_provider",
    "require_ocr_provider",
    "reset_ocr_provider",
]


class OcrNotConfiguredError(RuntimeError):
    """OCR provider 가 설정되지 않았다 — 결과를 만들 수 없다."""

    code = "E_OCR_NOT_CONFIGURED"


class OcrAuthenticationError(OcrNotConfiguredError):
    """OCR provider credentials are missing, invalid, or lack access."""

    code = "E_OCR_AUTH_FAILED"


class OcrBillingRequiredError(OcrNotConfiguredError):
    """Google Cloud billing is not enabled for the Vision project."""

    code = "E_OCR_BILLING_REQUIRED"


class OcrProviderError(RuntimeError):
    """OCR provider 호출이 실패했다 (네트워크·인증·벤더 오류 등)."""

    code = "E_OCR_PROVIDER_ERROR"

    http_status = 502


class OcrApiDisabledError(OcrProviderError):
    """The configured OCR API is disabled in the provider project."""

    code = "E_OCR_API_DISABLED"
    http_status = 503


class OcrUsageLimitError(OcrNotConfiguredError):
    """The local free-tier demo call budget has been exhausted.

    협의서 §4.6.3: 로컬 가드·quota 상한은 429 로 응답한다(재시도 대상 아님).
    """

    code = "E_OCR_USAGE_LIMIT_REACHED"
    http_status = 429


class OcrImageFetchError(OcrProviderError):
    """Backend 가 준 이미지 링크를 다운로드하지 못했다 (만료·404 등)."""

    code = "E_OCR_IMAGE_FETCH_FAILED"
    http_status = 422


class OcrUnsupportedImageError(OcrProviderError):
    """이미지 참조(스킴·호스트) 또는 형식이 지원되지 않는다."""

    code = "E_OCR_UNSUPPORTED_IMAGE"
    http_status = 422


class OcrProviderRequest(BaseModel):
    """provider 에 넘기는 입력.

    ``image_reference`` 의 해석(URL 인지 저장 경로인지)은 provider 의 몫이다 —
    AI¹ 은 형식을 추측하지 않고 그대로 넘긴다.
    """

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(..., description="Backend 가 정한 제출 식별자")
    image_reference: str = Field(..., min_length=1, description="Backend 가 보관한 이미지 참조")
    page_index: int | None = Field(None, ge=0, description="다중 페이지 중 몇 번째 페이지인지")


class OcrResult(BaseModel):
    """provider 가 돌려주는 OCR 결과 (원본).

    ``status`` 는 넣지 않는다 — 경계가 내용에서 파생한다(문서 상단 규칙).
    """

    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(..., min_length=1, description="OCR 모델 버전")
    problem: OcrProblem
    regions: list[OcrRegion] = Field(default_factory=list)
    steps: list[OcrStep] = Field(default_factory=list)
    ocr_result_id: str | None = Field(None, description="없으면 경계가 attempt_id 로 파생한다")
    page_index: int | None = None
    warnings: list[str] = Field(default_factory=list)


class OcrRawTextResult(BaseModel):
    """OCR text and geometry without guessing problem/step semantics.

    Used when a provider (for example Cloud Vision) detects text but does not
    identify which lines are the printed problem versus handwritten solution.
    The response is deliberately marked PARTIAL for human review.
    """

    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(..., min_length=1, description="Provider/model family")
    full_text: str = Field("", description="Provider's complete recognized text")
    lines: list[OcrRawTextLine] = Field(default_factory=list)
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    ocr_result_id: str | None = None
    page_index: int | None = Field(None, ge=0)
    warnings: list[str] = Field(default_factory=list)


class OcrProvider(ABC):
    """OCR provider 인터페이스.

    실제 구현은 벤더/자체 엔진을 감싼 어댑터가 된다. 어떤 구현이든
    ``recognize()`` 는 구조화된 :class:`OcrResult` 또는 의미 분리 전
    :class:`OcrRawTextResult` 를 돌려줄 수 있다. 읽을 수 없는 내용을 복원하거나
    문제/풀이 의미·LaTeX를 추측하지 않는다.
    """

    @abstractmethod
    def recognize(self, request: OcrProviderRequest) -> OcrResult | OcrRawTextResult:
        """이미지 한 장을 인식해 결과를 돌려준다.

        실패하면 :class:`OcrProviderError` 를 던진다.
        """


#: 등록된 provider. 기본값은 없다 — 미설정 상태가 정상이다(503).
_ocr_provider: OcrProvider | None = None


def register_ocr_provider(provider: OcrProvider | None) -> None:
    """provider 를 등록한다 (``None`` 이면 해제)."""
    global _ocr_provider
    _ocr_provider = provider


def get_ocr_provider() -> OcrProvider | None:
    """등록된 provider. 없으면 ``None``."""
    return _ocr_provider


def require_ocr_provider() -> OcrProvider:
    """provider 를 요구한다. 명시 설정이 없으면 503 으로 fail-closed 한다."""
    provider = _ocr_provider
    selected = os.environ.get("AI1_OCR_PROVIDER", "").strip().lower()
    if provider is None and selected == "google_vision":
        # Lazy import keeps the Google SDK and credentials out of unconfigured runs.
        from app.integration.google_vision import GoogleVisionProvider

        provider = GoogleVisionProvider.from_environment()
        register_ocr_provider(provider)
    elif provider is None and selected:
        raise OcrNotConfiguredError(
            "지원하지 않는 OCR provider 설정입니다: {!r}. "
            "현재 지원값은 'google_vision' 입니다.".format(selected)
        )
    if provider is None:
        raise OcrNotConfiguredError(
            "OCR provider 가 설정되지 않았습니다. "
            "등록 전에는 OCR 결과를 만들 수 없습니다 (503)."
        )
    return provider


def reset_ocr_provider() -> None:
    """provider 해제 (테스트 격리용)."""
    register_ocr_provider(None)


def _has_ocr_text(region: OcrRegion) -> bool:
    """region 에 **읽힌 텍스트**가 있는가.

    bbox 만 있는 region 은 영역 메타데이터이지 읽힌 내용이 아니다.
    """
    return bool(
        (region.text and region.text.strip()) or (region.latex_text and region.latex_text.strip())
    )


def derive_ocr_status(result: OcrResult | OcrRawTextResult) -> OcrStatus:
    """OCR 결과의 상태를 **내용 전체**에서 파생한다.

    문제(problem)·영역(regions)·풀이 단계(solution_steps) 를 모두 본다.

      ``COMPLETE``    읽을 수 있는 문제가 있고, 읽을 수 있는 풀이 단계가 하나 이상
                      있으며, unreadable 항목이 하나도 없다.
      ``PARTIAL``     읽을 수 있는 OCR 내용이 있지만 문제/풀이가 없거나 unreadable
                      항목이 있다.
      ``UNREADABLE``  읽을 수 있는 문제도, 읽을 수 있는 풀이 단계도, **읽힌 텍스트가
                      있는** region 도 하나도 없다.

    **regions 는 선택 메타데이터**다. provider 가 영역 정보를 주지 않아도(빈 목록이어도)
    그것만으로 PARTIAL/UNREADABLE 이 되지 않는다. 또 bbox 만 있는 region 은
    "읽힌 텍스트"가 아니므로 readable content 로 세지 않는다.
    """
    if isinstance(result, OcrRawTextResult):
        # Raw OCR has not semantically identified the problem and solution.
        # It is reviewable partial data, never an invented structured success.
        return "PARTIAL" if result.full_text.strip() or result.lines else "UNREADABLE"

    problem_readable = not result.problem.unreadable
    steps_readable = any(not step.unreadable for step in result.steps)
    regions_readable = any(
        not region.unreadable and _has_ocr_text(region) for region in result.regions
    )
    has_unreadable = (
        result.problem.unreadable
        or any(region.unreadable for region in result.regions)
        or any(step.unreadable for step in result.steps)
    )

    if not problem_readable and not steps_readable and not regions_readable:
        return "UNREADABLE"
    if problem_readable and steps_readable and not has_unreadable:
        return "COMPLETE"
    return "PARTIAL"


def derive_ocr_result_id(
    attempt_id: str, result: OcrResult | OcrRawTextResult, page_index: int | None
) -> str:
    """OCR 결과 식별자를 정한다.

    - provider 가 ``ocr_result_id`` 를 줬으면 그대로 쓴다.
    - 없고 ``page_index`` 가 있으면 **페이지별 ID** 를 만들어 중복을 막는다
      (``{attempt_id}#ocr-p{page_index}``). 페이지별 식별 규칙을 확정하기 전까지
      같은 attempt 의 여러 페이지가 같은 ID 를 갖게 되어서는 안 된다.
    - 둘 다 없으면 ``{attempt_id}#ocr`` (단일 페이지 기본값).
    """
    if result.ocr_result_id:
        return result.ocr_result_id
    if page_index is not None:
        return f"{attempt_id}#ocr-p{page_index}"
    return f"{attempt_id}#ocr"


def derive_ocr_confidence(result: OcrResult | OcrRawTextResult) -> float | None:
    """전체 OCR 신뢰도 — 문제·region·단계 신뢰도 중 최솟값.

    일부가 잘 못 읽혔으면 전체를 신뢰하면 안 되므로 최솟값을 쓴다.
    """
    if isinstance(result, OcrRawTextResult):
        values = [line.confidence for line in result.lines if line.confidence is not None]
        return min(values) if values else result.confidence

    values = [result.problem.confidence]
    values.extend(region.confidence for region in result.regions)
    values.extend(step.confidence for step in result.steps)
    return min(values) if values else 0.0
