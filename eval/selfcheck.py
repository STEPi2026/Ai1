"""하네스 자체 검증 — Mathpix **문서에 게시된 예시 응답** 으로 변환 프로토타입을 확인한다.

이것은 **실측이 아니다.** 실제 벤더 호출·실제 학생 이미지가 아니라
Mathpix 공식 문서에 실린 예시 JSON(문서 형식 확인용)이다.
정확도·지연·비용 어떤 값도 여기서 나오지 않는다.

용도
  - `mathpix_adapter` 의 MMD 분리와 줄→단계 파생이 문서 예시 모양에 붙는가
  - `measure.py` 가 라벨/결과 형식을 실제로 읽고 지표를 계산하는가
  - confidence 누락이 있으면 `to_ocr_result` 가 0.0 대체 없이 refuses 하는가
"""
import json
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent))

import mathpix_adapter as adapter  # noqa: E402

# --- Mathpix 공식 문서(post-v3-text / image-results)의 게시된 예시 ---
DOC_EXAMPLE_LINE_DATA = {
    "auto_rotate_confidence": 0,
    "auto_rotate_degrees": 0,
    "confidence": 0.6602140300674364,
    "confidence_rate": 0.9949494895122919,
    "image_height": 733,
    "image_width": 932,
    "is_handwritten": False,
    "is_printed": True,
    "line_data": [
        {
            "after_hyphen": False,
            "cnt": [[0, 77], [0, 32], [867, 54], [865, 98]],
            "confidence": 0.6602140300674364,
            "confidence_rate": 0.9949494895122919,
            "conversion_output": True,
            "id": "1177a9c84cba4101ad53c10053113218",
            "included": True,
            "is_handwritten": False,
            "is_printed": True,
            "text": "Equivalent resistance between points \\( A \\) \\& \\( B \\) in the adjacent circuit is -",
            "type": "text",
        },
        {
            "cnt": [[6, 243], [652, 243], [652, 683], [6, 683]],
            "conversion_output": False,
            "error_id": "image_not_supported",
            "id": "0c78ec14187d4193be93a9d30182efba",
            "included": False,
            "is_handwritten": False,
            "is_printed": True,
            "type": "diagram",
        },
    ],
    "request_id": "3c37d6b3-e3fe-4b85-9aeb-0d8d8a104679",
    "text": "Equivalent resistance between points \\( A \\) \\& \\( B \\) in the adjacent circuit is -",
    "version": "SuperNet-200",
}

DOC_EXAMPLE_LINES_JSON = {
    "text": "Hello",
    "lines_json": {
        "image_id": "request-image-id",
        "page": 1,
        "page_width": 1200,
        "page_height": 1600,
        "lines": [
            {
                "id": "region-1",
                "type": "text",
                "text": "Hello",
                "region": {"top_left_x": 20, "top_left_y": 30, "width": 200, "height": 40},
            }
        ],
    },
}

failures = []


def check(label, condition, detail=""):
    if condition:
        print("  OK   " + label)
    else:
        print("  FAIL " + label + ("  " + detail if detail else ""))
        failures.append(label)


print("=== 1) MMD → (text, latex_text) 분리 (후보 규칙) ===")
text, latex = adapter.split_mmd_to_text_and_latex("Equivalent resistance between points \\( A \\) \\& \\( B \\) in the adjacent circuit is -")
check("혼합 줄에서 latex 추출", latex is not None and "A" in latex, repr(latex))
check("혼합 줄에서 text 보존", "Equivalent resistance" in text, repr(text))
text2, latex2 = adapter.split_mmd_to_text_and_latex("\\(2(x-3)=6\\)")
check("순수 수식 줄 → latex 만", latex2 == "2(x-3)=6", repr(latex2))
text3, latex3 = adapter.split_mmd_to_text_and_latex("plain text only")
check("수식 없음 → latex None", latex3 is None and text3 == "plain text only")

print()
print("=== 2) line → step 파생 (후보 규칙) ===")
draft = adapter.draft_from_response(DOC_EXAMPLE_LINE_DATA)
check("인쇄 줄이 problem 로 분리", draft["problem"]["text"] != "", repr(draft["problem"]["text"]))
check("problem latex 추출", draft["problem"]["latex_text"] is not None, repr(draft["problem"]["latex_text"]))
check("model_version = version 필드", draft["model_version"] == "SuperNet-200", draft["model_version"])
check("image_not_supported 줄은 단계 아님(도형)", not any(s["unreadable"] for s in draft["steps"]), repr(draft["steps"]))
check("벤더가 내지 않은 줄은 confidence 누락 아님", draft["missing_confidence"] == [], repr(draft["missing_confidence"]))

# math_confidence 줄(수식인데 못 읽음)은 **미인식 단계**로 남아야 한다
MATH_CONF = json.loads(json.dumps(DOC_EXAMPLE_LINE_DATA))
MATH_CONF["line_data"][1]["error_id"] = "math_confidence"
MATH_CONF["line_data"][1]["type"] = "math"
MATH_CONF["line_data"][1]["is_printed"] = False
MATH_CONF["line_data"][1]["is_handwritten"] = True
draft_math = adapter.draft_from_response(MATH_CONF)
check("math_confidence 줄은 미인식 단계", any(s["unreadable"] for s in draft_math["steps"]), repr(draft_math["steps"]))
check("미인식 단계 text 는 ??? 만", all(s["text"] == "???" for s in draft_math["steps"] if s["unreadable"]))
check("미인식 단계 latex 는 None", all(s["latex_text"] is None for s in draft_math["steps"] if s["unreadable"]))
check("미인식 단계 confidence 누락으로 기록", len(draft_math["missing_confidence"]) > 0, repr(draft_math["missing_confidence"]))

print()
print("=== 3) confidence 누락 → 0.0 대체 없음 ===")
no_conf = json.loads(json.dumps(DOC_EXAMPLE_LINE_DATA))
no_conf["line_data"][0].pop("confidence")
no_conf["line_data"][0].pop("confidence_rate")
draft_missing = adapter.draft_from_response(no_conf)
check("누락이 missing 목록에 잡힘", len(draft_missing["missing_confidence"]) > 0, repr(draft_missing["missing_confidence"]))
try:
    adapter.to_ocr_result(draft_missing)
    check("누락 있으면 OcrResult 변환 거부", False, "변환되어 버림")
except adapter.UndecidedContractError as exc:
    check("누락 있으면 OcrResult 변환 거부", True)
    check("거부 메시지에 M12 언급", "M12" in str(exc))

print()
print("=== 4) confidence 있는 경우 OcrResult 로 변환 ===")
try:
    result = adapter.to_ocr_result(draft)
    check("OcrResult 변환 성공", result.model_version == "SuperNet-200")
    check("problem confidence 존재", result.problem.confidence is not None)
except Exception as exc:
    check("OcrResult 변환 성공", False, "{}: {}".format(type(exc).__name__, exc))

print()
print("=== 5) lines_json region → bbox ===")
bbox = adapter._bbox_of(DOC_EXAMPLE_LINES_JSON["lines_json"]["lines"][0])
check("region → [x,y,w,h]", bbox == [20.0, 30.0, 200.0, 40.0], repr(bbox))

print()
print("=== 6) measure.py 가 라벨/결과를 읽고 지표를 계산하는가 ===")
import measure  # noqa: E402

LABELS = {
    "pricing": {"per_image_usd": 0.002},
    "cases": [
        {
            "id": "E01",
            "category": "complete-correct",
            "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": ["2(x-3)=6"],
            "step_order": [1],
            "step_segments": [[1, 1]],
            "unreadable_steps": [],
            "printed_handwritten": "printed",
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ],
}
RESULTS = [
    {
        "id": "E01",
        "elapsed_seconds": 0.42,
        "error": None,
        "draft": {
            "problem": {"text": "2(x-3)=6", "latex_text": "2(x-3)=6", "confidence": 0.95},
            "steps": [
                {"step_no": 1, "text": "2(x-3)=6", "latex_text": "2(x-3)=6",
                 "confidence": 0.93, "unreadable": False}
            ],
            "regions": [],
            "source_line_count": 1,
            "billable_text_row_count": 1,
        },
        "analysis": {
            "analysis_status": "ANALYZED",
            "error_step": None,
            "error_type": None,
            "misconception_ids": [],
        },
    }
]
report = measure.measure(LABELS["cases"], RESULTS, LABELS["pricing"])
acc = report["A_ocr_accuracy"]
check("동치율 계산됨", acc["problem_latex_equivalence"] == 1.0, repr(acc["problem_latex_equivalence"]))
check("완전일치율 계산됨", acc["problem_latex_exact_match"] == 1.0, repr(acc["problem_latex_exact_match"]))
check("완전일치 분모 = 1", acc["problem_latex_exact_comparable"] == 1, repr(acc["problem_latex_exact_comparable"]))
check("동치 판정 가능 분모 = 1", acc["problem_latex_equivalence_decidable"] == 1, repr(acc["problem_latex_equivalence_decidable"]))
check("지연 p50 계산됨", report["C_operations"]["latency_p50"] == 0.42, repr(report["C_operations"]["latency_p50"]))
check("비용은 추정치로 명명됨", "cost_estimate_usd_total" in report["C_operations"])
check("비용 추정값 계산됨", report["C_operations"]["cost_estimate_usd_total"] == 0.002, repr(report["C_operations"]["cost_estimate_usd_total"]))
check("비용 설명에 추정치 고지", "추정치" in report["C_operations"]["cost_estimate_note"])
check("'actual cost' 라벨 없음", "actual cost" not in json.dumps(report, ensure_ascii=False))
check("B 최종판정 일치율 1.0", report["B_ai1_impact"]["final_error_judgement_match"] == 1.0)
check("B 비교 가능 건수 1", report["B_ai1_impact"]["final_error_judgement_comparable"] == 1)

print()
print("=== 7) 자격증명 없을 때 명확히 실패 ===")
import mathpix_client as client  # noqa: E402
import os  # noqa: E402

os.environ.pop("MATHPIX_APP_ID", None)
os.environ.pop("MATHPIX_APP_KEY", None)
try:
    client.credentials()
    check("자격증명 부재 시 명확히 실패", False, "예외 없이 통과")
except client.MathpixError as exc:
    check("자격증명 부재 시 명확히 실패", True)
    check("메시지에 환경변수 이름 포함", "MATHPIX_APP_ID" in str(exc))

print()
print("=== 8) M5 — 종류 미정은 임의 배정하지 않는다 ===")
UNDECIDED = json.loads(json.dumps(DOC_EXAMPLE_LINE_DATA))
UNDECIDED["line_data"][1] = {
    "cnt": [[6, 243], [652, 243], [652, 683], [6, 683]],
    "conversion_output": False,
    "error_id": "image_max_size",
    "id": "undecided-1",
    "is_handwritten": False,
    "is_printed": False,
    "type": "math",
}
draft_undecided = adapter.draft_from_response(UNDECIDED)
check("미정 종류 줄이 undecided_kind 에 있음", len(draft_undecided["undecided_kind"]) == 1,
      repr(draft_undecided["undecided_kind"]))
check("미정 사유가 both-false",
      draft_undecided["undecided_kind"] and
      draft_undecided["undecided_kind"][0].get("reason") == "both-false",
      repr(draft_undecided["undecided_kind"]))
check("미정 줄은 unreadable 로도 기록됨",
      draft_undecided["undecided_kind"] and
      draft_undecided["undecided_kind"][0].get("unreadable") is True)
check("미정 줄이 handwritten step 으로 안 나감", draft_undecided["steps"] == [],
      repr(draft_undecided["steps"]))
check("미정 줄의 region kind 는 None",
      any(r["kind"] is None for r in draft_undecided["regions"]))
try:
    adapter.to_ocr_result(draft_undecided)
    check("미정 종류 있으면 OcrResult 변환 보류", False, "변환되어 버림")
except adapter.UndecidedContractError as exc:
    check("미정 종류 있으면 OcrResult 변환 보류", True)
    check("거부 메시지에 M5 언급", "M5" in str(exc))
    check("거부 메시지에 사유(both-false) 언급", "both-false" in str(exc))

print()
print("=== 9) 비용 경계 — '12줄 초과' (11/12/13) ===")
PRICING = {"per_image_usd": 0.002, "per_page_usd": 0.005}
for rows, expected in ((11, 0.002), (12, 0.002), (13, 0.005)):
    cost_label = {
        "id": "E{}".format(rows), "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
        "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
    }
    cost_draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "source_line_count": rows,
        "billable_text_row_count": rows,
    }
    cost_report = measure.measure(
        [cost_label],
        [{"id": "E{}".format(rows), "elapsed_seconds": 0.1, "error": None,
          "draft": cost_draft, "analysis": None}],
        PRICING,
    )
    got = cost_report["C_operations"]["cost_estimate_usd_total"]
    check("{}줄 → ${}".format(rows, expected), got == expected, repr(got))
check("비용은 추정치로 명명됨",
      "cost_estimate_usd_total" in measure.measure(
          [], [], PRICING)["C_operations"])

print()
print("=== 10) 인쇄/필기 — 원본 줄 기준, problem_kind 에서 추론 금지 ===")
kind_label = {
    "id": "EK", "category": "x", "problem_latex_text": "2(x-3)=6",
    "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
    "line_kinds": {"0": "printed"},
    "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
}
kind_draft = {
    "problem": {"text": "", "latex_text": None, "confidence": None},
    "problem_bbox": None, "problem_kind": "handwritten",  # 일부러 틀림
    "steps": [], "regions": [], "line_kinds": {"0": "printed"},
    "source_line_count": 1,
}
kind_report = measure.measure(
    [kind_label],
    [{"id": "EK", "elapsed_seconds": 0.1, "error": None,
      "draft": kind_draft, "analysis": None}],
    {},
)
kind_acc = kind_report["A_ocr_accuracy"]
check("원본 줄 기준 채점 = 1.0",
      kind_acc["printed_handwritten_accuracy"] == 1.0, repr(kind_acc["printed_handwritten_accuracy"]))
check("problem_kind 가 틀려도 줄 라벨 기준",
      kind_acc["printed_handwritten_comparable_lines"] == 1)

no_line_label = dict(kind_label)
no_line_label.pop("line_kinds")
no_line_report = measure.measure(
    [no_line_label],
    [{"id": "EK", "elapsed_seconds": 0.1, "error": None,
      "draft": dict(kind_draft, problem_kind="printed"), "analysis": None}],
    {},
)
no_line_acc = no_line_report["A_ocr_accuracy"]
check("줄 라벨이 없으면 점수 없음",
      no_line_acc["printed_handwritten_accuracy"] is None,
      repr(no_line_acc["printed_handwritten_accuracy"]))
check("unavailable 사유 기록",
      no_line_acc["printed_handwritten_unavailable_reasons"].get("label_line_kinds_missing") == 1,
      repr(no_line_acc["printed_handwritten_unavailable_reasons"]))

print()
if failures:
    print("{}건 실패: {}".format(len(failures), failures))
    raise SystemExit(1)
print("하네스 자체 검증 통과 — 단, 이는 문서 예시 기반이며 실측값이 아니다.")
