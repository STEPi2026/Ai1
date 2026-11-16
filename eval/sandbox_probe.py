"""샌드박스 1회 호출 계약 검사기.

**목적** — 실제 Mathpix 응답을 보고 다음을 확정한다(결정안 §8-1).
  1. `v3/text` 실제 응답 형식
  2. `include_line_data` / `include_lines_json` 출력 모양
  3. **confidence 누락이 실제로 있는가** (M12 결정의 전제)
  4. `error_id` / `conversion_output` 동작 (→ `unreadable` 신호)

**저장소에는 아무것도 남기지 않는다** — 원본 응답은 `eval/_local/` 아래에만 쓴다.
자격증명은 환경변수에서만 읽는다.

사용
    $env:MATHPIX_APP_ID = "..."
    $env:MATHPIX_APP_KEY = "..."
    python eval\\sandbox_probe.py --image-ref "https://presigned-url" --out probe01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mathpix_client as client  # noqa: E402
import mathpix_adapter as adapter  # noqa: E402


def inspect_payload(payload: dict) -> dict:
    """응답에서 계약 확인에 필요한 사실만 뽑는다 (원본은 그대로 저장)."""
    lines = payload.get("line_data") or []
    lines_json = payload.get("lines_json") or {}

    confidences = [line.get("confidence") for line in lines]
    missing = [c for c in confidences if c is None]

    error_ids = {}
    for line in lines:
        error_id = line.get("error_id")
        if error_id:
            error_ids[error_id] = error_ids.get(error_id, 0) + 1

    region_keys = sorted(
        {
            key
            for line in lines_json.get("lines", []) or []
            for key in (line.get("region") or {})
        }
    )

    return {
        "top_level_keys": sorted(payload.keys()),
        "has_version": bool(payload.get("version")),
        "version": payload.get("version"),
        "has_confidence": "confidence" in payload,
        "top_confidence": payload.get("confidence"),
        "is_printed": payload.get("is_printed"),
        "is_handwritten": payload.get("is_handwritten"),
        "line_count": len(lines),
        "line_keys": sorted({key for line in lines for key in line}),
        "line_confidence_missing": len(missing),
        "line_confidence_missing_ids": [
            line.get("id") for line in lines if line.get("confidence") is None
        ][:10],
        "error_id_histogram": error_ids,
        "conversion_output_false_count": sum(
            1 for line in lines if line.get("conversion_output") is False
        ),
        "lines_json_present": bool(lines_json),
        "lines_json_keys": sorted(lines_json.keys()),
        "lines_json_region_keys": region_keys,
        "lines_json_line_count": len(lines_json.get("lines", []) or []),
        "detected_alphabets": payload.get("detected_alphabets"),
    }


def main() -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="Mathpix v3/text 계약 검사")
    parser.add_argument("--image-ref", required=True, help="Backend presigned HTTPS URL")
    parser.add_argument("--out", default="probe", help="로컬 저장 이름(접두)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="confidence_threshold 를 보내고 싶을 때 (기본 미지정)",
    )
    args = parser.parse_args()

    try:
        body = client.build_body(
            args.image_ref,
            confidence_threshold=args.threshold,
            improve_mathpix=False,
        )
        payload, elapsed = client.call_text(args.image_ref, body=body, timeout=args.timeout)
    except client.MathpixError as exc:
        print("샌드박스 호출 실패: {}".format(exc))
        return 1

    path = client.save_raw(payload, args.out)
    summary = inspect_payload(payload)
    summary["_elapsed_seconds"] = round(elapsed, 4)
    summary["_local_path"] = str(path)

    # 변환 프로토타입을 응답에 대어 시험한다 (M1/M2/M5 는 여전히 미결).
    try:
        draft = adapter.draft_from_response(payload)
        summary["_draft_problem_latex"] = draft["problem"]["latex_text"]
        summary["_draft_step_count"] = len(draft["steps"])
        summary["_draft_unreadable_steps"] = sum(
            1 for step in draft["steps"] if step["unreadable"]
        )
        summary["_draft_missing_confidence"] = len(draft["missing_confidence"])
        summary["_draft_convertible_to_ocr_result"] = not draft["missing_confidence"]
    except Exception as exc:  # 변환은 아직 확정 계약이 아니다 — 실패해도 기록한다
        summary["_draft_error"] = "{}: {}".format(type(exc).__name__, exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    print("원본 응답 저장: {}".format(path))
    print("리포트 저장: {}".format(client.save_raw(summary, args.out + "_summary")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
