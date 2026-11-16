"""Google Vision 응답 → **평가용 중간 구조** 변환.

**의미 해석을 하지 않는다.** 이 단계는 **raw text OCR 까지만** 한다.

하지 **않는** 것 (결정안 §1 과 동일):
  - `problem` / `solution_steps` 로 나누지 않는다
  - printed / handwritten 를 추정하지 않는다
  - LaTeX 를 만들지 않는다
  - Vision 은 의미상 문제/풀이 분리를 보장하지 않는다 — 따라서 이 구조로는
    **OCR 층만** 평가할 수 있다

담는 것:
  - 원문 텍스트 (`full_text`)
  - block · paragraph · word **순서** (Vision 이 준 순서를 그대로)
  - bounding polygon (Vision 이 준 값을 그대로)
  - confidence (Vision 이 준 값을 그대로)

`lines` 는 **파생** 값이다 — word 를 수직 위치로 묶은 것뿐이고 의미 해석이
아니므로 `derived: true` 로 표시한다.
"""
from __future__ import annotations

from typing import Any

from google_vision_client import VisionResponseError, redact_payload, uri_ref

#: 줄 파생에 쓰는 허용 오차 배수 (단어 높이의 비율).
LINE_TOLERANCE_RATIO = 0.6


def _polygon(raw: Any) -> list[list[float]]:
    """Vision bounding polygon → ``[[x, y], ...]``."""
    if not isinstance(raw, dict):
        return []
    vertices = raw.get("vertices")
    if not isinstance(vertices, list):
        return []
    out: list[list[float]] = []
    for vertex in vertices:
        if not isinstance(vertex, dict):
            continue
        try:
            out.append([float(vertex["x"]), float(vertex["y"])])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _confidence(raw: Any) -> float | None:
    """confidence 를 그대로 옮긴다. 없으면 None (0 으로 만들지 않는다)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _bbox(polygon: list[list[float]]) -> list[float] | None:
    """polygon → ``[x, y, width, height]`` (파생)."""
    if not polygon:
        return None
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def _word_text(word: dict) -> str:
    """단어 텍스트를 조립한다.

    **공식 Vision 응답**은 word 에 `text` 가 없고 **`symbols[].text`** 를 준다.
    구버전(`text` 직접) 형태는 fallback 으로만 처리한다.
    """
    symbols = word.get("symbols")
    if isinstance(symbols, list) and symbols:
        parts = []
        for symbol in symbols:
            if isinstance(symbol, dict):
                text = symbol.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(symbol, str):
                parts.append(symbol)
        if parts:
            return "".join(parts)
    legacy = word.get("text")
    return legacy if isinstance(legacy, str) else ""


def _word_property(word: dict) -> dict | None:
    """공식 `Word.property` (감지 언어 등) 를 그대로 보존한다."""
    prop = word.get("property")
    return prop if isinstance(prop, dict) else None


def _parse_words(raw_words: Any, start_index: int = 0) -> tuple[list[dict], int]:
    """공식 Vision `Word` 구조를 파싱한다.

    공식 필드: ``property`` · ``boundingBox`` · ``symbols`` · ``confidence``.
    ``confidence`` 가 **있으면 보존**하고, 실제로 **없을 때만** `None` 을 준다
    (0 으로 만들지 않는다).

    ``word_index`` 는 **문서 전체에서 유일**하다 (단락·블록 경계에서도 이어진다)
    — 줄의 ``word_indexes`` 가 서로 겹치지 않게 하기 위함.
    """
    out: list[dict] = []
    index = start_index
    for word in raw_words or []:
        if not isinstance(word, dict):
            continue
        polygon = _polygon(word.get("boundingBox"))
        out.append(
            {
                "word_index": index,
                "text": _word_text(word),
                "confidence": _confidence(word.get("confidence")),
                "property": _word_property(word),
                "bounding_polygon": polygon,
                "bounding_box": _bbox(polygon),
            }
        )
        index += 1
    return out, index


def _parse_paragraphs(raw_paragraphs: Any, start_index: int = 0) -> tuple[list[dict], int]:
    out: list[dict] = []
    index = start_index
    for paragraph in raw_paragraphs or []:
        if not isinstance(paragraph, dict):
            continue
        polygon = _polygon(paragraph.get("boundingBox"))
        words, index = _parse_words(paragraph.get("words"), index)
        out.append(
            {
                "paragraph_index": len(out),
                "confidence": _confidence(paragraph.get("confidence")),
                "bounding_polygon": polygon,
                "bounding_box": _bbox(polygon),
                "words": words,
            }
        )
    return out, index


def _parse_blocks(raw_blocks: Any, start_index: int = 0) -> tuple[list[dict], int]:
    out: list[dict] = []
    index = start_index
    for block in raw_blocks or []:
        if not isinstance(block, dict):
            continue
        # **공식 Vision 응답**은 block 에 `boundingBox` 하나를 준다.
        # 구버전 `bboxes` 배열은 fallback 으로만 처리한다.
        polygon = _polygon(block.get("boundingBox"))
        if not polygon:
            for candidate in block.get("bboxes") or []:
                polygon = _polygon(candidate)
                if polygon:
                    break
        paragraphs, index = _parse_paragraphs(block.get("paragraphs"), index)
        out.append(
            {
                "block_index": len(out),
                "block_type": block.get("blockType"),
                "confidence": _confidence(block.get("confidence")),
                "bounding_polygon": polygon,
                "bounding_box": _bbox(polygon) if polygon else None,
                "paragraphs": paragraphs,
            }
        )
    return out, index


def _parse_pages(raw_pages: Any) -> list[dict]:
    out: list[dict] = []
    index = 0
    for page in raw_pages or []:
        if not isinstance(page, dict):
            continue
        blocks, index = _parse_blocks(page.get("blocks"), index)
        out.append(
            {
                "page_index": len(out),
                "width": page.get("width"),
                "height": page.get("height"),
                "confidence": _confidence(page.get("confidence")),
                "blocks": blocks,
            }
        )
    return out


def derive_lines(words: list[dict]) -> list[dict]:
    """단어를 **수직 위치**로 묶어 줄을 만든다 (파생 — 의미 해석 아님).

    Vision 의 `DOCUMENT_TEXT_DETECTION` 은 줄 단위를 주지 않는다. 평가에서
    줄 단위 비교가 필요하므로, word 의 윗변 y 를 기준으로 묶을 뿐이다.
    """
    usable = [w for w in words if w.get("bounding_box")]
    if not usable:
        return []
    heights = [
        w["bounding_box"][3] for w in usable if w["bounding_box"][3] > 0
    ]
    heights.sort()
    median_height = heights[len(heights) // 2] if heights else 0.0
    tolerance = median_height * LINE_TOLERANCE_RATIO

    def top(word: dict) -> float:
        return word["bounding_box"][1]

    def left(word: dict) -> float:
        return word["bounding_box"][0]

    ordered = sorted(usable, key=lambda w: (top(w), left(w)))
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_top = None
    for word in ordered:
        if current_top is None or (top(word) - current_top) <= tolerance:
            current.append(word)
            current_top = top(word) if current_top is None else current_top
        else:
            groups.append(current)
            current = [word]
            current_top = top(word)
    if current:
        groups.append(current)

    lines: list[dict] = []
    for index, group in enumerate(groups):
        group = sorted(group, key=left)
        confidences = [
            w["confidence"] for w in group if w.get("confidence") is not None
        ]
        xs = [w["bounding_box"][0] for w in group]
        ys = [w["bounding_box"][1] for w in group]
        bottoms = [
            w["bounding_box"][1] + w["bounding_box"][3] for w in group
        ]
        rights = [
            w["bounding_box"][0] + w["bounding_box"][2] for w in group
        ]
        lines.append(
            {
                "line_index": index,
                "text": " ".join(w["text"] for w in group if w.get("text")),
                "confidence": (
                    sum(confidences) / len(confidences) if confidences else None
                ),
                "bounding_box": [
                    min(xs), min(ys), max(rights) - min(xs), max(bottoms) - min(ys)
                ],
                "word_indexes": [w["word_index"] for w in group],
            }
        )
    return lines


def parse_response(
    payload: dict,
    *,
    source_image: str | None = None,
    language_hint: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Vision 응답 → 평가용 중간 구조.

    **문제/풀이 분리·인쇄/필기·LaTeX 는 만들지 않는다.**

    ``source_image`` 는 presigned URL 일 수 있으므로 **저장하지 않는다** —
    `source_image_ref` (안정적·비가역 참조) 만 남긴다.
    """
    if not isinstance(payload, dict):
        raise VisionResponseError("응답이 dict 가 아니다")
    responses = payload.get("responses")
    if not isinstance(responses, list) or not responses:
        raise VisionResponseError("응답에 responses 배열이 없다")
    first = responses[0]
    if not isinstance(first, dict):
        raise VisionResponseError("responses[0] 이 dict 가 아니다")
    if first.get("error"):
        raise VisionResponseError(
            "Vision 이 오류를 반환했다: {}".format(first["error"])
        )

    full = first.get("fullTextAnnotation")
    if not isinstance(full, dict):
        # `fullTextAnnotation` 이 없으면 `textAnnotations` 로 대체한다.
        annotations = first.get("textAnnotations")
        if not isinstance(annotations, list) or not annotations:
            raise VisionResponseError(
                "응답에 fullTextAnnotation 도 textAnnotations 도 없다"
            )
        annotation = annotations[0] if isinstance(annotations[0], dict) else {}
        polygon = _polygon(annotation.get("boundingPoly"))
        full = {
            "text": annotation.get("description") or "",
            "pages": [
                {
                    "width": None,
                    "height": None,
                    "confidence": None,
                    "blocks": [
                        {
                            "blockType": "TEXT",
                            "confidence": None,
                            "boundingBox": {"vertices": []},
                            "paragraphs": [],
                        }
                    ],
                }
            ],
            "_text_annotation_polygon": polygon,
        }

    pages = _parse_pages(full.get("pages"))
    all_words: list[dict] = []
    for page in pages:
        for block in page["blocks"]:
            for paragraph in block["paragraphs"]:
                all_words.extend(paragraph["words"])

    warnings: list[str] = []
    if not pages or not any(page["blocks"] for page in pages):
        warnings.append("block 이 하나도 없다 — 텍스트를 못 읽었을 수 있다")

    structure = {
        "run_id": run_id,
        "source_image_ref": uri_ref(source_image) if source_image else None,
        "source_image_ref_is_stable_id": False,
        "source_image_ref_note": (
            "source_image_ref 는 **URI 전체의 해시**다 — 같은 이미지여도 presigned URL 이 "
            "새로 발급되면 값이 달라질 수 있다. **이미지의 안정 ID 가 아니며 비교·대응 키로 "
            "사용하지 않는다.** 안정적인 대응 키는 manifest 의 `id` 다."
        ),
        "language_hint": language_hint,
        "feature": "DOCUMENT_TEXT_DETECTION",
        "full_text": full.get("text") or "",
        "page_count": len(pages),
        "pages": pages,
        "word_count": len(all_words),
        "lines": derive_lines(all_words),
        "line_count": 0,
        "warnings": warnings,
        "semantics": {
            "problem": None,
            "solution_steps": None,
            "printed_handwritten": None,
            "latex": None,
            "note": (
                "이 단계는 raw text OCR 까지만 한다. 문제/풀이 구분·인쇄/필기·LaTeX 는 "
                "**추정하지 않는다** — Vision 은 의미상 문제/풀이 분리를 보장하지 않는다."
            ),
        },
    }
    structure["line_count"] = len(structure["lines"])
    # 방어적으로 — Vision 응답이 URI 를 반사했더라도 결과 구조에 남지 않게 한다.
    if source_image:
        structure = redact_payload(structure, None, [source_image])
    return structure
