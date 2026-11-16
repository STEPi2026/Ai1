"""PHASE 9-9B.11 — 결과 상태 필드의 JSON 타입 검증 테스트.

외부 호출 없음 — fake data 만 쓴다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT.parent))

import measure  # noqa: E402

from test_coverage import PRICING, _draft, _labels  # noqa: E402


def _explicit(provider, adapter, stage, *, draft=None, error=None):
    """명시 상태 필드를 그대로 넣은 행 (타입 검증을 그대로 받는다)."""
    return {
        "id": "E01",
        "sample_id": "S1",
        "run_id": "run-1",
        "attempt_id": "run-1#0001",
        "attempt_no": 1,
        "raw_response_file": None,
        "draft": draft,
        "elapsed_seconds": 0.25,
        "error": error,
        "provider_succeeded": provider,
        "adapter_succeeded": adapter,
        "error_stage": stage,
    }


# ===========================================================================
# 1. provider_succeeded / adapter_succeeded — JSON boolean 만
# ===========================================================================

@pytest.mark.parametrize("value", ["false", "true", "False", "TRUE", "", "0"])
def test_string_booleans_are_rejected(value):
    """문자열 상태값은 거부된다 — `"false"` 는 Python 에서 참이다."""
    row = _explicit(value, False, "provider", error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "JSON boolean" in str(excinfo.value)


@pytest.mark.parametrize("value", [0, 1, 2, -1])
def test_integer_booleans_are_rejected(value):
    """정수 상태값은 거부된다 (`bool` 은 `int` 의 부분클래스이므로 `isinstance` 로 구분)."""
    row = _explicit(value, False, "provider", error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "JSON boolean" in str(excinfo.value)


@pytest.mark.parametrize("value", [None, [], {}, 0.0])
def test_null_and_other_booleans_are_rejected(value):
    """null 등 boolean 이 아닌 값은 거부된다."""
    row = _explicit(value, False, "provider", error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "JSON boolean" in str(excinfo.value)


def test_adapter_succeeded_type_is_also_checked():
    """`adapter_succeeded` 도 동일하게 검증된다."""
    row = _explicit(True, "false", "adapter", error="변환 실패")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "adapter_succeeded" in str(excinfo.value)
    assert "JSON boolean" in str(excinfo.value)


# ===========================================================================
# 2. error_stage — null 또는 정확히 "provider" / "adapter"
# ===========================================================================

@pytest.mark.parametrize("value", ["", "PROVIDER", "Provider", "adapter ", " other", 0, 1, True, [], {}])
def test_invalid_error_stage_values_are_rejected(value):
    """`error_stage` 는 null 또는 정확히 두 문자열뿐이다."""
    row = _explicit(False, False, value, error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error_stage" in str(excinfo.value)


def test_error_stage_null_is_allowed_for_success():
    """성공 행의 `error_stage` 는 null 이다."""
    row = _explicit(True, True, None, draft=_draft())
    outcome = measure.attempt_outcome(row)
    assert outcome["error_stage"] is None
    assert outcome["provider_succeeded"] is True
    assert outcome["adapter_succeeded"] is True


# ===========================================================================
# 3. error — string 또는 null
# ===========================================================================

@pytest.mark.parametrize("value", [123, 0, [], {}, True, False])
def test_non_string_error_is_rejected(value):
    """`error` 는 string 또는 null 이어야 한다."""
    row = _explicit(False, False, "provider", error=value)
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error" in str(excinfo.value)


def test_non_string_error_is_rejected_on_legacy_row():
    """구버전 행에도 `error` 타입 검증이 적용된다."""
    row = {
        "id": "E01", "sample_id": "S1", "draft": None,
        "elapsed_seconds": 0.1, "error": 123,
    }
    with pytest.raises(measure.ResultContractError):
        measure.attempt_outcome(row)


# ===========================================================================
# 4. measure() 도 즉시 실패
# ===========================================================================

def test_measure_raises_on_wrong_type():
    """잘못된 타입의 행이 있으면 `measure()` 도 즉시 `ResultContractError`."""
    labels = _labels(1)
    row = _explicit("true", True, None, draft=_draft())
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.measure(labels, [row], PRICING)
    assert "JSON boolean" in str(excinfo.value)


def test_measure_raises_on_wrong_error_stage_type():
    labels = _labels(1)
    row = _explicit(False, False, 0, error="boom")
    with pytest.raises(measure.ResultContractError):
        measure.measure(labels, [row], PRICING)


def test_measure_raises_on_unmatched_row_with_wrong_type():
    """label 에 없는 행이어도 타입 검증은 먼저 한다."""
    labels = _labels(1)
    row = _explicit("false", False, "provider", error="boom")
    row["id"] = "E99"
    with pytest.raises(measure.ResultContractError):
        measure.measure(labels, [row], PRICING)


# ===========================================================================
# 5. 정상 행 · 구버전 fallback 은 계속 통과
# ===========================================================================

def test_valid_json_boolean_rows_pass():
    """정상 JSON boolean 성공/실패 행은 통과한다."""
    ok_row = _explicit(True, True, None, draft=_draft())
    assert measure.attempt_outcome(ok_row)["error_stage"] is None

    provider_fail = _explicit(False, False, "provider", error="boom")
    outcome = measure.attempt_outcome(provider_fail)
    assert outcome["provider_succeeded"] is False
    assert outcome["adapter_succeeded"] is False
    assert outcome["error_stage"] == "provider"

    adapter_fail = _explicit(True, False, "adapter", error="변환 실패")
    outcome = measure.attempt_outcome(adapter_fail)
    assert outcome["provider_succeeded"] is True
    assert outcome["adapter_succeeded"] is False
    assert outcome["error_stage"] == "adapter"


def test_json_roundtrip_keeps_boolean_types():
    """JSON 을 거쳐도 상태값은 boolean 으로 유지된다."""
    row = _explicit(False, False, "provider", error="boom")
    parsed = json.loads(json.dumps(row))
    assert isinstance(parsed["provider_succeeded"], bool)
    assert isinstance(parsed["adapter_succeeded"], bool)
    outcome = measure.attempt_outcome(parsed)
    assert outcome["provider_succeeded"] is False


def test_string_false_would_have_been_truthy_without_type_check():
    """회귀 — 타입 검증이 없으면 `"false"` 가 참으로 집계된다."""
    row = _explicit("false", "false", "provider", error="boom")
    with pytest.raises(measure.ResultContractError):
        measure.attempt_outcome(row)
    # Python truthiness 로 보면 참이었다
    assert bool("false") is True


def test_legacy_rows_still_pass():
    """구버전 행 fallback 은 계속 통과한다."""
    legacy_ok = {
        "id": "E01", "sample_id": "S1", "draft": _draft(),
        "elapsed_seconds": 0.1, "error": None,
    }
    legacy_fail = {
        "id": "E02", "sample_id": "S1", "draft": None,
        "elapsed_seconds": 0.1, "error": "boom",
    }
    assert measure.attempt_outcome(legacy_ok)["error_stage"] is None
    assert measure.attempt_outcome(legacy_fail)["error_stage"] == "provider"
    report = measure.measure(_labels(2), [legacy_ok, legacy_fail], PRICING)
    assert report["counts"]["provider_failed_attempts"] == 1
    assert report["counts"]["attempts_total"] == 2


def test_run_batch_output_passes_type_validation(tmp_path):
    """`run_batch` 가 만든 실제 결과 행은 타입 검증을 통과한다."""
    import run_batch as batch
    from test_run_batch import FakeTransport, SECRET_URL

    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = batch.run_batch(
        _labels(1), {"E01": SECRET_URL}, transport=transport,
        local_root=root, dry_run=False, max_calls=1,
    )
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    row = rows[0]
    assert isinstance(row["provider_succeeded"], bool)
    assert isinstance(row["adapter_succeeded"], bool)
    assert row["error_stage"] is None
    outcome = measure.attempt_outcome(row)
    assert outcome["provider_succeeded"] is True
