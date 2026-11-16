"""PHASE 9-9B.8 — 반복 측정 독립성 · 지표 집계 정합성 테스트.

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

import measure  # noqa: E402
import run_batch as batch  # noqa: E402

from test_run_batch import FakeTransport, SECRET_URL, SECRET_URL2, _labels  # noqa: E402


def _run(cases, manifest, transport, root, **kwargs):
    kwargs.setdefault("dry_run", False)
    kwargs.setdefault("max_calls", 10)
    return batch.run_batch(
        cases, manifest, transport=transport, local_root=root, **kwargs
    )


def _draft(text="2(x-3)=6", line_index=0, sample="a"):
    return {
        "problem": {"text": text, "latex_text": text, "confidence": 0.9},
        "problem_bbox": None,
        "problem_kind": "printed",
        "steps": [{"step_no": 1, "text": text, "latex_text": text,
                   "confidence": 0.9, "unreadable": False,
                   "source_line_index": line_index}],
        "regions": [{"source_line_index": line_index, "kind": "printed",
                     "text": text}],
        "line_kinds": {"0": "printed"},
        "source_line_count": 1,
        "billable_text_row_count": 1,
    }


def _row(case_id="E01", sample_id="S1", run_id="run-1", attempt=1, *,
         error=None, draft=None, elapsed=0.25):
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
    }


# ===========================================================================
# 1. run_id · attempt_id 기록
# ===========================================================================

def test_rows_record_run_id_and_attempt_id(tmp_path):
    """각 결과 행에 `run_id` 와 호출별 `attempt_id` 가 기록된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(count=2), {"E01": SECRET_URL, "E02": SECRET_URL2},
                   transport, root, run_id="run-x")
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2
    for row in rows:
        assert row["run_id"] == "run-x"
        assert row["attempt_id"].startswith("run-x#")
        assert row["attempt_no"] >= 1
    # attempt_id 는 서로 다르다
    assert len({row["attempt_id"] for row in rows}) == 2


def test_failed_attempt_records_elapsed(tmp_path):
    """실패한 시도도 elapsed 를 기록한다 (시도로 세어야 하므로)."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport(fail_on=("DEADBEEFSECRET",))
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root)
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["error"]
    assert rows[0]["elapsed_seconds"] is not None
    assert rows[0]["elapsed_seconds"] >= 0


# ===========================================================================
# 2. 누적 입력 검증 — 독립된 호출만
# ===========================================================================

def _write_results(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def test_same_file_twice_is_rejected(tmp_path):
    """**같은 결과 파일을 두 번 넣으면** 거부한다 — 반복으로 오인하지 않는다.

    run_id 중복 검사만으로 잡히지 않도록 **파일 중복 사유**를 특정해 확인한다.
    """
    path = _write_results(tmp_path / "r1.jsonl", [_row()])
    with pytest.raises(batch.BatchError) as excinfo:
        batch.accumulate_measurements([path, path])
    assert "같은 결과 파일" in str(excinfo.value)


def test_same_run_id_in_two_files_is_rejected(tmp_path):
    """**같은 run_id** 를 두 파일에서 주면 거부한다."""
    first = _write_results(tmp_path / "a.jsonl", [_row(run_id="run-1", attempt=1)])
    second = _write_results(tmp_path / "b.jsonl", [_row(run_id="run-1", attempt=2)])
    with pytest.raises(batch.BatchError) as excinfo:
        batch.accumulate_measurements([first, second])
    assert "run_id" in str(excinfo.value)


def test_rows_without_run_id_are_rejected(tmp_path):
    """**run_id 가 없는** 결과는 거부한다."""
    row = _row()
    row.pop("run_id")
    path = _write_results(tmp_path / "norun.jsonl", [row])
    with pytest.raises(batch.BatchError) as excinfo:
        batch.accumulate_measurements([path])
    assert "run_id" in str(excinfo.value)


def test_mixed_run_ids_in_one_file_are_rejected(tmp_path):
    """한 파일에 **여러 run_id 가 섞여 있으면** 거부한다."""
    path = _write_results(tmp_path / "mixed.jsonl", [
        _row(run_id="run-1", attempt=1),
        _row(run_id="run-2", attempt=1),
    ])
    with pytest.raises(batch.BatchError) as excinfo:
        batch.accumulate_measurements([path])
    assert "run_id" in str(excinfo.value)


def test_duplicate_attempt_id_is_rejected(tmp_path):
    """**attempt_id 중복**은 독립된 호출이 아니다."""
    path = _write_results(tmp_path / "dup.jsonl", [
        _row(run_id="run-1", attempt=1),
        _row(run_id="run-1", attempt=1),
    ])
    with pytest.raises(batch.BatchError) as excinfo:
        batch.accumulate_measurements([path])
    assert "attempt_id" in str(excinfo.value)


def test_accumulation_output_is_not_overwritten(tmp_path):
    """누적 산출물이 있으면 **덮어쓰지 않는다**."""
    out = tmp_path / "accumulated.jsonl"
    out.write_text("기존 내용\n", encoding="utf-8")
    code = batch.main([
        "--accumulate", str(tmp_path / "missing.jsonl"),
        "--accumulated-out", str(out),
    ])
    assert code == 1
    assert out.read_text(encoding="utf-8") == "기존 내용\n"


# ===========================================================================
# 3. M16 — 성공 호출만 비교
# ===========================================================================

def test_one_success_and_one_failure_is_null_not_unstable():
    """성공 1건 + 실패 1건은 **unstable 이 아니라 null** 이고 실패 수는 따로 집계한다."""
    rows = [
        _row(run_id="run-1", attempt=1, draft=_draft()),
        _row(run_id="run-2", attempt=1, error="RuntimeError: boom"),
    ]
    report = measure.line_index_stability_report(rows)
    assert report["source_line_index_stable"] is None
    assert report["stable_cases"] == 0
    assert report["unstable_cases"] == 0
    assert report["cases_with_repeats"] == 0
    assert report["successful_measurements"] == 1
    assert report["failed_measurements"] == 1


def test_two_successes_are_compared():
    """성공 2건이면 줄 인덱스 대응을 비교한다."""
    rows = [
        _row(run_id="run-1", attempt=1, draft=_draft()),
        _row(run_id="run-2", attempt=1, draft=_draft()),
    ]
    report = measure.line_index_stability_report(rows)
    assert report["source_line_index_stable"] is True
    assert report["stable_cases"] == 1
    assert report["failed_measurements"] == 0


def test_failed_calls_do_not_create_repeat_groups():
    """실패 호출은 반복 측정 그룹을 만들지 않는다."""
    rows = [
        _row(run_id="run-1", attempt=1, error="boom"),
        _row(run_id="run-2", attempt=1, error="boom"),
    ]
    groups = measure.repeated_measurement_groups(rows)
    assert groups == {}
    report = measure.line_index_stability_report(rows)
    assert report["source_line_index_stable"] is None
    assert report["failed_measurements"] == 2


# ===========================================================================
# 4. per-sample 정확도 — 첫 번째 성공 결과
# ===========================================================================

def test_per_sample_accuracy_uses_first_successful_result():
    """첫 호출이 실패하고 재호출이 성공하면 **성공 결과**를 사용한다."""
    labels = [{
        "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": ["2(x-3)=6"], "step_order": [1],
        "unreadable_steps": [],
        "line_kinds": {"0": "printed"},
        "expected_error": {"error_step": None, "error_type": None,
                           "misconception_ids": []},
    }]
    rows = [
        _row(run_id="run-1", attempt=1, error="RuntimeError: boom"),
        _row(run_id="run-2", attempt=1, draft=_draft()),
    ]
    report = measure.measure(labels, rows, {})
    acc = report["A_ocr_accuracy"]
    # 성공 결과로 채점된다
    assert acc["problem_latex_exact_comparable"] == 1
    assert acc["problem_latex_exact_match"] == 1.0
    assert acc["printed_handwritten_accuracy"] == 1.0
    # sample 은 실패로 세지 않는다 (성공 결과가 있으므로)
    assert report["counts"]["failures"] == 0
    assert report["counts"]["failed_attempts"] == 1


def test_sample_with_no_success_is_failure_and_unmeasured():
    """성공 결과가 하나도 없는 sample 만 실패/미측정이다."""
    labels = [{
        "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
        "expected_error": {"error_step": None, "error_type": None,
                           "misconception_ids": []},
    }]
    rows = [_row(run_id="run-1", attempt=1, error="boom")]
    report = measure.measure(labels, rows, {})
    assert report["counts"]["failures"] == 1
    assert report["counts"]["failed_attempts"] == 1
    assert report["A_ocr_accuracy"]["problem_latex_exact_comparable"] == 0
    assert report["A_ocr_accuracy"]["problem_latex_exact_match"] is None


# ===========================================================================
# 5. 운영 지표 — 모든 실제 호출 시도 기준
# ===========================================================================

def test_repeated_successful_calls_are_billed_and_timed():
    """반복 성공 호출의 **비용과 latency** 가 운영 지표에 반영된다."""
    labels = [{
        "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": ["2(x-3)=6"], "step_order": [1],
        "unreadable_steps": [],
        "expected_error": {"error_step": None, "error_type": None,
                           "misconception_ids": []},
    }]
    rows = [
        _row(run_id="run-1", attempt=1, draft=_draft(), elapsed=0.2),
        _row(run_id="run-2", attempt=1, draft=_draft(), elapsed=0.4),
    ]
    report = measure.measure(
        labels, rows, {"per_image_usd": 0.002, "per_page_usd": 0.005}
    )
    ops = report["C_operations"]
    # 2회 호출 → 2회 과금
    assert ops["cost_estimate_usd_total"] == pytest.approx(0.004)
    assert ops["latency_attempts"] == 2
    assert ops["latency_p50"] == pytest.approx(0.3)
    assert report["counts"]["attempts_total"] == 2


def test_failed_attempt_is_counted_as_attempt():
    """실패 시도도 시도 수·실패 시도 수에 반영된다."""
    labels = [{
        "id": "E01", "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
        "expected_error": {"error_step": None, "error_type": None,
                           "misconception_ids": []},
    }]
    rows = [
        _row(run_id="run-1", attempt=1, error="boom", elapsed=0.1),
        _row(run_id="run-2", attempt=1, draft=_draft(), elapsed=0.3),
    ]
    report = measure.measure(
        labels, rows, {"per_image_usd": 0.002, "per_page_usd": 0.005}
    )
    ops = report["C_operations"]
    assert report["counts"]["attempts_total"] == 2
    assert report["counts"]["failed_attempts"] == 1
    assert ops["latency_attempts"] == 2
    # 성공 호출 1회만 과금된다 (실패 시도는 draft 가 없어 행 수를 알 수 없다)
    assert ops["cost_estimate_usd_total"] == pytest.approx(0.002)
    assert ops["cases_cost_unknown"] == 1
    assert ops["attempt_failure_rate"] == pytest.approx(0.5)


def test_run_batch_then_measure_pipeline(tmp_path):
    """run_batch 산출물을 measure 가 그대로 읽는다 (attempt_id 포함)."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(count=2), {"E01": SECRET_URL, "E02": SECRET_URL2},
                   transport, root, run_id="run-p")
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert all(row["attempt_id"] for row in rows)
    report = measure.measure(
        _labels(count=2), rows, {"per_image_usd": 0.002, "per_page_usd": 0.005}
    )
    assert report["counts"]["attempts_total"] == 2
    assert report["counts"]["failed_attempts"] == 0
    assert report["C_operations"]["cost_estimate_usd_total"] == pytest.approx(0.004)
    assert report["D_label_key_stability"]["source_line_index_stable"] is None
