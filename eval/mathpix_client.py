"""Mathpix `v3/text` 호출 래퍼 (평가 하네스용).

**저장소에 아무것도 남기지 않는다.**
- 자격증명은 **환경변수** 에서만 읽는다 (`MATHPIX_APP_ID` / `MATHPIX_APP_KEY`).
  키를 코드·로그·리포트에 쓰지 않는다.
- 원본 응답은 `eval/_local/responses/` 아래에만 쓴다. 그 디렉터리는 gitignore 된다.
- **기본 요청은 항상 `metadata.improve_mathpix=false` 를 보낸다.** — 데이터 품질 개선
  사용과 OCR 데이터 보존을 동시에 막는다. 단 **account-level 고정값이 있으면 그 값이
  요청 설정을 덮어쓴다**(콘솔/지원팀 확인 필요).
  — [Privacy](https://docs.mathpix.com/concepts/privacy) ·
  [Data Retention](https://docs.mathpix.com/concepts/data-retention)

사용
    $env:MATHPIX_APP_ID = "..."      # PowerShell
    $env:MATHPIX_APP_KEY = "..."
    python eval\\mathpix_client.py --image-ref "https://.../page1.jpg" --out demo
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_URL = "https://api.mathpix.com/v3/text"

#: 계약 검증에 필요한 요청 옵션 (결정안 §3.2).
DEFAULT_FORMATS = ["text", "data", "latex_styled"]

EVAL_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = EVAL_ROOT / "_local"
RESPONSES_DIR = LOCAL_DIR / "responses"


class MathpixError(RuntimeError):
    """벤더 호출 실패 (HTTP 오류·네트워크 오류)."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def credentials() -> tuple[str, str]:
    """환경변수에서 자격증명을 읽는다. 없으면 명확히 실패한다."""
    app_id = os.environ.get("MATHPIX_APP_ID")
    app_key = os.environ.get("MATHPIX_APP_KEY")
    missing = [
        name
        for name, value in (("MATHPIX_APP_ID", app_id), ("MATHPIX_APP_KEY", app_key))
        if not value
    ]
    if missing:
        raise MathpixError(
            "자격증명 환경변수가 없습니다: {}. 키를 저장소에 넣지 말고 환경변수로만 설정하세요.".format(
                ", ".join(missing)
            )
        )
    return app_id, app_key


def build_body(
    image_reference: str,
    *,
    formats: list[str] | None = None,
    confidence_threshold: float | None = None,
    include_line_data: bool = True,
    include_lines_json: bool = True,
    improve_mathpix: bool = False,
) -> dict:
    """`v3/text` 요청 본문을 만든다.

    ``image_reference`` 는 Backend 가 발급한 **presigned HTTPS URL** 이나 base64 다.
    여기서는 URL 만 쓴다(AI¹ 경유 base64 는 대역폭 낭비 — 결정안 Q22).

    **``metadata.improve_mathpix`` 는 언제나 `false` 다.**

    `false` 는 두 가지를 동시에 막는다.
      1. **데이터 품질 개선 사용** — Mathpix QA 팀이 그 데이터를 볼 수 없다
      2. **OCR 데이터 보존** — 이미지 엔드포인트(`/v3/text` 등)는 입력 이미지를
         디스크에 남기지 않고 OCR 결과도 캐시하지 않는다. `/v3/ocr-results` 로
         조회할 수 없다 → **결과는 그 API 응답 안에만 있음**

    플래그를 **아예 생략하면 벤더 기본값(`true`) 이 적용**되므로 생략 경로를 두지
    않는다. — [Privacy](https://docs.mathpix.com/concepts/privacy) ·
    [Data Retention](https://docs.mathpix.com/concepts/data-retention)

    **`improve_mathpix=true` 는 eval 하네스에서 보낼 수 없다.** 참을 넣으면
    `ValueError` 로 거절한다 — 학생 과제 사진이 벤더 품질 개선에 사용될 수 있기
    때문이다. opt-out 을 **해제**하는 유일한 허용 경로는 없다.

    **단, account-level 로 고정된 값이 있으면 그 값이 요청 설정을 덮어쓴다.**
    요청 설정만으로는 그 고정(pin) 여부를 확인할 수 없고, **OCR 응답에도 유효 설정을
    확인하는 근거가 없다** → **콘솔 또는 Mathpix 지원팀으로 확인**해야 한다.
    (이 확인을 응답으로 대체하지 않는다)

    참고: **청구·감사용 요청 메타데이터**(status·timestamp·page count·error code)는
    `improve_mathpix` 값과 **무관하게** 보존된다.
    """
    if bool(improve_mathpix):
        raise ValueError(
            "eval 하네스에서는 `metadata.improve_mathpix=true` 를 보낼 수 없다 — "
            "학생 과제 사진이 벤더 품질 개선에 사용될 수 있다. "
            "account-level 고정값 확인은 콘솔 또는 Mathpix 지원팀으로 한다."
        )
    body: dict[str, Any] = {
        "src": image_reference,
        "formats": list(formats or DEFAULT_FORMATS),
        "include_line_data": include_line_data,
        "include_lines_json": include_lines_json,
        "include_detected_alphabets": True,
    }
    if confidence_threshold is not None:
        body["confidence_threshold"] = confidence_threshold
    body.setdefault("metadata", {})["improve_mathpix"] = False
    return body


def call_text(
    image_reference: str,
    *,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[dict, float]:
    """`v3/text` 를 한 번 호출한다. 반환값은 ``(응답, 지연초)``."""
    app_id, app_key = credentials()
    payload = body if body is not None else build_body(image_reference)
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "app_id": app_id,
            "app_key": app_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            elapsed = time.perf_counter() - started
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        raw = exc.read().decode("utf-8", errors="replace")
        raise MathpixError(
            "Mathpix HTTP {}: {}".format(exc.code, raw[:300]),
            status=exc.code,
            payload=_safe_json(raw),
        ) from None
    except urllib.error.URLError as exc:
        elapsed = time.perf_counter() - started
        raise MathpixError("Mathpix 네트워크 오류: {}".format(exc.reason)) from None
    return _safe_json(raw), elapsed


def save_raw(payload: dict, name: str) -> Path:
    """원본 응답을 로컬 전용 경로에 저장한다 (gitignore 대상)."""
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    path = RESPONSES_DIR / "{}.json".format(name)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def main() -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(description="Mathpix v3/text 1회 호출")
    parser.add_argument("--image-ref", required=True, help="Backend presigned HTTPS URL")
    parser.add_argument("--out", default="probe", help="로컬 저장 파일 이름(접두)")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        payload, elapsed = call_text(args.image_ref, timeout=args.timeout)
    except MathpixError as exc:
        print("호출 실패: {}".format(exc))
        return 1

    path = save_raw(payload, args.out)
    print("지연: {:.3f}s".format(elapsed))
    print("저장: {}".format(path))
    print("top-level keys: {}".format(sorted(payload.keys()) if isinstance(payload, dict) else type(payload)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
