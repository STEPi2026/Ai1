"""eval/run_batch.py 계약 테스트 (PHASE 9-9B.6 · 9-9B.7).

**외부 네트워크를 호출하지 않는다.** 전부 **fake transport** 를 주입한다.
- fake transport 는 호출이 실제로 발생했는지 기록한다
- live transport 를 쓰려는 시도는 테스트에서 차단한다
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

SECRET_URL = "https://backend.example.invalid/eval/E01.jpg?X-Amz-Signature=DEADBEEFSECRET&X-Amz-Expires=900"
SECRET_URL2 = "https://backend.example.invalid/eval/E02.jpg?X-Amz-Signature=C0FFEE0123456789"


# ---------------------------------------------------------------------------
# fake transport — 외부 네트워크를 절대 쓰지 않는다
# ---------------------------------------------------------------------------

class FakeTransport:
    """가짜 전송 계층. 실제 네트워크 호출은 전혀 하지 않는다."""

    def __init__(self, fail_on=(), payload=None):
        self.calls = []
        self.fail_on = set(fail_on)
        self.payload = payload or {
            "version": "SuperNet-200",
            "line_data": [
                {
                    "id": "line-0",
                    "type": "text",
                    "text": "\\(2(x-3)=6\\)",
                    "confidence": 0.9,
                    "is_printed": True,
                    "is_handwritten": False,
                    "conversion_output": True,
                }
            ],
        }

    def __call__(self, image_reference, *, body, timeout):
        self.calls.append({"url": image_reference, "body": body, "timeout": timeout})
        if any(token in image_reference for token in self.fail_on):
            raise RuntimeError("boom: {}".format(image_reference))
        return json.loads(json.dumps(self.payload)), 0.25


def _labels(count=2, prefix="E"):
    return [
        {
            "id": "{}{:02d}".format(prefix, index),
            "category": "complete-correct",
            "problem_latex_text": "2(x-3)=6",
            "step_latex_texts": ["2(x-3)=6"],
            "step_order": [1],
            "unreadable_steps": [],
        }
        for index in range(1, count + 1)
    ]


def _manifest(count=2):
    urls = [SECRET_URL, SECRET_URL2]
    return {
        "E{:02d}".format(index): urls[index - 1] for index in range(1, count + 1)
    }


def _run(cases, manifest, transport, root, **kwargs):
    kwargs.setdefault("dry_run", False)
    kwargs.setdefault("max_calls", 10)
    return batch.run_batch(
        cases, manifest, transport=transport, local_root=root, **kwargs
    )


# ===========================================================================
# 1. 경로 검증 — eval/_local 밖으로는 쓰지 않는다
# ===========================================================================

def test_results_path_outside_local_root_is_rejected(tmp_path):
    """출력 경로가 허용 폴더 밖이면 **호출·파일 생성 없이** 실패한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root,
             results=tmp_path / "outside" / "results.jsonl")
    assert transport.calls == []
    assert not (tmp_path / "outside").exists()


def test_responses_dir_outside_local_root_is_rejected(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root,
             responses_dir=tmp_path / "elsewhere" / "responses")
    assert transport.calls == []
    assert not (tmp_path / "elsewhere").exists()


def test_run_log_outside_local_root_is_rejected(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root,
             run_log=tmp_path / "elsewhere" / "run_log.jsonl")
    assert transport.calls == []
    assert not (tmp_path / "elsewhere").exists()


def test_relative_traversal_path_is_rejected(tmp_path):
    """`../` 로 탈출하는 경로는 거부된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root,
             results=root / ".." / ".." / "evil.jsonl")
    assert transport.calls == []
    assert not (tmp_path.parent / "evil.jsonl").exists()


def test_windows_traversal_path_is_rejected(tmp_path):
    """Windows `..\\` 로 탈출하는 경로도 거부된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root,
             results=root / ".." / ".." / "evil2.jsonl")
    assert transport.calls == []
    assert not (tmp_path.parent / "evil2.jsonl").exists()


def test_cli_rejects_output_path_outside_local_root():
    """CLI 출력 경로도 `eval/_local` 밖이면 거부된다."""
    code = batch.main(["--results", "../evil.jsonl"])
    assert code == 1


def test_case_id_with_traversal_is_converted_to_safe_slug(tmp_path):
    """`case_id` 에 경로 탈출 문자가 있어도 **안전한 slug** 로 변환된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    evil_id = "../../../etc/passwd"
    labels = [{
        "id": evil_id, "category": "x", "problem_latex_text": "2(x-3)=6",
        "step_latex_texts": [], "step_order": [], "unreadable_steps": [],
    }]
    manifest = {evil_id: SECRET_URL}
    summary = _run(labels, manifest, transport, root, max_calls=1)
    responses = list((Path(summary["run_dir"]) / "responses").glob("*.json"))
    assert len(responses) == 1
    # 파일은 응답 폴더 안에만 있다
    assert responses[0].parent == Path(summary["run_dir"]) / "responses"
    assert "passwd" in responses[0].name
    # 원래 id 는 결과 행에 그대로 남는다 (추적성)
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["id"] == evil_id


def test_slug_collision_is_rejected(tmp_path):
    """서로 다른 ID 가 같은 파일명이 되면 실패한다 (조용한 덮어쓰기 방지)."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    labels = [
        {"id": "E/01", "category": "x", "problem_latex_text": "x",
         "step_latex_texts": [], "step_order": [], "unreadable_steps": []},
        {"id": "E_01", "category": "x", "problem_latex_text": "x",
         "step_latex_texts": [], "step_order": [], "unreadable_steps": []},
    ]
    manifest = {"E/01": SECRET_URL, "E_01": SECRET_URL2}
    with pytest.raises(batch.BatchError):
        _run(labels, manifest, transport, root, max_calls=2)
    assert transport.calls == []


def test_safe_slug_rejects_unusable_ids():
    """파일명으로 쓸 수 없는 ID 는 거부된다."""
    for bad in ("", "..", ".", "///"):
        with pytest.raises(batch.BatchError):
            batch.safe_slug(bad)


# ===========================================================================
# 2. 기존 산출물 보존 — 덮어쓰지 않는다
# ===========================================================================

def test_existing_results_file_is_not_overwritten(tmp_path):
    """기존 results 파일이 있으면 **덮어쓰지 않고** 실패한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    existing = root / "results.jsonl"
    existing.write_text("{\"id\": \"OLD\"}\n", encoding="utf-8")
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root, results=existing)
    assert transport.calls == []
    assert existing.read_text(encoding="utf-8") == '{"id": "OLD"}\n'


def test_existing_responses_dir_is_not_overwritten(tmp_path):
    """기존 응답 폴더가 있으면 실패한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    responses_dir = root / "responses"
    responses_dir.mkdir()
    (responses_dir / "E01__E01.json").write_text("{}", encoding="utf-8")
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root, responses_dir=responses_dir)
    assert transport.calls == []
    assert (responses_dir / "E01__E01.json").read_text(encoding="utf-8") == "{}"


def test_same_run_id_twice_fails_second_time(tmp_path):
    """같은 run_id 를 두 번 쓰면 두 번째는 실패한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    first = _run(_labels(), _manifest(), transport, root, run_id="fixed-run")
    assert first["run_id"] == "fixed-run"
    with pytest.raises(batch.BatchError):
        _run(_labels(), _manifest(), transport, root, run_id="fixed-run")
    assert len(transport.calls) == 2  # 두 번째 호출은 일어나지 않았다


def test_default_runs_use_distinct_run_ids_and_preserve_both(tmp_path):
    """기본 실행은 서로 다른 run_id 를 쓰고 **양쪽 모두 보존**된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    first = _run(_labels(), _manifest(), transport, root)
    second = _run(_labels(), _manifest(), transport, root)
    assert first["run_id"] != second["run_id"]
    assert first["run_dir"] != second["run_dir"]
    for summary in (first, second):
        results = Path(summary["results_file"])
        assert results.exists()
        rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line]
        assert len(rows) == 2
        responses = list((Path(summary["run_dir"]) / "responses").glob("*.json"))
        assert len(responses) == 2


def test_run_artifacts_stay_under_local_root(tmp_path):
    """모든 산출물이 허용 폴더 안이다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(), _manifest(), transport, root)
    run_dir = Path(summary["run_dir"])
    assert batch.is_under(root, run_dir)
    assert batch.is_under(root, Path(summary["results_file"]))
    assert (run_dir / "run_log.jsonl").exists()


# ===========================================================================
# 3. 누적 — M16 반복 측정 (같은 이미지끼리만)
# ===========================================================================

def _write_results(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def test_accumulate_merges_runs_of_same_sample(tmp_path):
    """같은 이미지의 반복 결과를 누적한다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    first = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root)
    second = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root)
    rows = batch.accumulate_measurements(
        [Path(first["results_file"]), Path(second["results_file"])]
    )
    assert len(rows) == 2
    groups = batch.measurement_groups(rows)
    assert len(groups) == 1
    (key, group), = groups.items()
    assert key == ("E01", "E01")
    assert len(group) == 2


def test_accumulate_rejects_case_pointing_to_different_samples(tmp_path):
    """같은 case id 가 다른 sample_id 를 가리키면 누적하지 않는다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    first = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root)
    second = _run(
        _labels(count=1),
        {"E01": {"sample_id": "E01-other", "url": SECRET_URL2}},
        transport, root,
    )
    with pytest.raises(batch.BatchError):
        batch.accumulate_measurements(
            [Path(first["results_file"]), Path(second["results_file"])]
        )


def test_different_images_are_not_compared_as_repeat_measurement(tmp_path):
    """**서로 다른 이미지는 반복 측정으로 계산하지 않는다.**"""
    rows = [
        {"id": "E01", "sample_id": "S1", "draft": {"regions": [{"source_line_index": 0,
         "type": "text", "kind": "printed", "text": "a"}]}},
        {"id": "E01", "sample_id": "S2", "draft": {"regions": [{"source_line_index": 0,
         "type": "text", "kind": "printed", "text": "DIFFERENT"}]}},
    ]
    groups = batch.measurement_groups(rows)
    assert groups == {}
    report = measure.line_index_stability_report(rows)
    assert report["source_line_index_stable"] is None
    assert report["cases_with_repeats"] == 0


def test_same_image_repeated_measurement_is_compared(tmp_path):
    """같은 이미지의 반복 호출은 줄 대응을 비교한다."""
    rows = [
        {"id": "E01", "sample_id": "S1", "draft": {"regions": [{"source_line_index": 0,
         "type": "text", "kind": "printed", "text": "a"}]}},
        {"id": "E01", "sample_id": "S1", "draft": {"regions": [{"source_line_index": 0,
         "type": "text", "kind": "printed", "text": "a"}]}},
    ]
    report = measure.line_index_stability_report(rows)
    assert report["source_line_index_stable"] is True
    assert report["stable_cases"] == 1


# ===========================================================================
# 4. 응답 추적성 — 변환 실패 후에도 원본 응답 경로가 남아 있다
# ===========================================================================

def test_raw_response_file_recorded_before_adapter_conversion(tmp_path, monkeypatch):
    """adapter 변환이 실패해도 **원본 응답 경로**를 결과에서 찾을 수 있다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()

    def boom(payload):
        raise RuntimeError("변환 실패")

    monkeypatch.setattr(adapter, "draft_from_response", boom)
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)

    assert summary["failed"] == 1
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1
    row = rows[0]
    assert row["draft"] is None
    assert row["error"]
    # **원본 응답 경로가 남아 있다** — 파일도 실제로 존재한다
    assert row["raw_response_file"]
    raw_path = Path(row["raw_response_file"])
    assert raw_path.exists()
    assert batch.is_under(root, raw_path)
    assert "변환 실패" in row["error"]


def test_successful_row_has_raw_response_file(tmp_path):
    """정상 행도 원본 응답 경로를 갖는다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["raw_response_file"]
    assert Path(rows[0]["raw_response_file"]).exists()
    assert rows[0]["draft"] is not None


# ===========================================================================
# 5. 호출 상한 — API 경계에서도 거부한다
# ===========================================================================

def test_run_batch_rejects_non_positive_max_calls(tmp_path):
    """`run_batch()` 자체가 `max_calls <= 0` 을 거부한다 (CLI 만으로 안 된다)."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    for value in (0, -1, -10):
        with pytest.raises(batch.BatchError):
            _run(_labels(), _manifest(), transport, root, max_calls=value)
        with pytest.raises(batch.BatchError):
            batch.run_batch(
                _labels(), _manifest(), transport=transport, local_root=root,
                dry_run=True, max_calls=value,
            )
    assert transport.calls == []


def test_live_run_requires_max_calls(tmp_path):
    """실제 호출은 `--max-calls` 가 없으면 거부된다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError) as excinfo:
        batch.run_batch(
            _labels(), _manifest(), transport=transport, local_root=root, dry_run=False
        )
    assert "max_calls" in str(excinfo.value)
    assert transport.calls == []


# ===========================================================================
# 6. 실행 모드 — 기본 dry-run
# ===========================================================================

def test_default_is_dry_run_and_makes_no_call(tmp_path):
    """기본은 dry-run — 호출 0회, 파일도 안 쓴다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = batch.run_batch(
        _labels(), _manifest(), transport=transport, local_root=root
    )
    assert summary["mode"] == "dry-run"
    assert summary["called"] == 0
    assert transport.calls == []
    assert not (root / "runs").exists()
    assert summary["results_file"] is None


def test_cli_requires_run_and_max_calls_together():
    """CLI 에서 `--run` 만 주면 실패한다."""
    with pytest.raises(SystemExit):
        batch.main(["--run"])
    with pytest.raises(SystemExit):
        batch.main(["--run", "--max-calls", "0"])


def test_max_calls_caps_the_number_of_calls(tmp_path):
    """호출 상한이 지켜진다."""
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(
        _labels(count=3),
        {
            "E01": SECRET_URL,
            "E02": SECRET_URL2,
            "E03": "https://backend.example.invalid/eval/E03.jpg?X-Amz-Signature=ABC",
        },
        transport, root, max_calls=2,
    )
    assert summary["called"] == 2
    assert len(transport.calls) == 2


# ===========================================================================
# 7. 호출 전 검증 — manifest 없음 / ID 대응 오류
# ===========================================================================

def test_missing_manifest_fails_before_any_call(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), {}, transport, root)
    assert transport.calls == []


def test_manifest_without_manifest_file_fails(tmp_path):
    with pytest.raises(batch.BatchError) as excinfo:
        batch.load_manifest(tmp_path / "nope.json")
    assert "manifest" in str(excinfo.value)


def test_id_mismatch_fails_before_any_call(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(), {"E99": SECRET_URL}, transport, root)
    with pytest.raises(batch.BatchError):
        _run(
            _labels(),
            {"E01": SECRET_URL, "E02": SECRET_URL2, "E77": "https://x.invalid/a.jpg"},
            transport, root,
        )
    assert transport.calls == []


def test_non_https_url_is_rejected_before_call(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    with pytest.raises(batch.BatchError):
        _run(_labels(count=1), {"E01": "http://backend.example.invalid/a.jpg"},
             transport, root)
    assert transport.calls == []


# ===========================================================================
# 8. URL 비노출
# ===========================================================================

def test_url_never_appears_in_results_jsonl(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)
    text = Path(summary["results_file"]).read_text(encoding="utf-8")
    assert SECRET_URL not in text
    assert "DEADBEEFSECRET" not in text
    assert "X-Amz-Signature" not in text


def test_url_never_appears_in_run_log(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)
    text = (Path(summary["run_dir"]) / "run_log.jsonl").read_text(encoding="utf-8")
    assert SECRET_URL not in text
    assert "DEADBEEFSECRET" not in text


def test_url_is_sanitized_from_error_messages(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport(fail_on=("DEADBEEFSECRET",))
    summary = _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)
    assert summary["failed"] == 1
    text = Path(summary["results_file"]).read_text(encoding="utf-8")
    assert SECRET_URL not in text
    assert "DEADBEEFSECRET" not in text
    assert batch.REDACTED in text


def test_summary_contains_no_url(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    summary = _run(_labels(), _manifest(), transport, root)
    blob = json.dumps(summary, ensure_ascii=False)
    assert SECRET_URL not in blob
    assert SECRET_URL2 not in blob
    assert "DEADBEEFSECRET" not in blob


def test_sanitize_replaces_full_url_and_query():
    secrets = batch.secret_list([{"url": SECRET_URL}])
    out = batch.sanitize(
        "failed for {} (query {})".format(SECRET_URL, "X-Amz-Signature=DEADBEEFSECRET"),
        secrets,
    )
    assert SECRET_URL not in out
    assert "DEADBEEFSECRET" not in out
    assert batch.REDACTED in out


# ===========================================================================
# 9. 산출물 구조 — measure.py 입력 형식 호환
# ===========================================================================

def test_results_row_shape_matches_measure_expectations(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    labels = _labels(count=2)
    summary = _run(labels, _manifest(), transport, root)
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2
    for row in rows:
        assert row["id"]
        assert "elapsed_seconds" in row
        assert "error" in row
        assert "draft" in row
        assert row["sample_id"]
        assert row["raw_response_file"]


def test_results_jsonl_is_readable_by_measure(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    labels = _labels(count=2)
    summary = _run(labels, _manifest(), transport, root)
    results = [json.loads(line) for line
               in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    report = measure.measure(labels, results, {"per_image_usd": 0.002, "per_page_usd": 0.005})
    assert report["counts"]["measured"] == 2
    assert report["counts"]["failures"] == 0
    assert report["C_operations"]["cost_estimate_usd_total"] == pytest.approx(0.004)
    assert SECRET_URL not in json.dumps(report, ensure_ascii=False)


def test_failure_is_preserved_and_run_continues(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport(fail_on=("DEADBEEFSECRET",))
    summary = _run(_labels(count=2), _manifest(), transport, root)
    assert summary["called"] == 2
    assert summary["failed"] == 1
    assert summary["succeeded"] == 1
    rows = [json.loads(line) for line
            in Path(summary["results_file"]).read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2
    failed = [row for row in rows if row["error"]]
    assert len(failed) == 1
    assert failed[0]["id"] == "E01"
    assert failed[0]["draft"] is None
    assert failed[0]["raw_response_file"] is None


# ===========================================================================
# 10. 요청 본문 불변조건
# ===========================================================================

def test_batch_request_body_always_opts_out(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    transport = FakeTransport()
    _run(_labels(count=1), {"E01": SECRET_URL}, transport, root, max_calls=1)
    assert len(transport.calls) == 1
    body = transport.calls[0]["body"]
    assert body["metadata"]["improve_mathpix"] is False
