"""Mathpix 응답 → AI¹ `OcrResult` 변환 **프로토타입** (평가 하네스용).

**이 모듈은 M1·M2·M5·M6·M12 를 확정하지 않는다.**
아래 변환 규칙은 **후보 규칙** 이며, 실제 샌드박스 응답으로 검증하기 전에는
계약으로 채택하지 않는다. 결정안 §6 의 M1·M2·M5·M6·M12 를 참조할 것.

특히 **confidence 누락을 0.0 으로 대체하지 않는다** (지시: 샌드박스 결과 확인 전
어떤 값으로도 코드화하지 않는다). 누락이 있으면 그 항목을 **미확정 목록** 으로
분리해 보고하고, `OcrResult` 를 만들지 않는다.

PHASE 9-9B.2 규칙 (M5)
    - `is_printed` / `is_handwritten` 이 **둘 다 false** 인 콘텐츠 줄은
      **어떤 종류로도 배정하지 않는다**. `undecided_kind` 에 **보존**한다.
    - 그 줄이 **unreadable** 이더라도 **똑같이** `undecided_kind` 에 넣는다.
      (미인식이라고 해서 종류가 정해지는 것은 아니다)
    - `to_ocr_result()` 는 그 줄을 handwritten step 으로도, printed region 으로도
      **출력하지 않는다** — `UndecidedContractError` 로 **변환을 보류**한다.

이 파일은 `app/` 에 들어가지 않는다 — 벤더 응답 모양은 아직 확정되지 않았고,
평가 하네스는 회귀 테스트(`tests/`)와 분리한다.
"""
from __future__ import annotations

import re
from typing import Any

#: MMD 의 인라인/디스플레이 수식 구분자.
_INLINE_MATH = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_DISPLAY_MATH = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

#: **풀이 단계가 될 수 있는** 줄 타입 (text/math 계열).
#: 이 밖의 타입(도형·차트·페이지 정보 등)은 풀이 단계가 아니므로 제외한다.
SOLUTION_CONTENT_TYPES = frozenset(
    {
        "text",
        "math",
        "formula",
        "equation",
        "column",
        "code",
        "pseudocode",
    }
)

#: **과금 대상이 되는 텍스트 행** 의 타입.
#: 도형(diagram)·차트 등은 "줄"로 세지 않는다 — 12줄 초과 규칙은 이 수에 적용한다.
#:
#: **미확인**: 벤더 요금 문서에서 "line" 을 어떤 타입까지 포함하는지는 확인하지
#: 않았다. 여기서는 **보수적으로(비용을 낮게 잡지 않도록)** 텍스트 계열을 넓게
#: 센다. → 결정안 §6 M15.
BILLABLE_TEXT_ROW_TYPES = SOLUTION_CONTENT_TYPES

#: `error_id` 중 **텍스트가 아님이 확실한** 것 — 단계에서 제외한다.
#: `image_max_size` 는 여기에 **넣지 않는다** — 콘텐츠가 있을 수 있는 줄이기 때문이다.
#: `image_not_supported` 도 타입이 text/math 계열이면 **제외하지 않는다** (유실 금지).
NON_CONTENT_ERROR_IDS = frozenset({"image_no_content"})

#: 종류를 **결정할 수 없는** 이유 (M5).
KIND_UNDECIDED_BOTH_FALSE = "both-false"
KIND_UNDECIDED_BOTH_TRUE = "both-true"

#: M14 — `OcrResult` 에 **region 식별자가 없다.**
#: `OcrRegion` 에 `id` 필드가 없고 `OcrStep.region_id` 만 존재한다.
#: provider 의 line id 를 그대로 `region_id` 에 넣으면 **존재하지 않는 region 을
#: 참조**하게 된다 — 응답 안에서 resolve 되지 않는 dangling reference 다.
REGION_LINK_POLICY_NULL = "null"
REGION_LINK_POLICY_REQUIRE = "require"
REGION_LINK_POLICIES = (REGION_LINK_POLICY_NULL, REGION_LINK_POLICY_REQUIRE)


class UndecidedContractError(RuntimeError):
    """확정되지 않은 계약을 확정하려고 시도했다 (M1/M2/M5/M6/M12)."""


# ---------------------------------------------------------------------------
# 기하 — Mathpix `cnt` 다각형 → 축 정렬 bbox
# ---------------------------------------------------------------------------

def polygon_to_bbox(cnt: list) -> list[float] | None:
    """`line_data[].cnt` 폴리곤에서 축 정렬 bbox ``[x, y, width, height]`` 를 계산한다.

    Mathpix 는 꼭짓점을 회전된 사각형으로 주기도 하므로 (예: ``[[0,77],[0,32],...]``)
    순서에 의존하지 않고 min/max 로 잡는다.
    """
    if not isinstance(cnt, (list, tuple)) or len(cnt) < 2:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in cnt:
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except (TypeError, IndexError, ValueError):
            return None
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width < 0 or height < 0:
        return None
    return [min(xs), min(ys), width, height]


def _bbox_from_region(region: Any) -> list[float] | None:
    """`lines_json` 의 ``region`` → ``[x, y, width, height]``."""
    if not isinstance(region, dict):
        return None
    if not {"top_left_x", "top_left_y", "width", "height"} <= set(region):
        return None
    try:
        return [
            float(region["top_left_x"]),
            float(region["top_left_y"]),
            float(region["width"]),
            float(region["height"]),
        ]
    except (TypeError, ValueError):
        return None


def _bbox_of(line: dict, region_by_id: dict | None = None) -> list[float] | None:
    """줄 하나의 축 정렬 bbox.

    우선순위
      1. 줄에 ``region`` 이 붙어 있으면 그것
      2. ``cnt`` 폴리곤에서 계산
      3. ``lines_json`` 의 같은 ``id`` 항목
      4. 없으면 ``None``
    """
    bbox = _bbox_from_region(line.get("region"))
    if bbox is not None:
        return bbox
    bbox = polygon_to_bbox(line.get("cnt") or [])
    if bbox is not None:
        return bbox
    if region_by_id:
        match = region_by_id.get(line.get("id"))
        if match is not None:
            return _bbox_from_region(match.get("region"))
    return None


def _regions_by_id(payload: dict) -> dict:
    """``lines_json.lines`` 를 id 로 색인한다 (line_data 와 대응시키기 위함)."""
    lines_json = payload.get("lines_json") or {}
    out: dict = {}
    for region in lines_json.get("lines") or []:
        if isinstance(region, dict) and region.get("id") is not None:
            out[region["id"]] = region
    return out


# ---------------------------------------------------------------------------
# 후보 규칙 1 — MMD → (text, latex_text)
# ---------------------------------------------------------------------------

def split_mmd_to_text_and_latex(mmd: str) -> tuple[str, str | None]:
    """MMD 한 줄을 ``(text, latex_text)`` 로 분리한다. **후보 규칙(M1 미결).**

    후보 규칙
      1. 줄 전체가 수식 구분자로 감싸져 있으면 → ``latex_text`` = 구분자 안쪽,
         ``text`` = **구분자만 벗긴 내용**(내용 자체는 text 에도 남는다).
      2. 줄에 수식과 일반 텍스트가 섞여 있으면 → ``latex_text`` = 수식 부분만,
         ``text`` = MMD 원문 전체.
      3. 수식이 없으면 → ``latex_text`` = None, ``text`` = MMD 원문.

    **미정**: 한 줄에 수식이 여러 개일 때의 처리, 구문 텍스트 제거 여부,
    `latex_styled` 대 `data` 의 LaTeX 우선순위.
    """
    if not mmd:
        return "", None

    display = _DISPLAY_MATH.search(mmd)
    inline = _INLINE_MATH.search(mmd)

    if display and not inline:
        latex = display.group(1).strip()
        text = mmd[: display.start()] + display.group(1) + mmd[display.end():]
        return text.strip(), latex
    if inline and not display:
        latex = inline.group(1).strip()
        text = mmd[: inline.start()] + inline.group(1) + mmd[inline.end():]
        return text.strip(), latex
    if inline or display:
        parts = [m.group(1).strip() for m in _INLINE_MATH.finditer(mmd)]
        parts += [m.group(1).strip() for m in _DISPLAY_MATH.finditer(mmd)]
        return mmd.strip(), " ".join(p for p in parts if p)
    return mmd.strip(), None


# ---------------------------------------------------------------------------
# 후보 규칙 2 — line → step (M2·M6 미결)
# ---------------------------------------------------------------------------

def _is_content_line(line: dict) -> bool:
    """이 줄이 풀이 단계가 될 수 있는 콘텐츠인가."""
    line_type = line.get("type")
    if line_type is None:
        return True  # 타입 미상 → 유실하지 않는다
    return line_type in SOLUTION_CONTENT_TYPES


def _kind_of(line: dict) -> tuple[str | None, str | None]:
    """줄의 종류와, 정할 수 없었던 **사유**.

    ``is_printed` / `is_handwritten` 이 **둘 다 false** 면 종류를 정할 수 없다.
    **둘 다 true** 면 어느 쪽으로도 배정할 수 없다(둘 다 아니다).
    두 경우 모두 `kind=None` 이 되고 **어떤 종류로도 배정하지 않는다** (M5).
    """
    is_printed = bool(line.get("is_printed"))
    is_handwritten = bool(line.get("is_handwritten"))
    if is_printed and is_handwritten:
        return None, KIND_UNDECIDED_BOTH_TRUE
    if is_printed:
        return "printed", None
    if is_handwritten:
        return "handwritten", None
    return None, KIND_UNDECIDED_BOTH_FALSE


def classify_lines(lines: list[dict], region_by_id: dict | None = None) -> list[dict]:
    """줄을 **한 번만** 훑어 분류한다. (제외/미정 항목이 중복 집계되지 않게 한다)

    각 줄에 붙는 정보
        ``content``      풀이 단계가 될 수 있는가
        ``kind``         ``printed`` / ``handwritten`` / ``None``(미정 — 배정하지 않음)
        ``kind_reason``  미정이면 이유(``both-false`` / ``both-true``)
        ``unreadable``   벤더가 텍스트로 내지 않은 줄인가
        ``bbox``         축 정렬 bbox
        ``source_line_index`` 원본 ``line_data`` 순서 인덱스
    """
    classified: list[dict] = []
    for index, line in enumerate(lines):
        line_id = line.get("id")
        line_type = line.get("type")
        error_id = line.get("error_id")
        conversion_output = line.get("conversion_output")
        bbox = _bbox_of(line, region_by_id)

        content = _is_content_line(line)
        excluded_reason = None
        if not content:
            excluded_reason = "not-solution-content-type"
        elif error_id in NON_CONTENT_ERROR_IDS:
            excluded_reason = "no-content"

        kind, kind_reason = _kind_of(line)
        classified.append(
            {
                "source_line_index": index,
                "source_line_id": line_id,
                "type": line_type,
                "error_id": error_id,
                "conversion_output": conversion_output,
                "content": content,
                "excluded_reason": excluded_reason,
                "kind": kind,
                "kind_reason": kind_reason,
                "unreadable": conversion_output is False or bool(error_id),
                "bbox": bbox,
                "text": line.get("text"),
                "confidence": line.get("confidence"),
            }
        )
    return classified


def derive_steps(classified: list[dict], *, kind: str = "handwritten") -> list[dict]:
    """분류된 줄에서 단계 후보를 만든다. **후보 규칙(M2·M6 미결).**

    후보 규칙
      1. 풀이 단계가 될 수 없는 타입(도형·차트 등)은 제외된다 — 단,
         ``classify_lines`` 가 ``excluded_reason`` 로 **기록만 남긴다**.
      2. ``error_id`` 가 있어도 **콘텐츠 줄이면 유실하지 않는다** → 미인식 단계로 보존.
         ``image_max_size`` 는 콘텐츠가 있을 수 있으므로 반드시 보존한다.
      3. ``conversion_output:false`` 인 콘텐츠 줄 → 미인식 단계
         (``text="???"`` , ``latex_text=None`` — 추측 금지).
      4. 읽힌 단계는 위→아래(y), 같은 y 면 왼쪽→오른쪽(x) 순으로 정렬한다.
      5. **종류가 미정인 줄은 어느 kind 에도 넣지 않는다** (M5).
         unreadable 여부와 무관하다 — 미인식이라고 종류가 정해지는 것은 아니다.
      6. 종류가 정해진 줄은 그 종류의 버킷에 들어간다.
         (미인식 줄도 종류가 정해졌다면 해당 버킷에 들어간다 — 인쇄 줄을
         필기 단계로 몰아넣지 않는다)

    **미결**: 한 줄이 여러 단계일 때의 분리, 줄→단계 1:1 가정의 정당성,
    문제(인쇄)와 풀이(필기) 경계가 겹칠 때의 처리.
    """
    steps: list[dict] = []
    for item in classified:
        if not item["content"] or item["excluded_reason"]:
            continue
        # M5 — 종류를 정할 수 없는 줄은 **어느 버킷에도 넣지 않는다**.
        if item["kind"] is None:
            continue
        if item["kind"] != kind:
            continue

        if item["unreadable"]:
            steps.append(
                {
                    "step_no": 0,
                    "text": "???",
                    "latex_text": None,
                    "confidence": item["confidence"],
                    "unreadable": True,
                    "bbox": item["bbox"],
                    "kind": item["kind"],
                    "source_line_index": item["source_line_index"],
                    "source_line_id": item["source_line_id"],
                    "error_id": item["error_id"],
                }
            )
        else:
            text, latex = split_mmd_to_text_and_latex(item["text"] or "")
            steps.append(
                {
                    "step_no": 0,
                    "text": text,
                    "latex_text": latex,
                    "confidence": item["confidence"],
                    "unreadable": False,
                    "bbox": item["bbox"],
                    "kind": item["kind"],
                    "source_line_index": item["source_line_index"],
                    "source_line_id": item["source_line_id"],
                    "error_id": None,
                }
            )

    steps.sort(key=lambda item: (item["bbox"][1] if item["bbox"] else 0.0,
                                 item["bbox"][0] if item["bbox"] else 0.0))
    for number, item in enumerate(steps, start=1):
        item["step_no"] = number
    return steps


# ---------------------------------------------------------------------------
# 후보 규칙 3 — 응답 → 초안
# ---------------------------------------------------------------------------

def draft_from_response(payload: dict) -> dict:
    """Mathpix 응답에서 **초안** 을 만든다. 확정 계약이 아니다.

    반환 구조
        ``problem`` · ``problem_bbox`` · ``problem_kind`` · ``steps`` · ``regions``
        ``excluded_lines`` · ``undecided_kind`` · ``missing_confidence``
        ``line_kinds`` · ``step_region_links`` · ``source_line_count``
        ``billable_text_row_count`` · ``model_version`` · ``warnings``
    """
    lines = payload.get("line_data") or []
    region_by_id = _regions_by_id(payload)
    warnings: list[str] = []

    classified = classify_lines(lines, region_by_id)
    printed_steps = derive_steps(classified, kind="printed")
    handwritten_steps = derive_steps(classified, kind="handwritten")

    excluded_lines = [
        {
            "source_line_index": item["source_line_index"],
            "id": item["source_line_id"],
            "type": item["type"],
            "error_id": item["error_id"],
            "reason": item["excluded_reason"],
        }
        for item in classified
        if item["excluded_reason"]
    ]

    # M5 — 종류를 정할 수 없는 콘텐츠 줄은 **내용이 읽히지 않았어도** 여기에 넣는다.
    # (미인식이라는 사실이 종류를 정해 주지는 않는다)
    undecided_kind = [
        {
            "source_line_index": item["source_line_index"],
            "id": item["source_line_id"],
            "type": item["type"],
            "bbox": item["bbox"],
            "text": (item["text"] or "")[:80],
            "unreadable": item["unreadable"],
            "reason": item["kind_reason"],
        }
        for item in classified
        if item["content"]
        and not item["excluded_reason"]
        and item["kind"] is None
    ]

    # 줄(원본 줄 인덱스) 단위 종류 예측값.
    # 라벨의 `line_kinds` 와 대응시키기 위한 **안정적인 키**다.
    # provider 응답 id 는 반복 호출에서 안정성이 확인되지 않았으므로 **쓰지 않는다**.
    line_kinds = {
        str(item["source_line_index"]): item["kind"]
        for item in classified
        if item["content"] and item["kind"] is not None
    }

    # confidence 누락은 **단계가 된 줄만** 대상으로 센다.
    # 벤더가 애초에 내지 않은 줄(텍스트 아닌 영역)은 누락이 아니다.
    missing_confidence: list[dict] = []
    for step in printed_steps + handwritten_steps:
        if step.get("confidence") is None:
            missing_confidence.append(
                {
                    "where": "line_data[{}]".format(step["source_line_index"]),
                    "id": step.get("source_line_id"),
                    "text": (step.get("text") or "")[:80],
                    "unreadable": step.get("unreadable"),
                }
            )

    problem_lines = [step for step in printed_steps if not step["unreadable"]]
    problem_text = " ".join(step["text"] for step in problem_lines).strip()
    problem_latex = " ".join(
        step["latex_text"] for step in problem_lines if step.get("latex_text")
    ).strip() or None
    problem_confidences = [
        step["confidence"] for step in problem_lines if step.get("confidence") is not None
    ]
    problem_confidence = min(problem_confidences) if problem_confidences else None
    if problem_confidence is None:
        missing_confidence.append(
            {"where": "problem", "id": None, "text": problem_text[:80], "unreadable": None}
        )

    problem_bboxes = [step["bbox"] for step in problem_lines if step.get("bbox")]
    problem_bbox = _union_bbox(problem_bboxes)

    regions = []
    for index, item in enumerate(classified):
        region_text = item["text"]
        region_latex = None
        if item["unreadable"]:
            # 미인식 항목은 표식만 — 임의 내용·LaTeX 를 만들지 않는다
            region_text = "???"
            region_latex = None
        else:
            region_text, region_latex = split_mmd_to_text_and_latex(region_text or "")
        regions.append(
            {
                "id": item["source_line_id"],
                "source_line_index": index,
                "kind": item["kind"],
                "kind_undecided": item["kind"] is None,
                "kind_reason": item["kind_reason"],
                "bbox": item["bbox"],
                "text": region_text,
                "latex_text": region_latex,
                "confidence": item["confidence"],
                "unreadable": item["unreadable"],
                "excluded": (not item["content"]) or bool(item["excluded_reason"]),
            }
        )

    # 과금 대상 **텍스트 행** 수. 도형·차트 등은 세지 않는다.
    billable_text_row_count = sum(
        1 for item in classified if item["type"] in BILLABLE_TEXT_ROW_TYPES
    )

    # M14 — provider 의 line id 는 **eval 초안에 보존**한다.
    # `OcrResult` 에는 region 식별자가 없어 step→region 연결을 확정할 수 없다.
    # 연결을 "확정했다"고 말하지 않고, 미해결 상태로 기록한다.
    step_region_links = [
        {
            "step_no": step["step_no"],
            "source_line_index": step["source_line_index"],
            "provider_line_id": step.get("source_line_id"),
            "linked": False,
            "reason": "M14",
        }
        for step in handwritten_steps
    ]

    return {
        "problem": {
            "text": problem_text,
            "latex_text": problem_latex,
            "confidence": problem_confidence,
            "unreadable": not problem_lines,
        },
        "problem_bbox": problem_bbox,
        "problem_kind": _dominant_kind(printed_steps),
        "steps": handwritten_steps,
        "regions": regions,
        "excluded_lines": excluded_lines,
        "undecided_kind": undecided_kind,
        "missing_confidence": missing_confidence,
        "line_kinds": line_kinds,
        "step_region_links": step_region_links,
        "source_line_count": len(lines),
        "billable_text_row_count": billable_text_row_count,
        "model_version": str(payload.get("version") or "unknown"),
        "warnings": warnings,
    }


def _union_bbox(boxes: list[list[float]]) -> list[float] | None:
    """여러 bbox 의 합집합."""
    if not boxes:
        return None
    xs = [b[0] for b in boxes]
    ys = [b[1] for b in boxes]
    x2 = [b[0] + b[2] for b in boxes]
    y2 = [b[1] + b[3] for b in boxes]
    return [min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)]


def _dominant_kind(steps: list[dict]) -> str | None:
    """단계들에서 가장 많이 나온 종류 (없으면 None)."""
    kinds = [step["kind"] for step in steps if step.get("kind")]
    if not kinds:
        return None
    return max(set(kinds), key=kinds.count)


def to_ocr_result(draft: dict, *, region_link_policy: str = REGION_LINK_POLICY_NULL) -> Any:
    """초안을 우리 `OcrResult` 로 바꾼다.

    **확정되지 않은 항목이 하나라도 있으면 변환하지 않는다.**
      - ``missing_confidence`` 가 있으면 M12(누락 confidence 표현) 미결 → 값으로 코드화하지 않는다
      - ``undecided_kind`` 가 있으면 M5(인쇄/필기 판별) 미결 → 임의 배정하지 않는다
        (unreadable 인 줄도 여기에 포함된다)

    종류가 미정인 줄은 **handwritten step 으로도 printed region 으로도 출력하지 않는다.**

    M14 — region 연결 정책
        ``OcrResult`` 에는 **region 식별자가 없다**(`OcrRegion` 에 `id` 필드가 없다).
        provider 의 line id 를 그대로 `OcrStep.region_id` 에 넣으면 **존재하지 않는
        region 을 참조**하므로 출력하지 않는다.

        - ``REGION_LINK_POLICY_NULL`` (기본) → `region_id` 는 **null** 로 둔다.
          provider line id 는 **eval 초안**(`steps[].source_line_id`,
          `step_region_links[]`)에 보존하므로 측정용 정보는 잃지 않는다.
        - ``REGION_LINK_POLICY_REQUIRE`` → 연결이 필수라면 변환을 **보류**한다
          (`UndecidedContractError`, M14). 임의로 채우지 않는다.
    """
    if region_link_policy not in REGION_LINK_POLICIES:
        raise ValueError(
            "알 수 없는 region_link_policy: {!r} (허용: {})".format(
                region_link_policy, ", ".join(REGION_LINK_POLICIES)
            )
        )

    blockers: list[str] = []
    if draft.get("missing_confidence"):
        blockers.append(
            "M12(누락 confidence 표현) — confidence 누락 {}건: {}".format(
                len(draft["missing_confidence"]),
                ", ".join(
                    item["where"] for item in draft["missing_confidence"][:5]
                ),
            )
        )
    if draft.get("undecided_kind"):
        blockers.append(
            "M5(인쇄/필기 판별) — 종류를 정할 수 없는 줄 {}건: {}".format(
                len(draft["undecided_kind"]),
                ", ".join(
                    "line_data[{}]({}{})".format(
                        item["source_line_index"],
                        item.get("reason") or "?",
                        ", unreadable" if item.get("unreadable") else "",
                    )
                    for item in draft["undecided_kind"][:5]
                ),
            )
        )

    # M14 — 연결이 필수 정책이면, unresolved 인 step→region 연결이 있는 순간 보류한다.
    unresolved_links = [
        link for link in (draft.get("step_region_links") or [])
        if not link.get("linked")
    ]
    if region_link_policy == REGION_LINK_POLICY_REQUIRE and unresolved_links:
        blockers.append(
            "M14(region id) — OcrResult 에 region 식별자가 없어 step→region 연결을 "
            "확정할 수 없다. 임의로 채우지 않는다. 미해결 {}건: {}".format(
                len(unresolved_links),
                ", ".join(
                    "step_no {} (provider line id {})".format(
                        link.get("step_no"), link.get("provider_line_id")
                    )
                    for link in unresolved_links[:5]
                ),
            )
        )

    if blockers:
        raise UndecidedContractError(
            "미결 항목이 있어 OcrResult 로 변환할 수 없다. 값을 임의로 정하지 않는다. "
            + " | ".join(blockers)
        )
    from app.integration.ocr import OcrResult
    from app.schemas import OcrBBox, OcrProblem, OcrRegion, OcrStep

    def _bbox(values):
        if not values:
            return None
        return OcrBBox(
            x=values[0], y=values[1], width=values[2], height=values[3]
        )

    # M5 — 종류 미정인 줄이 region 으로 나가면 **종류를 추측**하게 된다.
    # 위에서 이미 차단했지만, 방어적으로 한 번 더 확인한다(추측 금지).
    for region in draft["regions"]:
        if region["bbox"] and not region.get("excluded") and region.get("kind") is None:
            raise UndecidedContractError(
                "M5(인쇄/필기 판별) — 종류가 미정인 region 이 있어 OcrResult 로 변환할 수 "
                "없다. 값을 임의로 정하지 않는다. (line_data[{}])".format(
                    region.get("source_line_index")
                )
            )

    return OcrResult(
        model_version=draft["model_version"],
        problem=OcrProblem(**draft["problem"]),
        regions=[
            OcrRegion(
                kind=region["kind"],
                bbox=_bbox(region["bbox"]),
                text=region.get("text"),
                latex_text=region.get("latex_text"),
                confidence=region["confidence"],
                unreadable=bool(region.get("unreadable")),
            )
            for region in draft["regions"]
            # **풀이 콘텐츠가 아닌 영역(도형 등)은 제외** — 그 영역은
            # `excluded_lines` 에 기록된다. 벤더가 confidence 를 안 준 영역을
            # 우리 계약(필수 float)에 억지로 넣지 않기 위한 것이기도 하다.
            if region["bbox"] and not region.get("excluded")
        ],
        steps=[
            OcrStep(
                step_no=step["step_no"],
                text=step["text"],
                latex_text=step["latex_text"],
                confidence=step["confidence"],
                unreadable=step["unreadable"],
                # M14 — `OcrResult` 에 region 식별자가 없으므로 **null** 로 둔다.
                # provider line id 는 eval 초안(`step_region_links`)에 보존되어 있다.
                region_id=None,
            )
            for step in draft["steps"]
        ],
    )
