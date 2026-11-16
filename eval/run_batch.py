"""오프라인 배치 러너 — E01~E16 세트를 순차 호출해 `results.jsonl` 을 만든다.

**이 Phase 에서 실제 OCR/API 호출은 하지 않는다.** 기본은 **dry-run** 이고,
외부 호출은 `--run` 과 `--max-calls` 를 **둘 다** 명시해야만 가능하다.

입력
    `eval/_local/labels.json`         — 정답 라벨 (스키마: `eval/labels.schema.json`)
    `eval/_local/presigned_urls.json` — **로컬 평가 자료** presigned URL manifest.
      Backend presigned URL **발급은 여기서 하지 않는다** — 실행 직전에 준비한다.
      URL 은 **만료되는 bearer 정보**로 취급한다.

출력 — **전부 `eval/_local/runs/<run_id>/` 아래** (gitignore 대상)
    `responses/<case>__<sample>.json` — 원본 응답
    `results.jsonl`                    — `measure.py` 가 읽는 행
    `run_log.jsonl`                    — 샘플 단위 실행 로그

**불변조건**
    1. **경로** — 출력 경로는 resolve 기준으로 `eval/_local/` **안**이어야 한다.
       밖으로 나가면 호출·파일 생성 없이 실패한다.
    2. **파일명** — `case_id`/`sample_id` 는 안전한 slug 로 변환해 쓴다.
       경로 탈출 문자를 제거하고, slug 충돌이 있으면 실패한다.
    3. **덮어쓰기** — 기본 실행은 기존 결과를 **덮어쓰지 않는다.**
       실행마다 충돌 없는 `run_id` 하위 경로를 쓴다. 지정 경로가 이미 있으면 실패한다.
    4. **URL 비노출** — presigned URL 은 **stdout · 로그 · `results.jsonl` · 리포트에
       기록하지 않는다.**
    5. **추적성** — 원본 응답을 저장한 **직후** 결과 행에 `raw_response_file` 을 기록한다.
       adapter 변환이 실패해도 원본 응답 경로로追溯할 수 있다.
    6. **호출 상한** — `run_batch()` 가 `max_calls <= 0` 을 거부한다 (CLI 만으로 막지 않는다).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

EVAL_ROOT = Path(__file__).resolve().parent
LOCAL_ROOT = (EVAL_ROOT / "_local").resolve()
RUNS_DIR_NAME = "runs"

#: URL 이 들어갈 수 있는 자리에서 쓰는 대체 문자열.
REDACTED = "<redacted-presigned-url>"

#: 파일명에 남길 수 있는 문자.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_SLUG_LEN = 64


class BatchError(RuntimeError):
    """배치 실행 **전** 검증 실패 — 호출이 일어나기 전에 raise 된다."""


# ---------------------------------------------------------------------------
# 경로 검증
# ---------------------------------------------------------------------------

def _norm(path: Path) -> str:
    return os.path.normcase(str(path))


def is_under(root: Path, target: Path) -> bool:
    """``target`` 이 ``root`` 안(또는 root 자신)인지."""
    root_s = _norm(Path(root).resolve())
    target_s = _norm(Path(target).resolve())
    if target_s == root_s:
        return True
    return target_s.startswith(root_s + os.sep)


def assert_under(root: Path, target: Path, label: str) -> Path:
    """``target`` 이 ``root`` 밖이면 `BatchError`. resolve 기준 판정."""
    resolved = Path(target).resolve()
    if not is_under(Path(root), resolved):
        raise BatchError(
            "{} 경로가 허용 폴더 밖이다 — {} 밖에는 쓰지 않는다: {}".format(
                label, Path(root), target
            )
        )
    return resolved


def safe_slug(value: str) -> str:
    """ID 를 **파일명에 안전하게** 쓸 수 있는 slug 로 변환한다.

    경로 구분자(`/` · `\\`) 와 그 밖의 특수문자를 `_` 로 바꾼다.
    `""` · `"."` · `".."` 로 만들 수 없게 한다 — 경로 탈출 방지.
    """
    if value is None:
        raise BatchError("ID 가 없다")
    text = _UNSAFE_CHARS.sub("_", str(value)).strip("._-")
    if len(text) > _MAX_SLUG_LEN:
        text = text[:_MAX_SLUG_LEN].strip("._-")
    if not text:
        raise BatchError("ID 를 파일명으로 바꿀 수 없다: {!r}".format(value))
    if text in (".", ".."):
        raise BatchError("ID 를 파일명으로 바꿀 수 없다: {!r}".format(value))
    if "/" in text or "\\" in text:
        raise BatchError("ID 가 경로 탈출 문자를 포함한다: {!r}".format(value))
    return text


def response_file_names(plan: list[dict]) -> dict:
    """계획 항목 → 응답 파일명. **slug 충돌이 있으면 실패한다.**"""
    names: dict[str, dict] = {}
    for item in plan:
        name = "{}__{}".format(
            safe_slug(item["case_id"]), safe_slug(item["sample_id"])
        )
        if name in names:
            raise BatchError(
                "ID 를 파일명으로 바꾼 결과가 충돌한다: {!r} / {!r}".format(
                    names[name]["case_id"], item["case_id"]
                )
            )
        names[name] = item
    return names


# ---------------------------------------------------------------------------
# 실행 경로 · run_id
# ---------------------------------------------------------------------------

def new_run_id() -> str:
    """충돌 가능성이 거의 없는 실행 ID."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "{}-{}".format(stamp, uuid.uuid4().hex[:6])


def resolve_run_paths(
    local_root: Path,
    run_id: str,
    *,
    responses_dir: Path | None = None,
    results: Path | None = None,
    run_log: Path | None = None,
) -> dict:
    """실행 산출물 경로를 정하고 **모두 local_root 안인지** 검증한다."""
    root = Path(local_root).resolve()
    safe_run_id = safe_slug(run_id)
    run_dir = root / RUNS_DIR_NAME / safe_run_id

    paths = {
        "run_dir": run_dir,
        "responses_dir": Path(responses_dir) if responses_dir else run_dir / "responses",
        "results": Path(results) if results else run_dir / "results.jsonl",
        "run_log": Path(run_log) if run_log else run_dir / "run_log.jsonl",
    }
    for key in ("responses_dir", "results", "run_log"):
        paths[key] = assert_under(root, paths[key], key)
    return paths


def assert_not_existing(paths: dict) -> None:
    """**기존 산출물을 덮어쓰지 않는다.** 하나라도 있으면 실패한다."""
    for key in ("responses_dir", "results", "run_log"):
        target = Path(paths[key])
        if target.exists():
            raise BatchError(
                "{} 가 이미 존재한다 — 덮어쓰지 않는다. 다른 run-id/경로를 쓴다: {}".format(
                    key, target
                )
            )


# ---------------------------------------------------------------------------
# 입력 로딩 · 검증
# ---------------------------------------------------------------------------

def load_labels(path: Path) -> list[dict]:
    """라벨 파일을 읽는다. 없으면 `BatchError`."""
    path = Path(path)
    if not path.exists():
        raise BatchError("라벨 파일이 없다: {}".format(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BatchError("라벨 파일 JSON 이 아니다: {}".format(exc)) from None
    if isinstance(payload, dict):
        cases = payload.get("cases") or []
    else:
        cases = payload
    if not cases:
        raise BatchError("라벨에 케이스가 없다: {}".format(path))
    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not case_id:
            raise BatchError("라벨에 id 가 없는 케이스가 있다")
        if case_id in seen:
            raise BatchError("라벨에 중복 id 가 있다: {}".format(case_id))
        seen.add(case_id)
    return cases


def load_manifest(path: Path) -> dict:
    """presigned URL manifest 를 읽는다. 없으면 `BatchError`."""
    path = Path(path)
    if not path.exists():
        raise BatchError(
            "presigned URL manifest 가 없다: {}\n"
            "Backend presigned URL 발급은 이 도구가 하지 않는다 — 실행 직전에 "
            "준비해 manifest 파일로 저장한 뒤 다시 실행한다.".format(path)
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BatchError("manifest 가 JSON 이 아니다: {}".format(exc)) from None
    if not isinstance(payload, dict) or not payload:
        raise BatchError("manifest 가 비었거나 형식이 아니다: {}".format(path))
    return payload


def normalize_manifest(payload: dict) -> dict:
    """manifest 를 ``{case_id: {"sample_id": str, "url": str}}`` 로 정규화."""
    out: dict[str, dict] = {}
    for case_id, entry in payload.items():
        if isinstance(entry, str):
            out[case_id] = {"sample_id": case_id, "url": entry}
        elif isinstance(entry, dict):
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                raise BatchError("manifest 항목에 url 이 없다: {}".format(case_id))
            out[case_id] = {
                "sample_id": str(entry.get("sample_id") or case_id),
                "url": url,
            }
        else:
            raise BatchError("manifest 항목 형식이 아니다: {}".format(case_id))
    return out


def build_plan(cases: list[dict], manifest: dict) -> list[dict]:
    """호출 계획을 만든다. **ID 대응이 틀리면 여기서 raise 된다.**"""
    normalized = normalize_manifest(manifest)
    case_ids = [case["id"] for case in cases]

    missing = [cid for cid in case_ids if cid not in normalized]
    if missing:
        raise BatchError(
            "manifest 에 없는 라벨 id {}건 (호출하지 않는다): {}".format(
                len(missing), ", ".join(missing[:10])
            )
        )
    extra = [cid for cid in normalized if cid not in set(case_ids)]
    if extra:
        raise BatchError(
            "manifest 에 라벨에 없는 id {}건 (ID 대응 오류로 본다): {}".format(
                len(extra), ", ".join(sorted(extra)[:10])
            )
        )

    plan: list[dict] = []
    for case in cases:
        case_id = case["id"]
        entry = normalized[case_id]
        url = entry["url"]
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise BatchError(
                "case {} 의 URL 이 https 가 아니다 (호출하지 않는다)".format(case_id)
            )
        if not parts.netloc:
            raise BatchError("case {} 의 URL 이 잘못된 형식이다".format(case_id))
        plan.append(
            {
                "case_id": case_id,
                "sample_id": entry["sample_id"],
                "url": url,
                "category": case.get("category"),
            }
        )
    return plan


# ---------------------------------------------------------------------------
# URL 비노출
# ---------------------------------------------------------------------------

def url_variants(url: str) -> list[str]:
    """URL 에서 나올 수 있는 민감 조각들.

    전체 URL · 경로+쿼리 · 쿼리문자열 · **개별 쿼리 파라미터**까지 포함한다.
    오류 메시지에 서명값만 따로 노출되는 경우도 막기 위함이다.
    """
    parts = urlsplit(url)
    variants = [url]
    if parts.query:
        variants.append(parts.query)
        variants.append(parts.path + "?" + parts.query)
        for pair in parts.query.split("&"):
            if not pair:
                continue
            variants.append(pair)
            # `key=value` 에서 값만 떼어내도 secret 으로 본다
            _, _, value = pair.partition("=")
            if len(value) >= 8:
                variants.append(value)
    # 긴 것부터 치환해야 부분 치환이 남지 않는다
    return sorted(set(variants), key=len, reverse=True)


def sanitize(text: str, secrets: list[str]) -> str:
    """민감 문자열(presigned URL)을 치환한다."""
    if not text:
        return text
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, REDACTED)
    return out


def secret_list(plan: list[dict]) -> list[str]:
    """계획에 등장하는 모든 URL 조각."""
    secrets: list[str] = []
    for item in plan:
        secrets.extend(url_variants(item["url"]))
    return sorted(set(secrets), key=len, reverse=True)


# ---------------------------------------------------------------------------
# 누적 (M16 반복 측정)
# ---------------------------------------------------------------------------

def accumulate_measurements(results_paths: list[Path]) -> list[dict]:
    """여러 실행의 결과 행을 **누적**한다 — M16 반복 측정용.

    규칙
      - **같은 파일 경로**를 두 번 주면 거부한다 (같은 호출을 반복으로 세지 않는다).
      - **같은 ``run_id``** 를 두 번 주면 거부한다.
      - 파일 안에 **여러 run_id 가 섞여 있으면** 거부한다.
      - ``run_id`` 가 **없는 행**이 있으면 거부한다.
      - ``attempt_id`` 가 중복되면 거부한다 — 독립된 호출이 아니다.
      - 같은 ``case_id`` 안에서 **같은 ``sample_id``** 끼리만 반복 측정으로 묶인다.
      - 같은 ``case_id`` 가 서로 다른 ``sample_id`` 를 가리키면 **BatchError**
        (평가 id ↔ sample 대응이 어긋난 것).
      - **서로 다른 이미지를 반복 측정으로 계산하지 않는다.**
    """
    seen_files: set = set()
    seen_run_ids: dict = {}
    seen_attempts: set = set()
    by_case: dict[str, set] = {}
    rows_out: list[dict] = []

    for path in results_paths:
        path = Path(path)
        resolved = path.resolve()
        if resolved in seen_files:
            raise BatchError(
                "같은 결과 파일이 중복 제공됐다 — 반복 측정으로 세지 않는다: {}".format(path)
            )
        seen_files.add(resolved)
        if not path.exists():
            raise BatchError("결과 파일이 없다: {}".format(path))

        file_rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not file_rows:
            raise BatchError("결과 파일이 비었다: {}".format(path))

        run_ids = {row.get("run_id") for row in file_rows}
        if None in run_ids or "" in run_ids:
            raise BatchError(
                "run_id 가 없는 결과 행이 있다 — run_id 없이 누적하지 않는다: {}".format(path)
            )
        if len(run_ids) > 1:
            raise BatchError(
                "결과 파일에 여러 run_id 가 섞여 있다: {} ({})".format(
                    path, ", ".join(sorted(run_ids))
                )
            )
        run_id = run_ids.pop()
        if run_id in seen_run_ids:
            raise BatchError(
                "run_id 가 중복됐다: {} ({} 와 {})".format(
                    run_id, seen_run_ids[run_id], path
                )
            )
        seen_run_ids[run_id] = path

        for row in file_rows:
            attempt_id = row.get("attempt_id")
            if not attempt_id:
                raise BatchError(
                    "attempt_id 가 없는 결과 행이 있다: {}".format(path)
                )
            if attempt_id in seen_attempts:
                raise BatchError(
                    "attempt_id 가 중복됐다 — 독립된 호출이 아니다: {}".format(attempt_id)
                )
            seen_attempts.add(attempt_id)

            case_id = row.get("id")
            sample_id = row.get("sample_id") or case_id
            seen = by_case.setdefault(case_id, set())
            if seen and sample_id not in seen:
                raise BatchError(
                    "case {} 가 서로 다른 sample_id 를 가리킨다 — 누적 대상이 아니다: "
                    "{} vs {}".format(case_id, ", ".join(sorted(seen)), sample_id)
                )
            seen.add(sample_id)
            rows_out.append(row)
    return rows_out


def measurement_groups(rows: list[dict]) -> dict:
    """``(case_id, sample_id)`` 별 반복 측정 그룹 — **성공 호출만**, 2건 이상.

    실패 호출은 줄 인덱스 대응이 없으므로 반복 측정으로 세지 않는다.
    """
    from measure import is_successful_row

    buckets: dict[tuple, list] = {}
    for row in rows:
        if not is_successful_row(row):
            continue
        case_id = row.get("id")
        sample_id = row.get("sample_id") or case_id
        buckets.setdefault((case_id, sample_id), []).append(row)
    return {key: value for key, value in buckets.items() if len(value) >= 2}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def _default_transport(image_reference: str, *, body: dict, timeout: float):
    """실제 호출 래퍼. `mathpix_client` 로 위임한다."""
    import mathpix_client as client

    return client.call_text(image_reference, body=body, timeout=timeout)


def _build_request_body(url: str) -> dict:
    """요청 본문. `improve_mathpix=false` 는 항상 포함된다."""
    import mathpix_client as client

    return client.build_body(url)


def run_batch(
    cases: list[dict],
    manifest: dict,
    *,
    transport=None,
    local_root: Path = LOCAL_ROOT,
    responses_dir: Path | None = None,
    results: Path | None = None,
    run_log: Path | None = None,
    run_id: str | None = None,
    max_calls: int | None = None,
    dry_run: bool = True,
    timeout: float = 30.0,
) -> dict:
    """배치를 실행한다.

    ``transport`` 는 ``transport(image_reference, *, body, timeout) -> (payload, elapsed)``
    시그니처여야 한다. 테스트는 **fake transport** 를 주입한다.

    ``dry_run=True`` (기본) 이면 **호출을 전혀 하지 않고 파일도 쓰지 않는다.**
    """
    # API 경계 — CLI 검사만으로 충분하지 않다.
    if max_calls is not None and max_calls <= 0:
        raise BatchError(
            "max_calls 는 1 이상이어야 한다 (받은 값: {})".format(max_calls)
        )
    if dry_run is False and max_calls is None:
        raise BatchError("실제 호출에는 호출 상한이 필요하다 — max_calls 를 지정한다.")

    run_id = run_id or new_run_id()
    paths = resolve_run_paths(
        local_root, run_id,
        responses_dir=responses_dir, results=results, run_log=run_log,
    )

    plan = build_plan(cases, manifest)
    file_names = response_file_names(plan)
    secrets = secret_list(plan)

    selected = plan if max_calls is None else plan[:max_calls]
    selected_names = {
        name: item
        for name, item in file_names.items()
        if item["case_id"] in {i["case_id"] for i in selected}
    }

    summary = {
        "mode": "dry-run" if dry_run else "run",
        "run_id": run_id,
        "run_dir": str(paths["run_dir"]),
        "cases_total": len(cases),
        "planned": len(selected),
        "called": 0,
        "succeeded": 0,
        "failed": 0,
        "provider_failures": 0,
        "adapter_failures": 0,
        "skipped": len(plan) - len(selected),
        "results_file": None,
        "case_ids": [item["case_id"] for item in selected],
        "errors": [],
    }

    if dry_run:
        # dry-run 은 **아무것도 쓰지 않고** 호출도 하지 않는다.
        return summary

    assert_not_existing(paths)

    import mathpix_adapter as adapter

    paths["responses_dir"].mkdir(parents=True, exist_ok=True)
    paths["results"].parent.mkdir(parents=True, exist_ok=True)
    paths["run_log"].parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    with paths["results"].open("w", encoding="utf-8") as results_file, \
            paths["run_log"].open("w", encoding="utf-8") as log_file:
        for attempt_no, item in enumerate(selected, start=1):
            case_id = item["case_id"]
            sample_id = item["sample_id"]
            name = next(
                key for key, value in selected_names.items()
                if value["case_id"] == case_id
            )
            # **호출별 식별자** — 같은 파일을 두 번 넣어도 반복으로 세지지 않게 한다.
            attempt_id = "{}#{:04d}".format(run_id, attempt_no)
            row = {
                "id": case_id,
                "sample_id": sample_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "attempt_no": attempt_no,
                "raw_response_file": None,
                "draft": None,
                "elapsed_seconds": None,
                "error": None,
                # provider 호출 결과와 adapter 변환 결과를 **구분**한다.
                "provider_succeeded": False,
                "adapter_succeeded": False,
                "error_stage": None,
            }
            event = "ok"

            # 1단계 — 호출 + 원본 응답 저장
            started = time.perf_counter()
            try:
                body = _build_request_body(item["url"])
                payload, elapsed = transport(item["url"], body=body, timeout=timeout)
            except Exception as exc:
                elapsed = time.perf_counter() - started
                message = sanitize(
                    "{}: {}".format(type(exc).__name__, exc), secrets
                )
                row["error"] = message
                row["elapsed_seconds"] = elapsed
                # **provider 실패** — 요청이 벤더에 닿지 못했다.
                row["provider_succeeded"] = False
                row["adapter_succeeded"] = False
                row["error_stage"] = "provider"
                summary["failed"] += 1
                summary["provider_failures"] += 1
                summary["errors"].append(
                    {"case_id": case_id, "sample_id": sample_id,
                     "error_stage": "provider", "error": message}
                )
                event = "error"
            else:
                raw_path = paths["responses_dir"] / "{}.json".format(name)
                raw_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                # **원본 응답을 저장한 직후** 기록한다 — 이후 변환이 실패해도
                # 결과 행에서 원본 응답 경로를 찾을 수 있다.
                row["raw_response_file"] = str(raw_path)
                # **provider 호출은 성공** — 응답을 받았고 원본도 저장했다.
                row["provider_succeeded"] = True
                row["elapsed_seconds"] = elapsed
                # 2단계 — adapter 변환
                try:
                    row["draft"] = adapter.draft_from_response(payload)
                    row["adapter_succeeded"] = True
                    row["error_stage"] = None
                    summary["succeeded"] += 1
                except Exception as exc:
                    message = sanitize(
                        "{}: {}".format(type(exc).__name__, exc), secrets
                    )
                    row["error"] = message
                    # **provider 성공 + adapter 실패** — provider 실패로 세지 않는다.
                    row["adapter_succeeded"] = False
                    row["error_stage"] = "adapter"
                    summary["failed"] += 1
                    summary["adapter_failures"] += 1
                    summary["errors"].append(
                        {"case_id": case_id, "sample_id": sample_id,
                         "error_stage": "adapter", "error": message}
                    )
                    event = "error"

            summary["called"] += 1
            rows.append(row)
            results_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            log_file.write(
                json.dumps(
                    {
                        "case_id": case_id,
                        "sample_id": sample_id,
                        "run_id": run_id,
                        "attempt_id": attempt_id,
                        "event": event,
                        "elapsed_seconds": row["elapsed_seconds"],
                        "error": row["error"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary["results_file"] = str(paths["results"])
    return summary


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(
        description="Mathpix 평가 배치 러너 (기본 dry-run)"
    )
    parser.add_argument("--labels", default=str(LOCAL_ROOT / "labels.json"))
    parser.add_argument("--manifest", default=str(LOCAL_ROOT / "presigned_urls.json"))
    parser.add_argument("--responses-dir", default=None)
    parser.add_argument("--results", default=None)
    parser.add_argument("--run-log", default=None)
    parser.add_argument("--run-id", default=None, help="실행 ID (기본 자동 생성)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="호출 상한. **실제 호출(--run)에는 필수다.**",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="실제 호출한다. **--max-calls 와 함께 지정해야 한다.** (기본은 dry-run)",
    )
    parser.add_argument(
        "--accumulate",
        nargs="+",
        default=None,
        metavar="RESULTS",
        help="누적 모드 — 여러 실행의 results.jsonl 을 합친다 (M16 반복 측정용). "
             "호출은 하지 않는다.",
    )
    parser.add_argument(
        "--accumulated-out",
        default=None,
        help="누적 결과 저장 경로 (기본 eval/_local/accumulated/results.jsonl)",
    )
    args = parser.parse_args(argv)

    # CLI 출력 경로는 **eval/_local 안**이어야 한다.
    for label, value in (
        ("responses-dir", args.responses_dir),
        ("results", args.results),
        ("run-log", args.run_log),
    ):
        if value:
            try:
                assert_under(LOCAL_ROOT, Path(value), label)
            except BatchError as exc:
                print("CLI 출력 경로 검증 실패: {}".format(exc))
                return 1

    if args.accumulate:
        return _accumulate_cli(args)

    if args.run and args.max_calls is None:
        parser.error(
            "실제 호출은 `--run` 과 `--max-calls` 를 **둘 다** 지정해야 한다. "
            "(실행 모드: dry-run)"
        )
    if args.max_calls is not None and args.max_calls <= 0:
        parser.error("--max-calls 는 1 이상이어야 한다")

    try:
        cases = load_labels(Path(args.labels))
        manifest = load_manifest(Path(args.manifest))
        summary = run_batch(
            cases,
            manifest,
            transport=_default_transport,
            local_root=LOCAL_ROOT,
            responses_dir=Path(args.responses_dir) if args.responses_dir else None,
            results=Path(args.results) if args.results else None,
            run_log=Path(args.run_log) if args.run_log else None,
            run_id=args.run_id,
            max_calls=args.max_calls,
            dry_run=not args.run,
            timeout=args.timeout,
        )
    except BatchError as exc:
        print("배치 실행 전 검증 실패: {}".format(exc))
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _accumulate_cli(args) -> int:  # pragma: no cover - CLI
    """누적 모드 — 호출 없이 여러 실행의 결과를 합친다."""
    out_path = Path(args.accumulated_out) if args.accumulated_out else (
        LOCAL_ROOT / "accumulated" / "results.jsonl"
    )
    try:
        assert_under(LOCAL_ROOT, out_path, "accumulated-out")
        # **조용히 덮어쓰지 않는다** — 기존 누적 산출물이 있으면 실패한다.
        if out_path.exists():
            raise BatchError(
                "누적 산출물이 이미 있다 — 덮어쓰지 않는다. 다른 경로를 쓰거나 "
                "기존 파일을 지운다: {}".format(out_path)
            )
        rows = accumulate_measurements([Path(p) for p in args.accumulate])
    except BatchError as exc:
        print("누적 실패: {}".format(exc))
        return 1

    groups = measurement_groups(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "mode": "accumulate",
        "rows": len(rows),
        "run_ids": sorted({row.get("run_id") for row in rows if row.get("run_id")}),
        "repeated_measurement_groups": len(groups),
        "groups": [
            {"case_id": key[0], "sample_id": key[1], "measurements": len(value)}
            for key, value in sorted(groups.items())
        ],
        "accumulated_file": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
