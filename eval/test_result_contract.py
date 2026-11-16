"""PHASE 9-9B.10 — 측정 입력 계약 · attempt outcome 정합성 테스트.

외부 호출 없음 — fake data 만 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT.parent))

import measure  # noqa: E402

from test_coverage import PRICING, _draft, _labels, _row  # noqa: E402


# ===========================================================================
# 1. 명시 상태 필드 — 모순은 입력 계약 오류
# ===========================================================================

def test_adapter_true_without_draft_is_contract_error():
    """`adapter_succeeded=True` 인데 draft 가 없으면 **계약 오류**다."""
    row = _row(case_id="E01", provider_succeeded=True, adapter_succeeded=True,
               error_stage=None, draft=None, error=None)
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "draft" in str(excinfo.value)


def test_adapter_false_with_draft_is_contract_error():
    """`adapter_succeeded=False` 인데 draft 가 있으면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=True, adapter_succeeded=False,
               error_stage="adapter", draft=_draft(), error="변환 실패")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "draft" in str(excinfo.value)


def test_provider_false_with_adapter_true_is_contract_error():
    """provider 실패인데 adapter 성공이면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=False, adapter_succeeded=True,
               error_stage=None, draft=_draft(), error=None)
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "provider_succeeded" in str(excinfo.value)


def test_success_with_error_is_contract_error():
    """두 단계 성공인데 error 가 있으면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=True, adapter_succeeded=True,
               error_stage=None, draft=_draft(), error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error" in str(excinfo.value)


def test_error_stage_none_on_failure_is_contract_error():
    """실패 상태인데 `error_stage` 가 None 이면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=False, adapter_succeeded=False,
               error_stage=None, draft=None, error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error_stage" in str(excinfo.value)


def test_error_stage_provider_with_provider_success_is_contract_error():
    """`error_stage='provider'` 인데 provider 성공이면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=True, adapter_succeeded=False,
               error_stage="provider", draft=None, error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error_stage" in str(excinfo.value)


def test_invalid_error_stage_value_is_contract_error():
    """`error_stage` 값이 provider/adapter/None 이 아니면 계약 오류다."""
    row = _row(case_id="E01", provider_succeeded=False, adapter_succeeded=False,
               error_stage="whatever", draft=None, error="boom")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "error_stage" in str(excinfo.value)


def test_partial_explicit_fields_are_contract_error():
    """명시 필드가 **일부만** 있으면 계약 오류다 — 셋 다 있어야 한다."""
    row = _row(case_id="E01", draft=_draft())
    row.pop("error_stage")
    with pytest.raises(measure.ResultContractError) as excinfo:
        measure.attempt_outcome(row)
    assert "일부" in str(excinfo.value) or "셋 다" in str(excinfo.value)


def test_measure_raises_on_contradictory_row():
    """모순된 행이 있으면 `measure()` 도 즉시 중단한다 (조용히 재계산 안 함)."""
    labels = _labels(1)
    row = _row(case_id="E01", provider_succeeded=True, adapter_succeeded=True,
               error_stage=None, draft=None, error=None)
    with pytest.raises(measure.ResultContractError):
        measure.measure(labels, [row], PRICING)


# ===========================================================================
# 2. label 과 일치하지 않는 결과 행
# ===========================================================================

def test_result_row_not_in_labels_is_excluded_and_counted():
    """label 에 없는 id 의 결과 행은 **집계에서 제외**하고 건수를 보고한다."""
    labels = _labels(1)
    rows = [
        _row(case_id="E01", draft=_draft()),
        _row(case_id="E99", draft=_draft()),
    ]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    assert counts["unmatched_result_rows"] == 1
    # 집계 대상 행 집합에는 들어가지 않는다
    assert counts["attempts_total"] == 1
    assert counts["measured"] == 1
    # 제외된 행의 비용은 따로 보고한다
    assert report["C_operations"]["unmatched_cost_estimate_usd"] == pytest.approx(0.002)


def test_unmatched_rows_do_not_inflate_failure_rates():
    """일치하지 않는 행은 실패율 분자·분모에 들어가지 않는다."""
    labels = _labels(1)
    rows = [
        _row(case_id="E01", draft=_draft()),
        _row(case_id="E98", error="boom"),
        _row(case_id="E99", error="boom"),
    ]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    assert counts["unmatched_result_rows"] == 2
    assert counts["attempts_total"] == 1
    assert counts["failed_attempts"] == 0
    assert counts["provider_failed_attempts"] == 0
    assert report["C_operations"]["provider_failure_rate"] == pytest.approx(0.0)
    assert report["C_operations"]["sample_failure_rate"] == pytest.approx(0.0)


# ===========================================================================
# 3. 분자·분모가 같은 행 집합
# ===========================================================================

def test_attempt_count_and_failure_rate_denominators_match():
    """혼합 결과에서 attempt count 와 provider/adapter 실패율의 분자·분모가 일치한다."""
    labels = _labels(4)
    rows = [
        _row(case_id="E01", run_id="r1", attempt=1, error="provider down",
             provider_succeeded=False, adapter_succeeded=False, error_stage="provider"),
        _row(case_id="E02", run_id="r1", attempt=2, error="변환 실패",
             provider_succeeded=True, adapter_succeeded=False, error_stage="adapter"),
        _row(case_id="E03", run_id="r1", attempt=3, draft=_draft()),
        _row(case_id="E03", run_id="r2", attempt=1, draft=_draft()),
        _row(case_id="E04", run_id="r2", attempt=2, error="provider down",
             provider_succeeded=False, adapter_succeeded=False, error_stage="provider"),
        # label 에 없는 행 — 집계 대상 아님
        _row(case_id="E99", run_id="r2", attempt=3, error="provider down",
             provider_succeeded=False, adapter_succeeded=False, error_stage="provider"),
    ]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    ops = report["C_operations"]

    measured = counts["attempts_total"]
    assert measured == 5                       # E99 제외
    assert counts["measurements_total"] == 6
    assert counts["unmatched_result_rows"] == 1

    # 분자 합 = 실패 시도 수
    assert counts["provider_failed_attempts"] + counts["adapter_failed_attempts"] \
        == counts["failed_attempts"]
    assert counts["provider_succeeded_attempts"] + counts["provider_failed_attempts"] \
        == measured
    # 분모도 같은 집합
    assert ops["provider_failure_rate"] == pytest.approx(
        counts["provider_failed_attempts"] / measured
    )
    assert ops["adapter_failure_rate"] == pytest.approx(
        counts["adapter_failed_attempts"] / counts["provider_succeeded_attempts"]
    )
    assert ops["attempt_failure_rate"] == pytest.approx(
        counts["failed_attempts"] / measured
    )
    assert ops["sample_failure_rate"] == pytest.approx(
        counts["failures"] / counts["samples_attempted"]
    )


# ===========================================================================
# 4. 구버전 행 fallback 유지
# ===========================================================================

def test_legacy_rows_still_work():
    """구버전 행(명시 필드 없음)은 fallback 으로 계속 동작한다."""
    legacy_ok = {
        "id": "E01", "sample_id": "S1", "draft": _draft(),
        "elapsed_seconds": 0.1, "error": None,
    }
    legacy_provider_fail = {
        "id": "E02", "sample_id": "S1", "draft": None,
        "elapsed_seconds": 0.1, "error": "boom",
    }
    outcome_ok = measure.attempt_outcome(legacy_ok)
    assert outcome_ok["provider_succeeded"] is True
    assert outcome_ok["adapter_succeeded"] is True
    assert outcome_ok["error_stage"] is None
    outcome_fail = measure.attempt_outcome(legacy_provider_fail)
    assert outcome_fail["provider_succeeded"] is False
    assert outcome_fail["error_stage"] == "provider"

    report = measure.measure(_labels(2), [legacy_ok, legacy_provider_fail], PRICING)
    assert report["counts"]["provider_failed_attempts"] == 1
    assert report["counts"]["attempts_total"] == 2


def test_legacy_rows_with_mixed_outcomes_are_classified():
    """구버전 행에서 provider 성공 + adapter 실패 형태도 분류된다."""
    legacy_adapter_fail = {
        "id": "E01", "sample_id": "S1", "draft": None,
        "elapsed_seconds": 0.1, "error": "변환 실패",
    }
    outcome = measure.attempt_outcome(legacy_adapter_fail)
    # error 만 있고 draft 가 없으면 provider 실패로 분류된다(구버전 규칙 유지)
    assert outcome["provider_succeeded"] is False
    assert outcome["error_stage"] == "provider"
