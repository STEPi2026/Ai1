"""PHASE 9-9B.9 — 부분 실행 coverage · provider/adapter 실패 구분 테스트.

전부 **fake transport** 만 사용한다. 외부 네트워크 호출은 없다.
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
import run_batch as batch  # noqa: E402

from test_run_batch import FakeTransport, SECRET_URL  # noqa: E402


def _labels(count):
    return [
        {
            "id": "E{:02d}".format(index),
            "category": "complete-correct",
            "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": ["2(x-3)=6"],
            "step_order": [1],
            "unreadable_steps": [],
        }
        for index in range(1, count + 1)
    ]


def _draft(text="2(x-3)=6"):
    return {
        "problem": {"text": text, "latex_text": text, "confidence": 0.9},
        "problem_bbox": None,
        "problem_kind": "printed",
        "steps": [{"step_no": 1, "text": text, "latex_text": text,
                   "confidence": 0.9, "unreadable": False, "source_line_index": 0}],
        "regions": [{"source_line_index": 0, "kind": "printed", "text": text}],
        "line_kinds": {"0": "printed"},
        "source_line_count": 1,
        "billable_text_row_count": 1,
    }


_UNSET = object()


def _row(case_id="E01", sample_id="S1", run_id="run-1", attempt=1, *,
         error=None, draft=None, elapsed=0.25,
         provider_succeeded=None, adapter_succeeded=None, error_stage=_UNSET):
    if provider_succeeded is None:
        provider_succeeded = bool(draft) or not error
    if adapter_succeeded is None:
        adapter_succeeded = bool(draft) and not error
    if error_stage is _UNSET:
        error_stage = (
            None if (provider_succeeded and adapter_succeeded)
            else ("adapter" if provider_succeeded else "provider")
        )
    return {
        "id": case_id,
        "sample_id": sample_id,
        "run_id": run_id,
        "attempt_id": "{}#{:04d}".format(run_id, attempt),
        "attempt_no": attempt,
        "raw_response_file": None,
        "draft": draft,
        "elapsed_seconds": elapsed,
        "error": error,
        "provider_succeeded": provider_succeeded,
        "adapter_succeeded": adapter_succeeded,
        "error_stage": error_stage,
    }


PRICING = {"per_image_usd": 0.002, "per_page_usd": 0.005}


# ===========================================================================
# 1. run_batch — provider / adapter 결과 구분
# ===========================================================================

def test_provider_failure_is_marked_as_provider_stage(tmp_path):
    """provider 호출 실패는 `error_stage="provider"` 다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport(fail_on=("DEADBEEFSECRET",))
    summary = batch.run_batch(
        _labels(1), {"E01": SECRET_URL}, transport=transport,
        local_root=root, dry_run=False, max_calls=1,
    )
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    row = rows[0]
    assert row["provider_succeeded"] is False
    assert row["adapter_succeeded"] is False
    assert row["error_stage"] == "provider"
    assert row["raw_response_file"] is None
    assert summary["provider_failures"] == 1
    assert summary["adapter_failures"] == 0


def test_adapter_failure_keeps_provider_success_and_raw_response(tmp_path, monkeypatch):
    """provider 성공 + adapter 실패 — 호출 성공·지연·원본 응답을 보존한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()

    def boom(payload):
        raise RuntimeError("변환 실패")

    monkeypatch.setattr(adapter, "draft_from_response", boom)
    summary = batch.run_batch(
        _labels(1), {"E01": SECRET_URL}, transport=transport,
        local_root=root, dry_run=False, max_calls=1,
    )
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    row = rows[0]
    # **provider 는 성공** — 응답을 받았고 원본이 저장되었다
    assert row["provider_succeeded"] is True
    assert row["adapter_succeeded"] is False
    assert row["error_stage"] == "adapter"
    assert row["draft"] is None
    assert row["raw_response_file"]
    assert Path(row["raw_response_file"]).exists()
    assert row["elapsed_seconds"] is not None
    # provider 실패로 집계되지 않는다
    assert summary["provider_failures"] == 0
    assert summary["adapter_failures"] == 1


def test_success_row_marks_both_stages(tmp_path):
    """정상 호출은 두 단계 모두 성공."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = batch.run_batch(
        _labels(1), {"E01": SECRET_URL}, transport=transport,
        local_root=root, dry_run=False, max_calls=1,
    )
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["provider_succeeded"] is True
    assert rows[0]["adapter_succeeded"] is True
    assert rows[0]["error_stage"] is None


# ===========================================================================
# 2. coverage — 부분 실행
# ===========================================================================

def test_one_of_twenty_successful_covers_five_percent():
    """20개 label 중 1개만 성공 실행 → coverage 5%, sample 실패율 0%."""
    labels = _labels(20)
    rows = [_row(case_id="E01", draft=_draft())]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    assert counts["samples_attempted"] == 1
    assert counts["samples_unattempted"] == 19
    assert counts["coverage"] == pytest.approx(0.05)
    ops = report["C_operations"]
    assert ops["sample_failure_rate"] == pytest.approx(0.0)
    # 전체 label 기준 end-to-end 는 5% (시도 안 한 sample 이 분모에 들어감)
    assert ops["end_to_end_failure_rate"] == pytest.approx(0.0)


def test_one_attempted_failure_gives_100_percent_attempted_failure_rate():
    """1개만 시도해 실패 → 시도 sample 실패율 100%, 나머지는 unattempted."""
    labels = _labels(20)
    rows = [_row(case_id="E01", error="RuntimeError: boom")]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    assert counts["samples_attempted"] == 1
    assert counts["samples_unattempted"] == 19
    assert counts["coverage"] == pytest.approx(0.05)
    assert counts["failures"] == 1
    ops = report["C_operations"]
    assert ops["sample_failure_rate"] == pytest.approx(1.0)
    # 시도하지 않은 sample 은 실패로 세지 않는다
    assert counts["failures"] == 1


def test_no_results_at_all_yields_null_rate():
    """결과가 없으면 시도 sample 실패율은 `null` 이다."""
    labels = _labels(20)
    report = measure.measure(labels, [], PRICING)
    counts = report["counts"]
    assert counts["samples_attempted"] == 0
    assert counts["samples_unattempted"] == 20
    assert counts["coverage"] == pytest.approx(0.0)
    ops = report["C_operations"]
    assert ops["sample_failure_rate"] is None
    assert ops["end_to_end_failure_rate"] == pytest.approx(0.0)
    assert ops["provider_failure_rate"] is None
    assert ops["adapter_failure_rate"] is None
    assert ops["cost_estimate_usd_total"] is None


def test_no_labels_yields_null_coverage():
    """label 자체가 없으면 coverage 도 `null`."""
    report = measure.measure([], [], PRICING)
    assert report["counts"]["coverage"] is None
    assert report["C_operations"]["sample_failure_rate"] is None


# ===========================================================================
# 3. provider 실패 / adapter 실패 분리 집계
# ===========================================================================

def test_provider_and_adapter_failures_are_separate_metrics():
    """provider 실패와 adapter 실패가 **서로 다른 지표**로 집계된다."""
    labels = _labels(4)
    rows = [
        _row(case_id="E01", run_id="r1", attempt=1,
             error="provider down", provider_succeeded=False,
             adapter_succeeded=False, error_stage="provider"),
        _row(case_id="E02", run_id="r1", attempt=2,
             error="변환 실패", provider_succeeded=True,
             adapter_succeeded=False, error_stage="adapter"),
        _row(case_id="E03", run_id="r1", attempt=3, draft=_draft()),
    ]
    report = measure.measure(labels, rows, PRICING)
    counts = report["counts"]
    assert counts["provider_failed_attempts"] == 1
    assert counts["adapter_failed_attempts"] == 1
    assert counts["provider_succeeded_attempts"] == 2
    assert counts["failed_attempts"] == 2
    ops = report["C_operations"]
    assert ops["provider_failure_rate"] == pytest.approx(1 / 3)
    # adapter 실패율의 분모는 provider 성공한 시도(2)
    assert ops["adapter_failure_rate"] == pytest.approx(0.5)
    assert ops["attempt_failure_rate"] == pytest.approx(2 / 3)


def test_adapter_failure_is_not_counted_as_provider_failure():
    """provider 성공 + adapter 실패는 provider 실패로 세지 않는다."""
    labels = _labels(1)
    rows = [_row(case_id="E01", error="변환 실패", provider_succeeded=True,
                 adapter_succeeded=False, error_stage="adapter")]
    report = measure.measure(labels, rows, PRICING)
    assert report["counts"]["provider_failed_attempts"] == 0
    assert report["counts"]["adapter_failed_attempts"] == 1
    assert report["C_operations"]["provider_failure_rate"] == pytest.approx(0.0)
    assert report["C_operations"]["adapter_failure_rate"] == pytest.approx(1.0)


# ===========================================================================
# 4. adapter 실패의 비용 — 0 으로 누락되지 않는다
# ===========================================================================

def test_adapter_failure_cost_is_unknown_not_zero():
    """provider 성공 + adapter 실패는 **비용 unknown** — 0 으로 누락되지 않는다."""
    labels = _labels(1)
    rows = [_row(case_id="E01", error="변환 실패", provider_succeeded=True,
                 adapter_succeeded=False, error_stage="adapter")]
    report = measure.measure(labels, rows, PRICING)
    ops = report["C_operations"]
    # 0 으로 세어지지 않는다 (unknown 으로 집계)
    assert ops["cases_cost_unknown"] == 1
    assert ops["cases_cost_unknown_adapter_failed"] == 1
    assert ops["cases_cost_unknown_provider_failed"] == 0
    # 추정 합계는 0 이 아니라 "없음"(미확정) — 하한 아님을 드러낸다
    assert ops["cost_estimate_usd_total"] is None
    assert ops["cost_estimate_usd_total_is_lower_bound"] is True
    # unknown 수와 추정 합계를 함께 보여준다
    assert "cases_cost_unknown" in ops
    assert "추정" in ops["cost_estimate_note"]


def test_provider_failure_cost_is_tracked_separately():
    """provider 실패도 unknown 으로 집계하고 provider 실패로 구분한다."""
    labels = _labels(1)
    rows = [_row(case_id="E01", error="provider down", provider_succeeded=False,
                 adapter_succeeded=False, error_stage="provider")]
    report = measure.measure(labels, rows, PRICING)
    ops = report["C_operations"]
    assert ops["cases_cost_unknown"] == 1
    assert ops["cases_cost_unknown_provider_failed"] == 1
    assert ops["cases_cost_unknown_adapter_failed"] == 0


def test_successful_attempt_cost_is_counted_alongside_unknown():
    """성공 시도 비용은 계산되고, unknown 시도 수와 함께 표시된다."""
    labels = _labels(2)
    rows = [
        _row(case_id="E01", draft=_draft()),
        _row(case_id="E02", error="변환 실패", provider_succeeded=True,
             adapter_succeeded=False, error_stage="adapter"),
    ]
    report = measure.measure(labels, rows, PRICING)
    ops = report["C_operations"]
    assert ops["cost_estimate_usd_total"] == pytest.approx(0.002)
    assert ops["cases_cost_unknown"] == 1
    assert ops["cost_estimate_usd_total_is_lower_bound"] is True


def test_legacy_rows_without_stage_fields_are_classified():
    """구버전 행(`provider_succeeded` 없음)도 단계로 분류된다."""
    legacy_ok = {
        "id": "E01", "sample_id": "S1", "draft": _draft(),
        "elapsed_seconds": 0.1, "error": None,
    }
    legacy_provider_fail = {
        "id": "E02", "sample_id": "S1", "draft": None,
        "elapsed_seconds": 0.1, "error": "boom",
    }
    assert measure.attempt_outcome(legacy_ok)["error_stage"] is None
    assert measure.attempt_outcome(legacy_provider_fail)["error_stage"] == "provider"
    report = measure.measure(_labels(2), [legacy_ok, legacy_provider_fail], PRICING)
    assert report["counts"]["provider_failed_attempts"] == 1
    assert report["counts"]["adapter_failed_attempts"] == 0
