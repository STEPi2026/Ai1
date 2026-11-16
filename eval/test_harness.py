"""eval 하네스 계약 테스트 (PHASE 9-9B.1).

**실제 Mathpix 호출·학생 이미지·API key 를 쓰지 않는다.**
synthetic response(문서에 게시된 Mathpix 예시 + 합성 케이스)로 변환과 지표 계산을 검증한다.

pytest 수집에 포함되도록 `pytest.ini` 의 testpaths 에 `eval` 가 들어 있다.
`eval/_local/` 은 norecursedirs 로 제외된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT.parent))

import mathpix_adapter as adapter  # noqa: E402
import measure  # noqa: E402
import mathpix_client as client  # noqa: E402
import sandbox_probe as probe  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic payload 빌더
# ---------------------------------------------------------------------------

def _line(index, text, *, printed=False, handwritten=True, confidence=0.9,
          kind="math", error_id=None, conversion_output=True, cnt=None):
    line = {
        "id": "line-{}".format(index),
        "type": kind,
        "text": text,
        "confidence": confidence,
        "confidence_rate": confidence,
        "is_printed": printed,
        "is_handwritten": handwritten,
        "conversion_output": conversion_output,
    }
    if error_id is not None:
        line["error_id"] = error_id
    if cnt is not None:
        line["cnt"] = cnt
    return line


def _payload(lines, version="SuperNet-200"):
    return {
        "request_id": "req-1",
        "version": version,
        "is_printed": True,
        "is_handwritten": True,
        "confidence": 0.9,
        "confidence_rate": 0.95,
        "line_data": lines,
    }


# ===========================================================================
# 1. bbox — polygon / region / lines_json 대응
# ===========================================================================

def test_polygon_to_bbox_handles_rotated_quad():
    """Mathpix `cnt` 는 회전된 사각형일 수 있다 — min/max 로 잡아야 한다."""
    bbox = adapter.polygon_to_bbox([[0, 77], [0, 32], [867, 54], [865, 98]])
    assert bbox is not None
    assert bbox == [0.0, 32.0, 867.0, 66.0]


def test_polygon_to_bbox_rejects_bad_input():
    assert adapter.polygon_to_bbox([]) is None
    assert adapter.polygon_to_bbox([[1, 2]]) is None
    assert adapter.polygon_to_bbox([["a", "b"], [1, 2]]) is None


def test_bbox_preserved_from_cnt_through_draft_and_ocr_result():
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x-6=6\\)", cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["problem_bbox"] == [40.0, 20.0, 960.0, 80.0]
    assert draft["steps"][0]["bbox"] == [40.0, 130.0, 860.0, 70.0]
    # regions 에도 bbox 가 남아야 한다
    assert all(region["bbox"] is not None for region in draft["regions"])

    result = adapter.to_ocr_result(draft)
    assert len(result.regions) == 2
    assert result.regions[0].bbox.x == 40.0
    assert result.regions[0].bbox.y == 20.0
    assert result.regions[0].bbox.width == 960.0
    assert result.regions[0].bbox.height == 80.0


def test_bbox_from_lines_json_region_when_cnt_missing():
    payload = _payload([
        _line(0, "\\(2x=6\\)", printed=True, handwritten=False, kind="text"),
    ])
    payload["lines_json"] = {
        "lines": [
            {
                "id": "line-0",
                "type": "text",
                "text": "\\(2x=6\\)",
                "region": {"top_left_x": 10, "top_left_y": 20, "width": 200, "height": 40},
            }
        ]
    }
    draft = adapter.draft_from_response(payload)
    assert draft["problem_bbox"] == [10.0, 20.0, 200.0, 40.0]


# ===========================================================================
# 2. y/x 정렬 — 실제 geometry 사용
# ===========================================================================

def test_steps_sorted_by_y_then_x():
    """위→아래(y), 같은 y 면 왼쪽→오른쪽(x) 순서."""
    payload = _payload([
        _line(0, "\\(b\\)", cnt=[[500, 300], [500, 320], [560, 320], [560, 300]]),
        _line(1, "\\(a\\)", cnt=[[40, 300], [40, 320], [100, 320], [100, 300]]),
        _line(2, "\\(c\\)", cnt=[[40, 100], [40, 120], [100, 120], [100, 100]]),
    ])
    draft = adapter.draft_from_response(payload)
    texts = [step["text"] for step in draft["steps"]]
    assert texts == ["c", "a", "b"]


# ===========================================================================
# 3. error_id 처리
# ===========================================================================

def test_image_max_size_is_preserved_as_unreadable_step():
    """`image_max_size` 는 콘텐츠가 있을 수 있으므로 버리지 않는다."""
    payload = _payload([
        _line(0, "\\(2x=6\\)", error_id="image_max_size", conversion_output=False,
              confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["steps"]) == 1
    assert draft["steps"][0]["unreadable"] is True
    assert draft["steps"][0]["text"] == "???"
    assert draft["steps"][0]["latex_text"] is None
    assert draft["excluded_lines"] == []


def test_image_not_supported_on_text_line_is_not_lost():
    """`image_not_supported` 라도 타입이 text/math 면 유실하지 않는다."""
    payload = _payload([
        _line(0, "\\(2x=6\\)", error_id="image_not_supported", conversion_output=False,
              confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["steps"]) == 1
    assert draft["steps"][0]["unreadable"] is True
    assert draft["steps"][0]["text"] == "???"


def test_image_not_supported_on_diagram_is_omitted_but_recorded():
    """도형 등 비풀이 콘텐츠는 생략하되 **기록은 남긴다**(조용히 사라지지 않음)."""
    payload = _payload([
        _line(0, "\\(2x=6\\)"),
        _line(1, "", kind="diagram", error_id="image_not_supported",
              conversion_output=False, confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["steps"]) == 1
    excluded = [e for e in draft["excluded_lines"] if e["type"] == "diagram"]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "not-solution-content-type"


def test_undecidable_kind_is_preserved_not_assigned():
    """is_printed/is_handwritten 이 둘 다 false 면 종류를 임의로 정하지 않는다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text"),
        _line(1, "\\(2x=6\\)", printed=False, handwritten=False),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["undecided_kind"]) == 1
    assert draft["undecided_kind"][0]["source_line_index"] == 1
    # 임의 배정 금지 → 변환을 보류한다
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M5" in str(excinfo.value)


# ===========================================================================
# 1. M5 — 종류 미정은 임의 배정하지 않는다 (PHASE 9-9B.2)
# ===========================================================================

def test_undecided_kind_includes_unreadable_content_line():
    """**unreadable 인 줄도** `undecided_kind` 에 포함된다.

    미인식이라는 사실이 종류를 정해 주지는 않는다.
    """
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text"),
        _line(1, "\\(2x=6\\)", printed=False, handwritten=False,
              error_id="image_max_size", conversion_output=False, confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["undecided_kind"]) == 1
    entry = draft["undecided_kind"][0]
    assert entry["source_line_index"] == 1
    assert entry["unreadable"] is True
    assert entry["reason"] == "both-false"


def test_undecided_line_is_not_output_as_step_or_region():
    """회귀 — 종류 미정인 줄이 handwritten step 으로도 printed region 으로도 나오면 안 된다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x=6\\)", printed=False, handwritten=False,
              cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    # 초안 어느 버킷에도 들어가면 안 된다
    assert draft["steps"] == []
    assert [r for r in draft["regions"] if r["source_line_index"] == 1][0]["kind"] is None
    # 변환은 보류된다
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M5" in str(excinfo.value)


def test_undecided_kind_block_message_carries_reason():
    """보류 메시지에 사유(both-false)와 unreadable 여부가 드러난다."""
    payload = _payload([
        _line(0, "\\(2x=6\\)", printed=False, handwritten=False),
    ])
    draft = adapter.draft_from_response(payload)
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    message = str(excinfo.value)
    assert "M5" in message
    assert "both-false" in message


def test_both_printed_and_handwritten_is_also_undecided():
    """둘 다 true 면 어느 쪽으로도 배정할 수 없다 — 미정으로 남는다."""
    payload = _payload([
        _line(0, "\\(2x=6\\)", printed=True, handwritten=True),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["undecided_kind"]) == 1
    assert draft["undecided_kind"][0]["reason"] == "both-true"
    assert draft["steps"] == []
    with pytest.raises(adapter.UndecidedContractError):
        adapter.to_ocr_result(draft)


def test_undecided_unreadable_line_is_not_output_as_step():
    """회귀 — **미인식 + 종류 미정** 인 줄이 handwritten step 으로 나오면 안 된다.

    이전 규칙(`not unreadable and kind != kind`)에서는 unreadable 인 줄이
    필터 자체를 통과해 버릴 수 있었다.
    """
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text"),
        _line(1, "\\(2x=6\\)", printed=False, handwritten=False,
              error_id="image_max_size", conversion_output=False, confidence=None,
              cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["steps"] == []
    assert len(draft["undecided_kind"]) == 1
    assert draft["undecided_kind"][0]["unreadable"] is True
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M5" in str(excinfo.value)


def test_unreadable_line_with_decided_kind_goes_to_its_own_bucket():
    """종류가 정해진 미인식 줄은 **인쇄 버킷**에 들어간다 (필기로 몰아넣지 않는다)."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              error_id="image_max_size", conversion_output=False, confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["steps"] == []          # 필기 단계 아님
    assert draft["undecided_kind"] == []  # 종류는 정해짐
    # 문제는 읽힌 인쇄 줄이 없으므로 confidence 누락(M12)으로 변환 보류
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M12" in str(excinfo.value)


def test_missing_confidence_blocks_conversion_without_inventing_zero():
    """confidence 누락 → 0.0 대체 없이 변환 중단 (M12 미결)."""
    payload = _payload([
        _line(0, "\\(2x=6\\)", confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["missing_confidence"]
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M12" in str(excinfo.value)


def test_excluded_non_content_line_is_not_missing_confidence():
    """벤더가 내지 않은 줄은 confidence 누락이 아니다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text"),
        _line(1, "", kind="diagram", error_id="image_not_supported",
              conversion_output=False, confidence=None),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["missing_confidence"] == []


def test_to_ocr_result_keeps_regions_and_steps():
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x-6=6\\)", cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
        _line(2, "\\(2x=12\\)", cnt=[[40, 210], [40, 280], [900, 280], [900, 210]]),
    ])
    draft = adapter.draft_from_response(payload)
    result = adapter.to_ocr_result(draft)
    assert len(result.steps) == 2
    assert len(result.regions) == 3
    assert result.problem.latex_text is not None
    assert result.problem.confidence == 0.9


def test_non_content_regions_do_not_enter_ocr_result():
    """풀이 콘텐츠가 아닌 영역(도형 등)은 OcrResult.regions 에 들어가지 않는다.

    벤더가 confidence 를 주지 않은 영역을 우리 계약(필수 float)에 억지로 넣지 않기
    위한 것이기도 하다. 그 영역은 `excluded_lines` 에 기록된다.
    """
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "", kind="diagram", error_id="image_not_supported",
              conversion_output=False, confidence=None,
              cnt=[[6, 243], [652, 243], [652, 683], [6, 683]]),
    ])
    draft = adapter.draft_from_response(payload)
    assert len(draft["excluded_lines"]) == 1
    result = adapter.to_ocr_result(draft)
    assert len(result.regions) == 1
    assert result.regions[0].kind == "printed"


def test_unreadable_content_region_keeps_marker_and_blocks_on_missing_confidence():
    """미인식 콘텐츠 영역은 '???' 로 표시되고 confidence 누락이면 변환이 보류된다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x=6\\)", printed=True, handwritten=False, kind="math",
              error_id="math_confidence", conversion_output=False, confidence=None,
              cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    region = draft["regions"][1]
    assert region["unreadable"] is True
    assert region["text"] == "???"
    assert region["latex_text"] is None
    # confidence 누락 → M12 로 변환 보류
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(draft)
    assert "M12" in str(excinfo.value)


def test_step_region_id_is_null_while_m14_undecided():
    """M14 — `OcrResult` 에 없는 region ID 를 참조하지 않는다.

    `OcrRegion` 에 `id` 필드가 없으므로 provider 의 line id 를 `region_id` 에
    넣으면 **응답 안에서 resolve 되지 않는 dangling reference** 가 된다.
    provider line id 는 **eval 초안**에 보존한다.
    """
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x=6\\)", cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    # provider line id 는 초안에 보존된다
    assert draft["steps"][0]["source_line_id"] == "line-1"
    links = draft["step_region_links"]
    assert len(links) == 1
    assert links[0]["provider_line_id"] == "line-1"
    assert links[0]["linked"] is False
    assert links[0]["reason"] == "M14"

    # OcrResult 에는 그런 region 식별자가 없다
    result = adapter.to_ocr_result(draft)
    assert not hasattr(result.regions[0], "id")
    # → region_id 는 null
    assert result.steps[0].region_id is None


def test_require_region_link_policy_blocks_conversion_with_m14():
    """연결이 필수라면 **보류**한다 — 임의로 채우지 않는다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x=6\\)", cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
    ])
    draft = adapter.draft_from_response(payload)
    with pytest.raises(adapter.UndecidedContractError) as excinfo:
        adapter.to_ocr_result(
            draft, region_link_policy=adapter.REGION_LINK_POLICY_REQUIRE
        )
    assert "M14" in str(excinfo.value)


def test_unknown_region_link_policy_is_rejected():
    payload = _payload([
        _line(0, "\\(2x=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
    ])
    draft = adapter.draft_from_response(payload)
    with pytest.raises(ValueError):
        adapter.to_ocr_result(draft, region_link_policy="guess")


def test_ocr_result_never_references_a_nonexistent_region():
    """회귀 — 어떤 단계도 존재하지 않는 region 을 가리키지 않는다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text",
              cnt=[[40, 20], [40, 100], [1000, 100], [1000, 20]]),
        _line(1, "\\(2x=6\\)", cnt=[[40, 130], [40, 200], [900, 200], [900, 130]]),
        _line(2, "\\(2x=12\\)", cnt=[[40, 210], [40, 280], [900, 280], [900, 210]]),
    ])
    draft = adapter.draft_from_response(payload)
    result = adapter.to_ocr_result(draft)
    region_ids = {getattr(region, "id", None) for region in result.regions}
    for step in result.steps:
        if step.region_id is not None:
            assert step.region_id in region_ids
    assert all(step.region_id is None for step in result.steps)


# ===========================================================================
# 4. 지표 — 분모 분리 / 경계 / IoU / 비용
# ===========================================================================

def _result_for(draft, elapsed=0.4, analysis=None, case_id="E01"):
    return {
        "id": case_id,
        "elapsed_seconds": elapsed,
        "error": None,
        "draft": draft,
        "analysis": analysis,
    }


def test_exact_and_equivalence_denominators_are_separated():
    """완전일치 분모와 동치 판정 가능 분모는 다르다."""
    labels = [
        {
            "id": "E01",
            "category": "complete-correct",
            "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": ["2(x-3)=6"],
            "step_source_lines": [0],
            "step_order": [1],
            "unreadable_steps": [],
            "printed_handwritten": "printed",
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "2(x-3)=6", "latex_text": "2(x-3)=6", "confidence": 0.9},
        "problem_bbox": None,
        "problem_kind": "printed",
        "steps": [
            {"step_no": 1, "text": "2(x-3)=6", "latex_text": "2(x-3)=6",
             "confidence": 0.9, "unreadable": False, "source_line_index": 0}
        ],
        "regions": [],
        "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    acc = report["A_ocr_accuracy"]
    assert acc["problem_latex_exact_comparable"] == 1
    assert acc["problem_latex_equivalence_decidable"] == 1
    assert acc["problem_latex_exact_match"] == 1.0
    assert acc["problem_latex_equivalence"] == 1.0


def test_segment_f1_not_reported_without_source_lines():
    """원본 줄 범위가 없으면 경계 정확도로 부르지 않는다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [{"step_no": 1, "text": "x", "latex_text": None,
                   "confidence": 0.5, "unreadable": False, "source_line_index": 0}],
        "regions": [], "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    acc = report["A_ocr_accuracy"]
    assert acc["segment_f1_mean"] is None
    assert acc["segment_f1_unavailable_cases"] == 1


def test_segment_f1_computed_with_source_lines():
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": ["2(x-3)=6"], "step_source_lines": [0],
            "step_order": [1], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "2(x-3)=6", "latex_text": "2(x-3)=6", "confidence": 0.9},
        "problem_bbox": None, "problem_kind": "printed",
        "steps": [{"step_no": 1, "text": "2(x-3)=6", "latex_text": "2(x-3)=6",
                   "confidence": 0.9, "unreadable": False, "source_line_index": 0}],
        "regions": [], "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    assert report["A_ocr_accuracy"]["segment_f1_mean"] == 1.0


def test_bbox_iou_only_between_matched_regions():
    """라벨에 없는 region 은 IoU 분모에 들어가지 않는다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "region_bboxes": {"0": [0, 0, 10, 10]},
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [
            {"id": "line-0", "source_line_index": 0, "bbox": [0, 0, 10, 10]},
            {"id": "line-1", "source_line_index": 1, "bbox": [100, 100, 10, 10]},
        ],
        "source_line_count": 2,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    assert report["A_ocr_accuracy"]["bbox_iou_matched_regions"] == 1
    assert report["A_ocr_accuracy"]["bbox_iou_median"] == 1.0


def test_bbox_iou_does_not_match_by_provider_id():
    """회귀 — provider 응답 id 를 키로 쓰면 매칭되지 않는다 (M16).

    라벨 키는 **원본 줄 인덱스**여야 한다. id 로 키를 쓴 라벨은
    경고로 집계되고 매칭되지 않는다.
    """
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "region_bboxes": {"1177a9c84cba4101ad53c10053113218": [0, 0, 10, 10]},
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [
            {"id": "1177a9c84cba4101ad53c10053113218", "source_line_index": 0,
             "bbox": [0, 0, 10, 10]},
        ],
        "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    acc = report["A_ocr_accuracy"]
    assert acc["bbox_iou_matched_regions"] == 0
    assert acc["label_region_bbox_keys_not_source_line_index"] == 1


# ===========================================================================
# 2. 인쇄/필기 정확률 — 원본 줄( region) 기준 (PHASE 9-9B.2)
# ===========================================================================

def test_printed_handwritten_scored_per_source_line():
    """원본 줄 단위로 인쇄/필기를 채점한다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "line_kinds": {"0": "printed", "1": "handwritten", "2": "handwritten"},
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [],
        "line_kinds": {"0": "printed", "1": "handwritten", "2": "printed"},
        "source_line_count": 3,
    }
    acc = measure.measure(labels, [_result_for(draft)], {})["A_ocr_accuracy"]
    assert acc["printed_handwritten_comparable_lines"] == 3
    assert acc["printed_handwritten_accuracy"] == pytest.approx(2 / 3)


def test_printed_handwritten_unavailable_when_label_has_no_line_kinds():
    """대응시킬 줄이 없으면 **점수를 만들지 않는다** (unavailable + 사유)."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "line_kinds": {"0": "printed"},
        "source_line_count": 1,
    }
    acc = measure.measure(labels, [_result_for(draft)], {})["A_ocr_accuracy"]
    assert acc["printed_handwritten_accuracy"] is None
    assert acc["printed_handwritten_comparable_lines"] == 0
    assert acc["printed_handwritten_unavailable_cases"] == 1
    assert acc["printed_handwritten_unavailable_reasons"]["label_line_kinds_missing"] == 1


def test_printed_handwritten_is_not_inferred_from_problem_kind():
    """회귀 — `problem_kind` 에서 정확도를 추론하지 않는다.

    (a) `problem_kind` 가 틀려도 원본 줄 라벨이 맞으면 점수는 줄 라벨 기준이다.
    (b) `problem_kind` 가 맞아도 줄 라벨이 없으면 **점수를 만들지 않는다.**
    """
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "line_kinds": {"0": "printed"},
            "printed_handwritten": "printed",
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    base = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None,
        "steps": [], "regions": [],
        "line_kinds": {"0": "printed"},
        "source_line_count": 1,
    }
    # (a) problem_kind 는 틀렸지만 줄 라벨 기준이므로 1.0
    wrong_problem_kind = dict(base, problem_kind="handwritten")
    acc = measure.measure(labels, [_result_for(wrong_problem_kind)], {})["A_ocr_accuracy"]
    assert acc["printed_handwritten_accuracy"] == 1.0

    # (b) 줄 라벨이 없으면 problem_kind 가 맞아도 unavailable
    labels_no_lines = [dict(labels[0])]
    labels_no_lines[0].pop("line_kinds")
    acc2 = measure.measure(labels_no_lines, [_result_for(dict(base, problem_kind="printed"))], {})[
        "A_ocr_accuracy"
    ]
    assert acc2["printed_handwritten_accuracy"] is None
    assert acc2["printed_handwritten_unavailable_reasons"]["label_line_kinds_missing"] == 1


def test_printed_handwritten_provider_id_label_keys_are_not_used():
    """provider id 로 키를 쓴 라벨은 **사용하지 않는다** — unavailable 로 보고."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "line_kinds": {"1177a9c84cba4101ad53c10053113218": "printed"},
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [],
        "line_kinds": {"0": "printed"},
        "source_line_count": 1,
    }
    acc = measure.measure(labels, [_result_for(draft)], {})["A_ocr_accuracy"]
    assert acc["printed_handwritten_accuracy"] is None
    assert acc["printed_handwritten_comparable_lines"] == 0
    assert (
        acc["printed_handwritten_unavailable_reasons"]["label_keys_not_source_line_index"] == 1
    )


def test_printed_handwritten_undecided_prediction_line_is_not_scored():
    """예측 종류가 미정인 줄은 채점하지 않고 그 사실만 보고한다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "line_kinds": {"0": "printed", "1": "handwritten"},
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [],
        "line_kinds": {"0": "printed", "1": None},
        "source_line_count": 2,
    }
    acc = measure.measure(labels, [_result_for(draft)], {})["A_ocr_accuracy"]
    assert acc["printed_handwritten_comparable_lines"] == 1
    assert acc["printed_handwritten_accuracy"] == 1.0
    assert acc["printed_handwritten_undecided_lines"] == 1


# ===========================================================================
# 3. 비용 경계 — "12줄 초과" (PHASE 9-9B.2)
# ===========================================================================

def test_cost_boundary_11_12_13_rows():
    """11줄·12줄은 이미지 단가, **13줄부터** 페이지 단가 ("12줄 초과")."""
    pricing = {"per_image_usd": 0.002, "per_page_usd": 0.005}
    base = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "source_line_count": 0,
    }
    expected = {11: 0.002, 12: 0.002, 13: 0.005}
    for rows, unit in expected.items():
        labels = [
            {
                "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
                "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
                "expected_error": {"error_step": None, "error_type": None,
                                   "misconception_ids": []},
            }
        ]
        draft = dict(base, billable_text_row_count=rows, source_line_count=rows)
        report = measure.measure(labels, [_result_for(draft)], pricing)
        ops = report["C_operations"]
        assert ops["cost_estimate_usd_total"] == pytest.approx(unit), rows
        assert ops["cases_billed_at_page_rate"] == (1 if rows > 12 else 0), rows


def test_cost_counts_only_text_math_rows():
    """도형 등은 **과금 대상 텍스트 행**으로 세지 않는다."""
    payload = _payload([
        _line(0, "\\(2(x-3)=6\\)", printed=True, handwritten=False, kind="text"),
        _line(1, "", kind="diagram", error_id="image_not_supported",
              conversion_output=False, confidence=None),
        _line(2, "\\(2x=6\\)"),
    ])
    draft = adapter.draft_from_response(payload)
    assert draft["source_line_count"] == 3
    assert draft["billable_text_row_count"] == 2  # 도형 제외


def test_cost_uses_billable_rows_not_total_lines():
    """총 줄 수가 12줄을 넘더라도 **과금 대상 텍스트 행**이 12줄이면 이미지 단가다."""
    pricing = {"per_image_usd": 0.002, "per_page_usd": 0.005}
    lines = [
        _line(index, "\\(x={}\\)".format(index)) for index in range(12)
    ]
    lines.append(
        _line(12, "", kind="diagram", error_id="image_not_supported",
              conversion_output=False, confidence=None)
    )
    payload = _payload(lines)
    draft = adapter.draft_from_response(payload)
    assert draft["source_line_count"] == 13      # 총 줄 수는 12 초과
    assert draft["billable_text_row_count"] == 12  # 과금 대상 텍스트 행은 12
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None,
                               "misconception_ids": []},
        }
    ]
    ops = measure.measure(labels, [_result_for(draft)], pricing)["C_operations"]
    assert ops["cost_estimate_usd_total"] == pytest.approx(0.002)
    assert ops["cases_billed_at_page_rate"] == 0


def test_cost_is_estimate_and_applies_12_line_rule():
    """비용은 '추정치' 명명이고 12줄 초과에는 페이지 단가를 적용한다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        },
        {
            "id": "E02", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        },
    ]
    small = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "source_line_count": 5,
        "billable_text_row_count": 5,
    }
    big = dict(small, source_line_count=13, billable_text_row_count=13)
    pricing = {"per_image_usd": 0.002, "per_page_usd": 0.005}
    report = measure.measure(
        labels,
        [_result_for(small, case_id="E01"), _result_for(big, case_id="E02")],
        pricing,
    )
    ops = report["C_operations"]
    assert ops["cost_estimate_usd_total"] == pytest.approx(0.002 + 0.005)
    assert ops["cases_billed_at_page_rate"] == 1
    assert "추정치" in ops["cost_estimate_note"]
    assert "actual cost" not in json.dumps(report, ensure_ascii=False)


# ===========================================================================
# 4. 라벨 키 안정성 (PHASE 9-9B.2)
# ===========================================================================

def test_line_index_stability_unknown_without_repeats():
    """반복 측정이 없으면 **미확인(None)** — 안정하다고 가정하지 않는다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    stability = report["D_label_key_stability"]
    assert stability["source_line_index_stable"] is None
    assert stability["cases_with_repeats"] == 0


def test_line_index_stability_detects_stable_repeats():
    """반복 측정에서 줄 대응이 같으면 안정으로 보고한다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [
            {"id": "a", "source_line_index": 0, "type": "text", "kind": "printed", "text": "p"},
            {"id": "b", "source_line_index": 1, "type": "math", "kind": "handwritten", "text": "h"},
        ],
        "source_line_count": 2,
    }
    report = measure.measure(
        labels,
        [_result_for(draft), _result_for(dict(draft), case_id="E01")],
        {},
    )
    stability = report["D_label_key_stability"]
    assert stability["source_line_index_stable"] is True
    assert stability["stable_cases"] == 1
    assert stability["unstable_cases"] == 0
    assert report["counts"]["measurements_total"] == 2


def test_line_index_stability_detects_unstable_repeats():
    """줄 순서/내용이 달라지면 **불안정**으로 보고한다."""
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    first = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [
            {"id": "a", "source_line_index": 0, "type": "text", "kind": "printed", "text": "p"},
        ],
        "source_line_count": 1,
    }
    second = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [],
        "regions": [
            {"id": "z", "source_line_index": 0, "type": "text", "kind": "printed", "text": "different"},
        ],
        "source_line_count": 1,
    }
    report = measure.measure(
        labels,
        [_result_for(first), _result_for(second, case_id="E01")],
        {},
    )
    stability = report["D_label_key_stability"]
    assert stability["source_line_index_stable"] is False
    assert stability["unstable_cases"] == 1
    assert stability["unstable_details"][0]["id"] == "E01"


def test_cost_unknown_when_pricing_missing():
    labels = [
        {
            "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
            "expected_error": {"error_step": None, "error_type": None, "misconception_ids": []},
        }
    ]
    draft = {
        "problem": {"text": "", "latex_text": None, "confidence": None},
        "problem_bbox": None, "problem_kind": None,
        "steps": [], "regions": [], "source_line_count": 1,
    }
    report = measure.measure(labels, [_result_for(draft)], {})
    assert report["C_operations"]["cost_estimate_usd_total"] is None
    assert report["C_operations"]["cases_cost_unknown"] == 1


# ===========================================================================
# 5. 라벨 스키마 정합 (외부 dependency 없이)
# ===========================================================================

def _type_ok(value, expected) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, item) for item in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def _validate(instance, schema, path="$"):
    """라벨 스키마의 일부(필수·타입·enum·배열 길이)만 검증한다."""
    errors: list[str] = []
    if "type" in schema and not _type_ok(instance, schema["type"]):
        errors.append("{}: 타입 불일치 (기대 {})".format(path, schema["type"]))
        return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("{}: enum 위반 {}".format(path, schema["enum"]))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("{}: 필수 필드 '{}' 없음".format(path, key))
        for key, sub in (schema.get("properties") or {}).items():
            if key in instance:
                errors.extend(_validate(instance[key], sub, "{}.{}".format(path, key)))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("{}: 최소 {}개 필요".format(path, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("{}: 최대 {}개 허용".format(path, schema["maxItems"]))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(_validate(item, item_schema, "{}[{}]".format(path, index)))
    return errors


def test_labels_example_conforms_to_schema():
    """합성 예시 라벨이 스키마에 맞아야 한다."""
    schema = json.loads((EVAL_ROOT / "labels.schema.json").read_text(encoding="utf-8"))
    example = json.loads((EVAL_ROOT / "labels.example.json").read_text(encoding="utf-8"))
    errors = _validate(example, schema)
    assert not errors, errors


def test_labels_schema_accepts_empty_string_for_unreadable_step():
    """판정 불가 단계는 빈 문자열로 표현한다 (null 아님)."""
    schema = json.loads((EVAL_ROOT / "labels.schema.json").read_text(encoding="utf-8"))
    items = schema["properties"]["cases"]["items"]["properties"]["step_latex_texts"]
    assert items["items"]["type"] == "string"


# ===========================================================================
# 6. 클라이언트 계약
# ===========================================================================

def test_credentials_missing_raises_clear_error(monkeypatch):
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    monkeypatch.delenv("MATHPIX_APP_KEY", raising=False)
    with pytest.raises(client.MathpixError) as excinfo:
        client.credentials()
    assert "MATHPIX_APP_ID" in str(excinfo.value)


def test_request_body_contains_required_options():
    body = client.build_body("https://example.com/signed.jpg")
    assert body["src"] == "https://example.com/signed.jpg"
    assert body["include_line_data"] is True
    assert body["include_lines_json"] is True
    assert "latex_styled" in body["formats"]


def test_improve_mathpix_flag_is_never_true_by_default():
    """기본값은 언제나 `false` — 학생 과제 사진이 품질 개선에 쓰이지 않는다."""
    body = client.build_body("https://example.com/signed.jpg")
    assert body["metadata"]["improve_mathpix"] is not True
    assert body["metadata"]["improve_mathpix"] is False


def test_default_request_opts_out_of_data_improvement():
    """기본 요청 본문에 `metadata.improve_mathpix=false` 가 들어간다 (M13)."""
    body = client.build_body("https://example.com/signed.jpg")
    assert "metadata" in body
    assert body["metadata"]["improve_mathpix"] is False


def test_improve_mathpix_is_always_present_in_request_body():
    """플래그를 **생략하는 경로가 없어야 한다** — 생략하면 벤더 기본값(true) 이 된다."""
    bodies = [
        client.build_body("https://example.com/signed.jpg"),
        client.build_body("https://example.com/signed.jpg", confidence_threshold=0.85),
        client.build_body("https://example.com/signed.jpg", improve_mathpix=False),
    ]
    for body in bodies:
        assert "improve_mathpix" in body.get("metadata", {})
        assert body["metadata"]["improve_mathpix"] is False


def test_probe_request_body_opts_out():
    """`sandbox_probe.py` 가 보내는 요청도 `false` 다."""
    body = client.build_body(
        "https://example.com/signed.jpg", improve_mathpix=False
    )
    assert body["metadata"]["improve_mathpix"] is False


def test_improve_mathpix_true_is_rejected():
    """**opt-in 은 eval 하네스에서 불가능하다** — 참을 넣으면 거절된다.

    학생 과제 사진이 벤더 품질 개선에 사용될 수 있는 경로를 원천 차단한다.
    """
    with pytest.raises(ValueError) as excinfo:
        client.build_body("https://example.com/signed.jpg", improve_mathpix=True)
    message = str(excinfo.value)
    assert "improve_mathpix" in message
    assert "true" in message


def test_improve_mathpix_truthy_values_are_all_rejected():
    """참으로 평가되는 값은 전부 거절된다 (1, "true", "yes" 등)."""
    for value in (1, "true", "yes", 2, [1]):
        with pytest.raises(ValueError):
            client.build_body(
                "https://example.com/signed.jpg", improve_mathpix=value
            )


def test_no_request_path_can_produce_improve_mathpix_true():
    """**어떤 호출 형태에서도 `true` 가 만들어지지 않는다.**"""
    bodies = [
        client.build_body("https://example.com/signed.jpg"),
        client.build_body("https://example.com/signed.jpg", improve_mathpix=False),
        client.build_body("https://example.com/signed.jpg", improve_mathpix=None),
        client.build_body("https://example.com/signed.jpg", confidence_threshold=0.5),
    ]
    for body in bodies:
        assert body["metadata"]["improve_mathpix"] is False


def test_probe_does_not_claim_to_verify_effective_opt_out():
    """**응답으로 account-level pin 여부를 확인할 수 없다** — 검사 항목을 만들지 않는다.

    유효 설정은 콘솔 또는 Mathpix 지원팀으로만 확인한다. 응답 검사기에
    "opt-out 확인됨" 같은 항목을 두면 근거 없는 안심을 주게 된다.
    """
    summary = probe.inspect_payload({"line_data": [], "lines_json": {}})
    forbidden = ("improve_mathpix", "opt_out", "optout", "opt-out")
    for key in summary:
        assert not any(token in key for token in forbidden), key
