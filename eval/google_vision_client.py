"""Google Cloud Vision `DOCUMENT_TEXT_DETECTION` 호출 래퍼 (데모 평가용).

**AI¹ `/ocr` 경로와 무관하다.** OcrResult 로 추측 변환하지 않고, raw text OCR
결과만 **평가용 중간 구조**로 남긴다.

**새 dependency 없음** — 표준 라이브러리만 쓴다. credential 은 다음 둘 중 하나:
  1. 환경변수 ``GOOGLE_VISION_ACCESS_TOKEN`` (외부에서 미리 발급받아 주입)
  2. **ADC** — `gcloud auth application-default print-access-token` (로컬 ADC 저장소)

**secret 은 어떤 경우에도 파일·로그·보고서에 남지 않는다.** 토큰은 요청 헤더에만
쓰고, 오류 메시지에서는 제거한다.

**과금** — Cloud Vision 은 **기능별** 첫 1,000 units/month/**billing account**
가 무료다. 따라서:
  - **OCR 기능 하나만** 호출한다 (`DOCUMENT_TEXT_DETECTION`). `TEXT_DETECTION` 을
    같이 부르면 **같은 이미지에서 무료 unit 을 두 번** 소비한다.
  - 이 모듈은 `TEXT_DETECTION` 등 다른 기능을 **거부**한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.integration.google_vision_budget import (  # noqa: E402
    MonthlyQuotaPolicy,
    BudgetExceeded,
    BudgetLedgerCorrupt,
    BudgetUnverified,
    check_budget,
    check_cumulative_budget,
    month_usage,
    record_outcome,
    reserve_call_slot,
    status,
    used_calls,
)

# These shared budget helpers are intentionally public through this module:
# the standalone probe and its existing tests use `client.BudgetExceeded`.
__all__ = [
    "MonthlyQuotaPolicy",
    "BudgetExceeded",
    "BudgetLedgerCorrupt",
    "BudgetUnverified",
    "check_budget",
    "check_cumulative_budget",
    "month_usage",
    "record_outcome",
    "reserve_call_slot",
    "status",
    "used_calls",
]

VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

#: 유일하게 쓸 OCR 기능. 다른 기능은 **무료 unit 을 중복 소비**하므로 금지.
FEATURE_TYPE = "DOCUMENT_TEXT_DETECTION"

#: 허용하는 이미지 소스 스킴.
ALLOWED_URI_SCHEMES = ("gs://", "https://")

#: ADC 토큰을 얻을 때 쓰는 gcloud 명령.
GCLOUD_ADC_COMMAND = ["gcloud", "auth", "application-default", "print-access-token"]

#: 토큰 주입용 환경변수 이름.
ACCESS_TOKEN_ENV = "GOOGLE_VISION_ACCESS_TOKEN"

#: 응답을 저장할 기본 디렉터리 (gitignore 대상).
EVAL_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = EVAL_ROOT / "_local"
GOOGLE_DIR = LOCAL_DIR / "google_vision"
RAW_DIR = GOOGLE_DIR / "raw"
RESULTS_DIR = GOOGLE_DIR / "results"

#: 파일명에 남길 수 있는 문자.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class VisionError(RuntimeError):
    """Google Vision 호출 실패 (HTTP·네트워크·자격증명)."""


class VisionResponseError(RuntimeError):
    """응답이 계약에 맞지 않는다 (파싱 불가)."""


def slug(value: str) -> str:
    """파일명에 안전하게 쓸 수 있는 slug."""
    text = _UNSAFE.sub("_", str(value or "")).strip("._-")
    if len(text) > 64:
        text = text[:64].strip("._-")
    return text or "unnamed"


def uri_ref(uri: str) -> str:
    """이미지 URI 의 **비가역 참조**.

    presigned URL 은 **만료되는 bearer 정보**라 쿼리(서명·토큰)까지
    기록하면 안 된다. URI 자체도 남기지 않고, **안정적이되 되돌릴 수 없는**
    참조만 남긴다 (같은 이미지면 같은 참조).
    """
    digest = hashlib.sha256((uri or "").encode("utf-8")).hexdigest()
    return "sha256:" + digest[:16]


def redact_payload(payload: Any, token: str | None = None, uris=()) -> Any:
    """중첩 구조 안의 **문자열 값**을 치환한다.

    Vision 응답은 `context.uri` 로 **요청한 이미지 URI 를 반사**할 수 있다.
    그래서 저장 전에 구조를 훑어 URI·서명·토큰을 제거한다.
    """
    if isinstance(payload, dict):
        return {
            key: redact_payload(value, token, uris)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(item, token, uris) for item in payload]
    if isinstance(payload, str):
        return redact(payload, token, uris)
    return payload


def redact(text: str, token: str | None = None, uris=()) -> str:
    """토큰과 presigned URL 을 치환한다.

    서명·토큰이 **쿼리 일부만** 노출되어도 막는다 — 서버 오류가 서명 쿼리만
    반사하는 경우가 있기 때문이다.
    """
    if not text:
        return text
    out = text
    for uri in uris or ():
        if not uri:
            continue
        out = out.replace(uri, "<redacted-image-uri>")
        if "?" in uri:
            tail = uri.split("?", 1)[1]
            if tail:
                out = out.replace(tail, "<redacted-image-uri>")
            for param in tail.split("&"):
                if not param:
                    continue
                out = out.replace(param, "<redacted-image-uri>")
                _, _, value = param.partition("=")
                if len(value) >= 8:
                    out = out.replace(value, "<redacted-image-uri>")
    if token:
        out = out.replace(token, "<redacted-access-token>")
    return out


# ---------------------------------------------------------------------------
# 자격증명
# ---------------------------------------------------------------------------

def resolve_access_token(env: dict | None = None, runner=None) -> str:
    """ADC(또는 주입된 access token) 으로 Bearer 토큰을 얻는다.

    1. ``GOOGLE_VISION_ACCESS_TOKEN`` 환경변수가 있으면 그 값을 쓴다.
    2. 없으면 **ADC** — `gcloud auth application-default print-access-token`.

    **토큰 값은 반환만 하고 어디에도 기록하지 않는다.**
    """
    env = os.environ if env is None else env
    injected = env.get(ACCESS_TOKEN_ENV)
    if injected and injected.strip():
        return injected.strip()

    runner = runner or _default_runner
    try:
        proc = runner(GCLOUD_ADC_COMMAND)
    except FileNotFoundError:
        raise VisionError(
            "자격증명을 찾을 수 없다. `gcloud auth application-default login` 을 먼저 "
            "실행하거나, {} 환경변수에 access token 을 주입한다.".format(ACCESS_TOKEN_ENV)
        ) from None
    except Exception as exc:
        raise VisionError("ADC 조회 실패: {}".format(type(exc).__name__)) from None

    if proc.returncode != 0:
        raise VisionError(
            "ADC 조회 실패 (exit {}). `gcloud auth application-default login` 이 "
            "필요할 수 있다.".format(proc.returncode)
        )
    token = (proc.stdout or "").strip()
    if not token:
        raise VisionError("ADC 에서 access token 을 얻지 못했다.")
    return token


def _default_runner(command: list[str]):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


# ---------------------------------------------------------------------------
# 요청 본문
# ---------------------------------------------------------------------------

def build_body(
    image_uri: str,
    *,
    language_hint: str | None = None,
    feature_type: str = FEATURE_TYPE,
) -> dict:
    """`images:annotate` 요청 본문을 만든다.

    **OCR 기능은 하나만** 넣는다. `languageHints` 는 `imageContext` 에 넣는다
    (`DOCUMENT_TEXT_DETECTION` 의 언어 힌트 위치).
    """
    if feature_type != FEATURE_TYPE:
        raise ValueError(
            "이 데모는 {} 만 쓴다 — 다른 OCR 기능은 같은 이미지의 무료 unit 을 "
            "추가로 소비한다.".format(FEATURE_TYPE)
        )
    if not isinstance(image_uri, str) or not image_uri:
        raise ValueError("image_uri 가 없다")
    if not image_uri.startswith(ALLOWED_URI_SCHEMES):
        raise ValueError(
            "image_uri 는 {} 중 하나로 시작해야 한다: {!r}".format(
                " 또는 ".join(ALLOWED_URI_SCHEMES), image_uri[:40]
            )
        )

    request: dict[str, Any] = {
        "image": {"source": {"imageUri": image_uri}},
        "features": [{"type": feature_type}],
    }
    if language_hint:
        request["imageContext"] = {"languageHints": [language_hint]}
    return {"requests": [request]}


# ---------------------------------------------------------------------------
# 호출
# ---------------------------------------------------------------------------

def call_annotate(
    body: dict,
    *,
    token: str,
    timeout: float = 60.0,
    opener=None,
) -> tuple[dict, float]:
    """`images:annotate` 를 **한 번** 호출한다. 반환값은 ``(응답, 지연초)``."""
    request = urllib.request.Request(
        VISION_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = opener or urllib.request.urlopen
    started = time.perf_counter()
    try:
        with opener(request, timeout=timeout) as response:
            elapsed = time.perf_counter() - started
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        raise VisionError(
            "Google Vision HTTP {}: {}".format(exc.code, redact(detail, token))
        ) from None
    except urllib.error.URLError as exc:
        elapsed = time.perf_counter() - started
        raise VisionError(
            "Google Vision 네트워크 오류: {}".format(redact(str(exc.reason), token))
        ) from None
    except Exception as exc:
        elapsed = time.perf_counter() - started
        raise VisionError(
            "Google Vision 호출 실패: {}".format(redact(type(exc).__name__, token))
        ) from None

    try:
        return json.loads(raw), elapsed
    except json.JSONDecodeError as exc:
        raise VisionError(
            "Google Vision 응답이 JSON 이 아니다: {}".format(redact(str(exc), token))
        ) from None
