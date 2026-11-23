"""Google Vision 데모 계약 테스트 (PHASE 9-9C.1 · 9-9C.2).

**실제 Google Vision API 는 호출하지 않는다.** 전부 fake 응답/transport 를 쓴다.

fixture 는 **공식 Vision 응답 구조**를 따른다:
  - block 은 ``boundingBox`` 하나
  - word 는 ``symbols[].text`` (word 에 ``text`` 가 없다)
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT.parent))

import google_vision_adapter as adapter  # noqa: E402
import google_vision_client as client  # noqa: E402
import google_vision_probe as probe  # noqa: E402

FAKE_TOKEN = "ya29.FAKE-ACCESS-TOKEN-SHOULD-NEVER-APPEAR"

#: 테스트용 정책 env — 실제 무료 한도 값이 아니다. 코드는 기본값을 갖지 않으므로
#: 실제 호출 경로 테스트에는 이렇게 "확인된" 값을 주입해야 한다.
TEST_POLICY_ENV = {
    "AI1_OCR_BILLING_UNIT": "document_text_detection",
    "AI1_OCR_MONTHLY_FREE_QUOTA": "100",
    "AI1_OCR_MONTHLY_CALL_CAP": "10",
    "AI1_OCR_FREE_QUOTA_CONFIRMED": "1",
}

#: 이전 달(월별 집계에서 제외)을 흉내 내는 예약 타임스탬프.
PAST_TIMESTAMP = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _quota_env(monkeypatch):
    for key, value in TEST_POLICY_ENV.items():
        monkeypatch.setenv(key, value)

#: 서명·토큰이 들어간 presigned URL (비노출 검증용).
PRESIGNED_URL = (
    "https://storage.googleapis.com/demo-bucket/E01.jpg"
    "?X-Goog-Signature=DEADBEEFSIGNATURE"
    "&X-Goog-Credential=SECRETTOKEN%2Fscope"
    "&X-Goog-Expires=900"
)


# ---------------------------------------------------------------------------
# fake Vision 응답 (공식 구조)
# ---------------------------------------------------------------------------

def _vertices(x, y, w, h):
    return [
        {"x": x, "y": y}, {"x": x + w, "y": y},
        {"x": x + w, "y": y + h}, {"x": x, "y": y + h},
    ]


def _word(text, x, y, w=120, h=30, confidence=0.98, language="ko"):
    """공식 Vision ``Word`` 구조.

    공식 필드: ``property`` · ``boundingBox`` · ``symbols`` · ``confidence``.
    (``mid`` 는 공식 필드가 아니다)
    """
    symbols = []
    offset = x
    for char in text:
        symbols.append(
            {
                "text": char,
                "boundingBox": {"vertices": _vertices(offset, y, 12, h)},
                "property": {
                    "detectedLanguages": [
                        {"languageCode": language, "confidence": confidence}
                    ]
                },
            }
        )
        offset += 12
    return {
        "boundingBox": {"vertices": _vertices(x, y, w, h)},
        "symbols": symbols,
        "confidence": confidence,
        "property": {
            "detectedLanguages": [
                {"languageCode": language, "confidence": confidence}
            ]
        },
    }


def _word_without_confidence(text, x, y, w=120, h=30):
    """``confidence`` 가 실제로 빠진 word."""
    word = _word(text, x, y, w, h)
    word.pop("confidence")
    return word


def _vision_response():
    """문서 2블록 · 3단락 · 5단어짜리 fake 응답 (공식 구조)."""
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "text": "2(x-3)=6\n2x=12\nx=6",
                    "pages": [
                        {
                            "width": 1000,
                            "height": 2000,
                            "confidence": 0.95,
                            "blocks": [
                                {
                                    "blockType": "TEXT",
                                    "boundingBox": {"vertices": _vertices(10, 20, 890, 100)},
                                    "confidence": 0.96,
                                    "paragraphs": [
                                        {
                                            "boundingBox": {
                                                "vertices": _vertices(10, 20, 890, 50)
                                            },
                                            "confidence": 0.97,
                                            "words": [
                                                _word("2(x-3)=6", 10, 20),
                                                _word("2x=12", 10, 80),
                                            ],
                                        },
                                        {
                                            "boundingBox": {
                                                "vertices": _vertices(10, 120, 890, 40)
                                            },
                                            "confidence": 0.93,
                                            "words": [
                                                _word("x=6", 10, 120),
                                            ],
                                        },
                                    ],
                                },
                                {
                                    "blockType": "TEXT",
                                    "boundingBox": {"vertices": _vertices(10, 200, 390, 40)},
                                    "confidence": 0.90,
                                    "paragraphs": [
                                        {
                                            "boundingBox": {
                                                "vertices": _vertices(10, 200, 390, 40)
                                            },
                                            "confidence": 0.90,
                                            "words": [
                                                _word("답:", 10, 200),
                                                _word("6", 100, 200),
                                            ],
                                        },
                                    ],
                                },
                            ],
                        }
                    ],
                }
            }
        ]
    }


def _legacy_vision_response():
    """구버전 형태 — block 은 ``bboxes`` 배열, word 에 ``text`` 직접."""
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "text": "legacy",
                    "pages": [
                        {
                            "width": 100,
                            "height": 200,
                            "blocks": [
                                {
                                    "blockType": "TEXT",
                                    "bboxes": [{"vertices": _vertices(1, 2, 30, 40)}],
                                    "paragraphs": [
                                        {
                                            "boundingBox": {
                                                "vertices": _vertices(1, 2, 30, 40)
                                            },
                                            "words": [
                                                {
                                                    "text": "legacy",
                                                    "boundingBox": {
                                                        "vertices": _vertices(1, 2, 30, 40)
                                                    },
                                                    "confidence": 0.5,
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            }
        ]
    }


class FakeTransport:
    """가짜 전송 계층 — 실제 네트워크 호출 없음."""

    def __init__(self, payload=None, error=None, delay=None):
        self.calls = []
        self.payload = payload if payload is not None else _vision_response()
        self.error = error
        self.delay = delay

    def __call__(self, body, *, token, timeout):
        self.calls.append({"body": body, "token": token, "timeout": timeout})
        if self.delay:
            import time as _time
            _time.sleep(self.delay)
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.payload)), 0.42


def _images(count=2, uri=None):
    base = uri or "gs://demo-bucket/g{:02d}.png"
    return [
        {"id": "G{:02d}".format(index),
         "uri": base.format(index) if "{}" in base or "{" in base else base}
        for index in range(1, count + 1)
    ]


# ===========================================================================
# 1. 공식 응답 구조 파싱
# ===========================================================================

def test_word_text_is_assembled_from_symbols():
    """word 텍스트는 `symbols[].text` 에서 조립한다."""
    structure = adapter.parse_response(_vision_response())
    words = structure["pages"][0]["blocks"][0]["paragraphs"][0]["words"]
    assert words[0]["text"] == "2(x-3)=6"
    assert words[1]["text"] == "2x=12"
    assert structure["full_text"] == "2(x-3)=6\n2x=12\nx=6"


def test_block_bounding_box_is_preserved():
    """block 의 `boundingBox` polygon 이 보존된다."""
    structure = adapter.parse_response(_vision_response())
    block = structure["pages"][0]["blocks"][0]
    assert block["bounding_polygon"] == [
        [10.0, 20.0], [900.0, 20.0], [900.0, 120.0], [10.0, 120.0]
    ]
    assert block["bounding_box"] == [10.0, 20.0, 890.0, 100.0]
    assert block["block_type"] == "TEXT"
    assert block["confidence"] == pytest.approx(0.96)
    # 두 번째 블록도 각자의 polygon 을 가진다
    second = structure["pages"][0]["blocks"][1]
    assert second["bounding_polygon"][0] == [10.0, 200.0]


def test_confidence_is_kept_and_missing_is_not_invented():
    """confidence 는 그대로 옮기고, 실제로 빠진 경우에만 None (0 으로 만들지 않는다)."""
    structure = adapter.parse_response(_vision_response())
    page = structure["pages"][0]
    assert page["confidence"] == pytest.approx(0.95)
    assert page["blocks"][0]["confidence"] == pytest.approx(0.96)
    assert page["blocks"][0]["paragraphs"][0]["confidence"] == pytest.approx(0.97)
    # **word confidence 가 있으면 보존**한다
    word = page["blocks"][0]["paragraphs"][0]["words"][0]
    assert word["confidence"] == pytest.approx(0.98)
    # 공식 `Word.property` 도 보존된다
    assert word["property"]["detectedLanguages"][0]["languageCode"] == "ko"


def test_word_confidence_absent_returns_none():
    """word confidence 가 **실제로 빠졌으면** None 이다 (0 으로 만들지 않는다)."""
    payload = _vision_response()
    payload["responses"][0]["fullTextAnnotation"]["pages"][0]["blocks"][0][
        "paragraphs"
    ][0]["words"][0] = _word_without_confidence("2(x-3)=6", 10, 20)
    structure = adapter.parse_response(payload)
    word = structure["pages"][0]["blocks"][0]["paragraphs"][0]["words"][0]
    assert word["confidence"] is None
    assert word["text"] == "2(x-3)=6"


def test_line_confidence_is_derived_from_word_values():
    """파생 line confidence 는 word 값에서 계산된다."""
    payload = _vision_response()
    blocks = payload["responses"][0]["fullTextAnnotation"]["pages"][0]["blocks"]
    # 두 번째 블록의 마지막 단락 = "답:" + "6" (같은 줄)
    words = blocks[1]["paragraphs"][0]["words"]
    words[0]["confidence"] = 0.90
    words[1]["confidence"] = 0.70
    structure = adapter.parse_response(payload)
    line = structure["lines"][-1]
    assert len(line["word_indexes"]) == 2
    assert line["confidence"] == pytest.approx(0.80, abs=1e-9)


def test_word_index_is_unique_across_the_document():
    """`word_index` 는 문서 전체에서 유일하다 (단락·블록 경계에서도 이어진다)."""
    structure = adapter.parse_response(_vision_response())
    indexes = []
    for page in structure["pages"]:
        for block in page["blocks"]:
            for paragraph in block["paragraphs"]:
                indexes.extend(word["word_index"] for word in paragraph["words"])
    assert indexes == sorted(indexes)
    assert len(indexes) == len(set(indexes))
    assert indexes == list(range(len(indexes)))
    # 줄의 word_indexes 도 서로 겹치지 않는다
    line_indexes = [i for line in structure["lines"] for i in line["word_indexes"]]
    assert len(line_indexes) == len(set(line_indexes))


def test_line_confidence_is_none_when_no_word_has_it():
    """word confidence 가 하나도 없으면 line confidence 도 None."""
    payload = _vision_response()
    words = payload["responses"][0]["fullTextAnnotation"]["pages"][0]["blocks"][0][
        "paragraphs"
    ][0]["words"]
    for word in words:
        word.pop("confidence", None)
    structure = adapter.parse_response(payload)
    assert structure["lines"][0]["confidence"] is None


def test_word_property_is_preserved():
    """공식 `Word.property` (감지 언어) 가 보존된다."""
    structure = adapter.parse_response(_vision_response())
    word = structure["pages"][0]["blocks"][0]["paragraphs"][0]["words"][0]
    assert word["property"]["detectedLanguages"][0]["languageCode"] == "ko"
    assert word["property"]["detectedLanguages"][0]["confidence"] == pytest.approx(0.98)


def test_non_official_mid_field_is_not_carried_over():
    """공식 구조가 아닌 `mid` 는 결과에 싣지 않는다."""
    payload = _vision_response()
    words = payload["responses"][0]["fullTextAnnotation"]["pages"][0]["blocks"][0][
        "paragraphs"
    ][0]["words"]
    words[0]["mid"] = "legacy-mid-value"
    structure = adapter.parse_response(payload)
    word = structure["pages"][0]["blocks"][0]["paragraphs"][0]["words"][0]
    assert "mid" not in word
    assert "legacy-mid-value" not in json.dumps(structure, ensure_ascii=False)


def test_legacy_response_shape_still_parsed():
    """구버전 형태(bboxes + word.text)도 fallback 으로 읽는다."""
    structure = adapter.parse_response(_legacy_vision_response())
    block = structure["pages"][0]["blocks"][0]
    assert block["bounding_polygon"] == [
        [1.0, 2.0], [31.0, 2.0], [31.0, 42.0], [1.0, 42.0]
    ]
    assert block["paragraphs"][0]["words"][0]["text"] == "legacy"


def test_derived_lines_group_words_by_position():
    structure = adapter.parse_response(_vision_response())
    assert structure["line_count"] == len(structure["lines"])
    assert structure["line_count"] >= 1
    first = structure["lines"][0]
    assert first["line_index"] == 0
    assert first["text"]
    assert len(first["bounding_box"]) == 4


def test_structure_does_not_infer_problem_steps_or_latex():
    """문제/풀이·인쇄/필기·LaTeX 를 **추정하지 않는다**."""
    structure = adapter.parse_response(_vision_response())
    semantics = structure["semantics"]
    assert semantics["problem"] is None
    assert semantics["solution_steps"] is None
    assert semantics["printed_handwritten"] is None
    assert semantics["latex"] is None
    for key in ("problem", "solution_steps", "printed_handwritten", "latex", "steps"):
        assert key not in structure
    outside = {k: v for k, v in structure.items() if k != "semantics"}
    blob = json.dumps(outside, ensure_ascii=False)
    for token in ('"problem"', '"solution_steps"', '"printed_handwritten"', '"latex"'):
        assert token not in blob


# ===========================================================================
# 2. presigned URL 비노출
# ===========================================================================

def test_uri_is_not_stored_in_result_structure():
    """URI 자체는 구조에 남지 않는다 — 참조만 남는다."""
    structure = adapter.parse_response(
        _vision_response(), source_image=PRESIGNED_URL
    )
    blob = json.dumps(structure, ensure_ascii=False)
    assert PRESIGNED_URL not in blob
    assert "DEADBEEFSIGNATURE" not in blob
    assert "SECRETTOKEN" not in blob
    assert "X-Goog-Signature" not in blob
    # 안정적이고 되돌릴 수 없는 참조
    assert structure["source_image_ref"].startswith("sha256:")
    assert structure["source_image_ref"] == client.uri_ref(PRESIGNED_URL)


def test_uri_is_not_in_files_summary_or_logs(tmp_path):
    """결과 파일·요약·실행 로그 어디에도 URI 가 없다."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    images = [
        {"id": "G01", "uri": PRESIGNED_URL},
        {"id": "G02", "uri": PRESIGNED_URL.replace("E01", "E02")},
    ]
    summary = probe.run_probe(
        images, transport=transport, local_dir=local,
        dry_run=False, max_calls=2, token=FAKE_TOKEN,
    )
    for path in list(local.rglob("*.json")) + list(local.rglob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        assert PRESIGNED_URL not in text
        assert "DEADBEEFSIGNATURE" not in text
        assert "SECRETTOKEN" not in text
        assert "X-Goog-Signature" not in text
    blob = json.dumps(summary, ensure_ascii=False)
    assert PRESIGNED_URL not in blob
    assert "DEADBEEFSIGNATURE" not in blob
    assert "SECRETTOKEN" not in blob


def test_uri_is_not_in_failure_message(tmp_path):
    """실패 메시지에서도 URI 가 제거된다."""
    transport = FakeTransport(
        error=RuntimeError("request failed for " + PRESIGNED_URL)
    )
    local = tmp_path / "gv"
    summary = probe.run_probe(
        [{"id": "G01", "uri": PRESIGNED_URL}], transport=transport,
        local_dir=local, dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    assert summary["failed"] == 1
    blob = json.dumps(summary, ensure_ascii=False)
    assert PRESIGNED_URL not in blob
    assert "DEADBEEFSIGNATURE" not in blob
    assert "<redacted-image-uri>" in blob


def test_uri_query_only_in_error_message_is_redacted(tmp_path):
    """오류 메시지에 **쿼리만** 있어도 치환된다 (서명·토큰 노출 방지)."""
    query_only = "X-Goog-Signature=DEADBEEFSIGNATURE&X-Goog-Credential=SECRETTOKEN%2Fscope"
    transport = FakeTransport(error=RuntimeError("403 for " + query_only))
    local = tmp_path / "gv"
    summary = probe.run_probe(
        [{"id": "G01", "uri": PRESIGNED_URL}], transport=transport,
        local_dir=local, dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    assert summary["failed"] == 1
    blob = json.dumps(summary, ensure_ascii=False)
    assert "DEADBEEFSIGNATURE" not in blob
    assert "SECRETTOKEN" not in blob
    assert "<redacted-image-uri>" in blob
    for path in local.rglob("*.json"):
        assert "DEADBEEFSIGNATURE" not in path.read_text(encoding="utf-8")


def test_uri_ref_is_stable_and_not_reversible():
    """같은 URI 는 같은 참조, 다른 URI 는 다른 참조."""
    assert client.uri_ref(PRESIGNED_URL) == client.uri_ref(PRESIGNED_URL)
    assert client.uri_ref(PRESIGNED_URL) != client.uri_ref(PRESIGNED_URL + "&x=1")
    assert PRESIGNED_URL not in client.uri_ref(PRESIGNED_URL)


def test_uri_ref_is_not_a_stable_image_id():
    """`source_image_ref` 는 URI 전체의 해시 — presigned URL 이 바뀌면 값이 달라진다.

    따라서 **이미지의 안정 ID 가 아니며** 비교·대응 키로 쓰면 안 된다.
    안정적인 대응 키는 manifest 의 `id` 다.
    """
    rotated = PRESIGNED_URL.replace("DEADBEEFSIGNATURE", "ROTATEDSIGNATURE")
    assert client.uri_ref(PRESIGNED_URL) != client.uri_ref(rotated)

    structure = adapter.parse_response(
        _vision_response(), source_image=PRESIGNED_URL
    )
    assert structure["source_image_ref_is_stable_id"] is False
    note = structure["source_image_ref_note"]
    assert "비교" in note          # 비교 키로 쓰지 말라
    assert "대응 키" in note       # 대응 키는 manifest 의 id
    assert "sha256" in structure["source_image_ref"]


def test_manifest_id_is_the_correlation_key(tmp_path):
    """대응 키는 manifest 의 `id` 다 (URI 해시가 아니다)."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        [{"id": "G07", "uri": PRESIGNED_URL}], transport=transport,
        local_dir=local, dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    row = summary["results"][0]
    assert row["id"] == "G07"
    assert row["source_image_ref"].startswith("sha256:")


def test_raw_response_with_context_uri_is_redacted_before_saving(tmp_path):
    """Vision 응답의 `context.uri` 로 presigned URL 이 들어와도 남지 않는다."""
    payload = _vision_response()
    payload["responses"][0]["context"] = {"uri": PRESIGNED_URL}
    transport = FakeTransport(payload=payload)
    local = tmp_path / "gv"
    summary = probe.run_probe(
        [{"id": "G01", "uri": PRESIGNED_URL}], transport=transport,
        local_dir=local, dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    for path in list(local.rglob("*.json")) + list(local.rglob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        assert PRESIGNED_URL not in text
        assert "DEADBEEFSIGNATURE" not in text
        assert "SECRETTOKEN" not in text
        assert "X-Goog-Signature" not in text
    blob = json.dumps(summary, ensure_ascii=False)
    assert PRESIGNED_URL not in blob
    assert "DEADBEEFSIGNATURE" not in blob
    # raw 파일에는 redacted 표시가 남는다
    raw_path = Path(summary["results"][0]["raw_response_file"])
    raw_text = raw_path.read_text(encoding="utf-8")
    assert "<redacted-image-uri>" in raw_text
    assert summary["results"][0]["raw_response_redacted"] is True


def test_result_structure_is_redacted_when_response_echoes_uri():
    """응답이 URI 를 반사해도 **결과 구조**에는 남지 않는다."""
    payload = _vision_response()
    payload["responses"][0]["context"] = {"uri": PRESIGNED_URL}
    # OCR 텍스트 안에도 서명 쿼리 파라미터가 섞여 들어온 경우 (방어적 경로)
    payload["responses"][0]["fullTextAnnotation"]["text"] = (
        "2x=6 X-Goog-Signature=DEADBEEFSIGNATURE " + PRESIGNED_URL
    )
    structure = adapter.parse_response(payload, source_image=PRESIGNED_URL)
    blob = json.dumps(structure, ensure_ascii=False)
    assert PRESIGNED_URL not in blob
    assert "DEADBEEFSIGNATURE" not in blob
    assert "<redacted-image-uri>" in blob


# ===========================================================================
# 3. raw 응답 보존 — 파일명 유일
# ===========================================================================

def test_two_images_keep_separate_raw_responses(tmp_path):
    """한 run 의 두 이미지 응답이 서로 덮어쓰지 않는다."""
    first = _vision_response()
    second = _vision_response()
    second["responses"][0]["fullTextAnnotation"]["text"] = "SECOND IMAGE TEXT"

    class TwoPayloadTransport(FakeTransport):
        def __call__(self, body, *, token, timeout):
            self.calls.append({"body": body, "token": token, "timeout": timeout})
            payload = first if len(self.calls) == 1 else second
            return json.loads(json.dumps(payload)), 0.42

    transport = TwoPayloadTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(2), transport=transport, local_dir=local,
        dry_run=False, max_calls=2, token=FAKE_TOKEN,
    )
    raw_files = sorted((local / "raw").glob("*.json"))
    assert len(raw_files) == 2
    texts = {
        json.loads(path.read_text(encoding="utf-8"))
        ["responses"][0]["fullTextAnnotation"]["text"]
        for path in raw_files
    }
    assert texts == {"2(x-3)=6\n2x=12\nx=6", "SECOND IMAGE TEXT"}
    # 결과 행의 raw 경로도 서로 다르다
    raw_refs = {row["raw_response_file"] for row in summary["results"]}
    assert len(raw_refs) == 2


def test_raw_file_name_contains_run_and_image_id(tmp_path):
    """파일명에 run_id 와 image_id 가 모두 들어간다."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    probe.run_probe(
        _images(1), transport=transport, local_dir=local,
        dry_run=False, max_calls=1, run_id="gv-fixed", token=FAKE_TOKEN,
    )
    raw_files = list((local / "raw").glob("*.json"))
    assert len(raw_files) == 1
    assert raw_files[0].name == "gv-fixed__G01.json"


# ===========================================================================
# 4. 누적 40회 가드 — 슬롯 예약 + lock
# ===========================================================================

def test_slot_is_reserved_before_the_request(tmp_path):
    """슬롯은 **요청 전에** 예약된다 — 실패해도 남는다."""
    transport = FakeTransport(error=RuntimeError("timeout"))
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(1), transport=transport, local_dir=local,
        dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    assert summary["failed"] == 1
    ledger = local / "call_budget.jsonl"
    assert ledger.exists()
    lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 예약 1줄 + 결과(failed) 1줄 — 결과 기록은 별도 줄이다.
    entries = [json.loads(line) for line in lines]
    reservations = [entry for entry in entries if entry["kind"] == "reservation"]
    outcomes = [entry for entry in entries if entry["kind"] == "outcome"]
    assert len(reservations) == 1
    assert reservations[0]["status"] == "reserved"
    assert reservations[0]["id"] == "G01"
    assert [entry["status"] for entry in outcomes] == ["failed"]
    assert probe.used_calls(ledger) == 1


def test_cumulative_cap_is_enforced(tmp_path):
    """누적 상한을 넘으면 **호출 전에** 거부한다."""
    local = tmp_path / "gv"
    ledger = local / "call_budget.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "".join(
            '{"kind":"reservation","call_id":"c%02d","run_id":"r","id":"G%02d",'
            '"reserved_at":"%s","status":"reserved"}\n'
            % (i, i, PAST_TIMESTAMP)
            for i in range(38)
        ),
        encoding="utf-8",
    )
    transport = FakeTransport()
    with pytest.raises(client.BudgetExceeded):
        probe.run_probe(
            _images(3), transport=transport, local_dir=local,
            dry_run=False, max_calls=3, token=FAKE_TOKEN,
        )
    assert transport.calls == []


def test_cumulative_cap_allows_calls_within_budget(tmp_path):
    local = tmp_path / "gv"
    ledger = local / "call_budget.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "".join(
            '{"kind":"reservation","call_id":"c%02d","run_id":"r","id":"G%02d",'
            '"reserved_at":"%s","status":"reserved"}\n'
            % (i, i, PAST_TIMESTAMP)
            for i in range(38)
        ),
        encoding="utf-8",
    )
    transport = FakeTransport()
    summary = probe.run_probe(
        _images(2), transport=transport, local_dir=local,
        dry_run=False, max_calls=2, token=FAKE_TOKEN,
    )
    assert summary["called"] == 2
    assert len(transport.calls) == 2


def test_reserve_call_slot_respects_cap(tmp_path):
    """슬롯 예약 자체가 상한을 지킨다."""
    ledger = tmp_path / "call_budget.jsonl"
    lock = tmp_path / "call_budget.lock"
    for index in range(3):
        probe.reserve_call_slot("r", "G{}".format(index), ledger_path=ledger,
                                lock_path=lock, cap=3)
    assert probe.used_calls(ledger) == 3
    with pytest.raises(client.BudgetExceeded):
        probe.reserve_call_slot("r", "G99", ledger_path=ledger, lock_path=lock, cap=3)


def test_concurrent_reservations_are_serialized(tmp_path):
    """동시 실행은 lock 으로 직렬화된다 — 상한을 넘지 않는다."""
    ledger = tmp_path / "call_budget.jsonl"
    lock = tmp_path / "call_budget.lock"
    errors: list = []
    granted: list = []

    def worker(index):
        try:
            probe.reserve_call_slot("r", "G{}".format(index),
                                    ledger_path=ledger, lock_path=lock, cap=10)
            granted.append(index)
        except client.BudgetExceeded as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 상한 10 을 넘지 않는다
    assert len(granted) == 10
    assert len(errors) == 6
    assert probe.used_calls(ledger) == 10
    # 원장이 깨지지 않았다 (줄마다 JSON)
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)


def test_used_calls_counts_ledger_lines(tmp_path):
    ledger = tmp_path / "call_budget.jsonl"
    assert probe.used_calls(ledger) == 0
    ledger.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
    assert probe.used_calls(ledger) == 2


def test_run_probe_rejects_non_positive_max_calls(tmp_path):
    transport = FakeTransport()
    with pytest.raises(probe.ProbeError):
        probe.run_probe(
            _images(1), transport=transport, local_dir=tmp_path / "gv",
            dry_run=False, max_calls=0, token=FAKE_TOKEN,
        )
    assert transport.calls == []


# ===========================================================================
# 5. 실행 모드 — dry-run 무호출·무파일쓰기
# ===========================================================================

def test_dry_run_makes_no_call_and_writes_nothing(tmp_path):
    """dry-run 은 호출 0회, 파일 0 writes."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(2), transport=transport, local_dir=local, dry_run=True
    )
    assert summary["mode"] == "dry-run"
    assert summary["called"] == 0
    assert transport.calls == []
    assert not local.exists()


def test_cli_run_without_max_calls_is_rejected():
    with pytest.raises(SystemExit):
        probe.main(["--run"])
    with pytest.raises(SystemExit):
        probe.main(["--run", "--max-calls", "0"])


# ===========================================================================
# 6. 요청 본문 · credential
# ===========================================================================

def test_body_uses_only_document_text_detection():
    """`TEXT_DETECTION` 을 함께 부르면 무료 unit 을 중복 소비한다 — 금지."""
    body = client.build_body("gs://demo-bucket/g01.png")
    features = body["requests"][0]["features"]
    assert len(features) == 1
    assert features[0]["type"] == "DOCUMENT_TEXT_DETECTION"
    with pytest.raises(ValueError):
        client.build_body("gs://demo-bucket/g01.png", feature_type="TEXT_DETECTION")


def test_language_hint_is_optional():
    without = client.build_body("gs://demo-bucket/g01.png")
    assert "imageContext" not in without["requests"][0]
    with_hint = client.build_body("gs://demo-bucket/g01.png", language_hint="ko")
    assert with_hint["requests"][0]["imageContext"]["languageHints"] == ["ko"]
    assert without != with_hint


def test_invalid_image_uri_is_rejected():
    for bad in ("http://x.invalid/a.png", "file:///etc/passwd", ""):
        with pytest.raises(ValueError):
            client.build_body(bad)


def test_access_token_never_appears_in_outputs(tmp_path):
    """토큰은 파일·요약·로그 어디에도 남지 않는다."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(2), transport=transport, local_dir=local,
        dry_run=False, max_calls=2, token=FAKE_TOKEN,
    )
    for path in list(local.rglob("*.json")) + list(local.rglob("*.jsonl")):
        assert FAKE_TOKEN not in path.read_text(encoding="utf-8")
    assert FAKE_TOKEN not in json.dumps(summary, ensure_ascii=False)


def test_access_token_is_redacted_from_error_messages(tmp_path):
    transport = FakeTransport(error=RuntimeError("boom with " + FAKE_TOKEN))
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(1), transport=transport, local_dir=local,
        dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    assert summary["failed"] == 1
    assert FAKE_TOKEN not in json.dumps(summary, ensure_ascii=False)
    for path in local.rglob("*.json"):
        assert FAKE_TOKEN not in path.read_text(encoding="utf-8")


def test_credential_resolution_prefers_injected_env_token():
    token = client.resolve_access_token(
        env={client.ACCESS_TOKEN_ENV: "  injected-token  "}
    )
    assert token == "injected-token"


def test_credential_resolution_uses_adc_when_env_absent():
    class Result:
        returncode = 0
        stdout = "adc-token\n"

    token = client.resolve_access_token(env={}, runner=lambda cmd: Result())
    assert token == "adc-token"


def test_credential_resolution_fails_clearly_without_gcloud():
    class Result:
        returncode = 1
        stdout = ""

    with pytest.raises(client.VisionError):
        client.resolve_access_token(env={}, runner=lambda cmd: Result())


# ===========================================================================
# 7. manifest · 산출물 위치
# ===========================================================================

def test_outputs_are_written_under_local_dir(tmp_path):
    """결과 파일은 `eval/_local/google_vision/` 아래에만 생긴다."""
    transport = FakeTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(1), transport=transport, local_dir=local,
        dry_run=False, max_calls=1, token=FAKE_TOKEN,
    )
    assert len(list((local / "raw").glob("*.json"))) == 1
    assert len(list((local / "results").glob("*.json"))) == 1
    assert summary["results"][0]["raw_response_file"]
    assert summary["results"][0]["result_file"]


def test_manifest_is_required_and_validated(tmp_path):
    with pytest.raises(probe.ProbeError):
        probe.load_manifest(tmp_path / "missing.json")

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "G01"}]), encoding="utf-8")
    with pytest.raises(probe.ProbeError):
        probe.load_manifest(bad)

    dup = tmp_path / "dup.json"
    dup.write_text(
        json.dumps([{"id": "G01", "uri": "gs://b/a.png"},
                    {"id": "G01", "uri": "gs://b/b.png"}]),
        encoding="utf-8",
    )
    with pytest.raises(probe.ProbeError):
        probe.load_manifest(dup)

    ok = tmp_path / "ok.json"
    ok.write_text(
        json.dumps([{"id": "G01", "uri": "gs://b/a.png"}]), encoding="utf-8"
    )
    assert probe.load_manifest(ok) == [{"id": "G01", "uri": "gs://b/a.png"}]


def test_language_hint_is_carried_into_the_result(tmp_path):
    transport = FakeTransport()
    local = tmp_path / "gv"
    summary = probe.run_probe(
        _images(1), transport=transport, local_dir=local,
        dry_run=False, max_calls=1, language_hint="ko", token=FAKE_TOKEN,
    )
    result = json.loads(
        Path(summary["results"][0]["result_file"]).read_text(encoding="utf-8")
    )
    assert result["language_hint"] == "ko"
    assert transport.calls[0]["body"]["requests"][0]["imageContext"]["languageHints"] == ["ko"]
