"""Google Cloud Vision OCR 데모 평가기 — **dry-run 기본**.

**이번 단계에서 실제 Google Vision API 는 호출하지 않는다.** 기본이 dry-run 이고,
실제 호출은 `--run` 과 `--max-calls` 를 **둘 다** 명시해야만 가능하다.

**과금 규칙 (Cloud Vision)**
  - OCR **기능별** 첫 1,000 units/month/**billing account** 가 무료다.
  - 이 데모는 **한 이미지당 OCR 기능 하나만** 호출한다
    (`DOCUMENT_TEXT_DETECTION`). `TEXT_DETECTION` 을 함께 부르면 같은 이미지의
    **무료 unit 을 두 번** 소비한다 — 그래서 금지한다.
  - **전체 누적 상한은 40회**로 둔다 (실행마다가 아니라 누적).
  - **예산 알림만으로 과금을 막을 수 있다고 가정하지 않는다.** 실제 잔량과
    결제 설정을 확인한 뒤 별도 지시가 있을 때만 호출한다.

**credential** — ADC 또는 로컬 환경 설정만 쓴다. 저장소·로그·보고서에 secret 을
남기지 않는다.

**하지 않는 것** — OcrResult 로 추측 변환하지 않는다. `problem` · `solution_steps` ·
printed/handwritten · LaTeX 를 만들지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import google_vision_adapter as vision_adapter  # noqa: E402
import google_vision_client as client  # noqa: E402

#: **누적** 호출 상한 — 실측 전체 합계가 이 값을 넘으면 실행을 거부한다.
CUMULATIVE_CALL_CAP = 40

#: 로그/결과 디렉터리 (전부 `eval/_local/` 아래 — gitignore 대상).
GOOGLE_DIR = client.GOOGLE_DIR
RAW_DIR = client.RAW_DIR
RESULTS_DIR = client.RESULTS_DIR
CALL_LOG_PATH = GOOGLE_DIR / "call_log.jsonl"
#: **호출 슬롯 원장** — API 요청 *전에* 예약을 기록한다(flush+fsync).
#: timeout·프로세스 중단처럼 과금 여부를 모르는 시도도 여기 남으므로 상한에 포함된다.
BUDGET_LEDGER_PATH = GOOGLE_DIR / "call_budget.jsonl"
#: 동시 실행을 직렬화하기 위한 잠금 파일.
LOCK_PATH = GOOGLE_DIR / "call_budget.lock"
MANIFEST_PATH = GOOGLE_DIR / "images.json"

#: 잠금 획득 대기(초) · 고아 잠금 정리 기준(초).
class ProbeError(RuntimeError):
    """데모 평가기 실행 전 검증 실패."""


# ---------------------------------------------------------------------------
# 누적 호출 예산
# ---------------------------------------------------------------------------

def used_calls(
    ledger_path: Path = BUDGET_LEDGER_PATH
) -> int:
    """지금까지 **예약된** 호출 슬롯 수 (= 실제로 시도했을 가능성이 있는 시도)."""
    return client.used_calls(ledger_path)


def check_cumulative_budget(
    planned: int,
    call_log_path: Path = BUDGET_LEDGER_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
) -> int:
    """누적 상한을 넘으면 `BudgetExceeded`. 반환값은 누적 사용량."""
    return client.check_cumulative_budget(planned, ledger_path=call_log_path, cap=cap)


def reserve_call_slot(
    run_id: str,
    image_id: str,
    *,
    policy: "client.MonthlyQuotaPolicy | None" = None,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    lock_path: Path = LOCK_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
    call_id: str | None = None,
) -> int:
    """API 요청 **전에** 호출 슬롯을 예약한다.

    - **잠금**으로 동시 실행을 직렬화한다.
    - 예약 기록은 **flush + fsync** 한다 → 프로세스가 죽어도 슬롯이 남는다.
    - 따라서 **timeout·프로세스 중단처럼 과금 여부를 모르는 시도**도 상한에 포함된다.
    - 월별 내부 상한은 운영자가 확인한 정책(``policy``)이 있어야 검사된다.
      정책이 없으면 ``BudgetUnverified`` — 실제 호출은 거부된다.
    """
    return client.reserve_call_slot(
        run_id,
        image_id,
        policy=policy,
        ledger_path=ledger_path,
        lock_path=lock_path,
        cap=cap,
        call_id=call_id,
    )


def _record_outcome(
    call_id: str,
    outcome: str,
    *,
    reason: str | None = None,
    ledger: Path = BUDGET_LEDGER_PATH,
    lock: Path = LOCK_PATH,
) -> None:
    """호출 결과를 원장에 남긴다 — **기록 실패가 실행을 막지 않는다**.

    예약 줄이 이미 flush+fsync 되어 있으므로 과금 안전은 확보돼 있다.
    결과를 못 남기면 상태 조회에서 ``unknown`` 으로 집계될 뿐이다.
    """
    try:
        client.record_outcome(
            call_id, outcome, reason=reason, ledger_path=ledger, lock_path=lock
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    """이미지 manifest 를 읽는다. ``[{"id": "G01", "uri": "gs://..."}, ...]``"""
    path = Path(path)
    if not path.exists():
        raise ProbeError(
            "이미지 manifest 가 없다: {}\n"
            "Backend presigned URL 이나 GCS 경로를 **실행 직전에** 준비해 "
            "manifest 파일로 저장한다.".format(path)
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeError("manifest 가 JSON 이 아니다: {}".format(exc)) from None
    if isinstance(payload, dict):
        payload = payload.get("images") or []
    if not isinstance(payload, list) or not payload:
        raise ProbeError("manifest 에 이미지가 없다: {}".format(path))
    out: list[dict] = []
    seen = set()
    for entry in payload:
        if isinstance(entry, str):
            entry = {"id": "G{:03d}".format(len(out) + 1), "uri": entry}
        if not isinstance(entry, dict):
            raise ProbeError("manifest 항목 형식이 아니다")
        image_id = entry.get("id")
        uri = entry.get("uri")
        if not image_id or not uri:
            raise ProbeError("manifest 항목에 id/uri 가 없다: {!r}".format(entry))
        if image_id in seen:
            raise ProbeError("manifest 에 중복 id 가 있다: {}".format(image_id))
        seen.add(image_id)
        out.append({"id": image_id, "uri": uri})
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def new_run_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "gv-{}-{}".format(stamp, uuid.uuid4().hex[:6])


def _default_transport(body: dict, *, token: str, timeout: float):
    return client.call_annotate(body, token=token, timeout=timeout)


def run_probe(
    images: list[dict],
    *,
    transport=None,
    local_dir: Path = GOOGLE_DIR,
    run_id: str | None = None,
    max_calls: int | None = None,
    dry_run: bool = True,
    language_hint: str | None = None,
    timeout: float = 60.0,
    token: str | None = None,
    cap: int = CUMULATIVE_CALL_CAP,
    policy: "client.MonthlyQuotaPolicy | None" = None,
) -> dict:
    """Vision OCR 데모를 실행한다.

    ``dry_run=True`` (기본) 이면 **호출도 파일 쓰기도 전혀 하지 않는다.**
    실제 호출(``dry_run=False``)에는 월별 무료 한도 정책이 **필수**다 —
    확인되지 않았으면 ``BudgetUnverified`` 로 거부한다(fail-closed).
    """
    if max_calls is not None and max_calls <= 0:
        raise ProbeError("max_calls 는 1 이상이어야 한다 (받은 값: {})".format(max_calls))
    if dry_run is False and max_calls is None:
        raise ProbeError("실제 호출에는 호출 상한이 필요하다 — max_calls 를 지정한다.")

    run_id = run_id or new_run_id()
    google_dir = Path(local_dir)
    raw_dir = google_dir / "raw"
    results_dir = google_dir / "results"
    call_log = google_dir / "call_log.jsonl"
    ledger = google_dir / "call_budget.jsonl"
    lock = google_dir / "call_budget.lock"

    selected = images if max_calls is None else images[:max_calls]

    summary = {
        "mode": "dry-run" if dry_run else "run",
        "run_id": run_id,
        "images_total": len(images),
        "planned": len(selected),
        "called": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": len(images) - len(selected),
        "feature": client.FEATURE_TYPE,
        "language_hint": language_hint,
        "cumulative_cap": cap,
        "cumulative_used_before": used_calls(ledger),
        "monthly_policy": policy.to_dict() if policy is not None else None,
        "image_ids": [item["id"] for item in selected],
        "results": [],
        "errors": [],
    }

    if dry_run:
        # dry-run 은 **아무것도 쓰지 않고** 호출도 하지 않는다.
        return summary

    # 실제 호출에는 **확인된** 월별 무료 한도 정책이 필수다 (fail-closed).
    policy = policy or client.MonthlyQuotaPolicy.from_environment()
    summary["monthly_policy"] = policy.to_dict()
    budget = client.check_budget(
        len(selected), policy=policy, ledger_path=ledger, cumulative_cap=cap
    )
    summary["month_before"] = budget["month"]
    summary["monthly_used_before"] = budget["monthly_used"]
    summary["cumulative_used_before"] = budget["cumulative_used"]

    transport = transport or _default_transport
    if token is None:
        token = client.resolve_access_token()

    google_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    with call_log.open("a", encoding="utf-8") as log_file:
        for item in selected:
            image_id = item["id"]
            uri = item["uri"]
            # presigned URL 은 **기록하지 않는다** — 참조만 남긴다.
            image_ref = client.uri_ref(uri)
            row = {
                "id": image_id,
                "run_id": run_id,
                "source_image_ref": image_ref,
                "language_hint": language_hint,
                "feature": client.FEATURE_TYPE,
                "raw_response_file": None,
                "raw_response_redacted": False,
                "result_file": None,
                "elapsed_seconds": None,
                "error": None,
            }
            try:
                body = client.build_body(uri, language_hint=language_hint)
            except Exception as exc:
                message = client.redact(
                    "{}: {}".format(type(exc).__name__, exc), token, [uri]
                )
                row["error"] = message
                row["elapsed_seconds"] = 0.0
                summary["failed"] += 1
                summary["errors"].append({"id": image_id, "error": message})
                summary["results"].append(row)
                continue

            # **API 요청 전에** 슬롯을 예약한다 — timeout·중단도 상한에 포함된다.
            call_id = uuid.uuid4().hex
            try:
                reserve_call_slot(
                    run_id,
                    image_id,
                    policy=policy,
                    ledger_path=ledger,
                    lock_path=lock,
                    cap=cap,
                    call_id=call_id,
                )
            except (client.BudgetExceeded, client.BudgetUnverified,
                    client.BudgetLedgerCorrupt) as exc:
                message = client.redact(str(exc), token, [uri])
                row["error"] = message
                summary["failed"] += 1
                summary["errors"].append({"id": image_id, "error": message})
                summary["results"].append(row)
                break

            try:
                payload, elapsed = transport(body, token=token, timeout=timeout)
            except Exception as exc:
                message = client.redact(
                    "{}: {}".format(type(exc).__name__, exc), token, [uri]
                )
                _record_outcome(
                    call_id, "failed", reason=type(exc).__name__, ledger=ledger, lock=lock
                )
                row["error"] = message
                row["elapsed_seconds"] = 0.0
                summary["failed"] += 1
                summary["errors"].append({"id": image_id, "error": message})
            else:
                _record_outcome(call_id, "succeeded", ledger=ledger, lock=lock)
                # 파일명은 run_id **와** image_id 로 유일하게 — 같은 run 의
                # 다른 이미지 응답이 서로 덮어쓰지 않게 한다.
                stem = "{}__{}".format(client.slug(run_id), client.slug(image_id))
                raw_path = raw_dir / "{}.json".format(stem)
                # Vision 응답은 `context.uri` 로 **요청 URI 를 반사**할 수 있다.
                # 저장 전에 URI·서명·토큰을 제거한다.
                safe_payload = client.redact_payload(payload, token, [uri])
                raw_path.write_text(
                    json.dumps(safe_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                row["raw_response_file"] = str(raw_path)
                row["raw_response_redacted"] = True
                row["elapsed_seconds"] = elapsed
                try:
                    structure = vision_adapter.parse_response(
                        safe_payload,
                        source_image=uri,
                        language_hint=language_hint,
                        run_id=run_id,
                    )
                except Exception as exc:
                    message = client.redact(
                        "{}: {}".format(type(exc).__name__, exc), token, [uri]
                    )
                    row["error"] = message
                    summary["failed"] += 1
                    summary["errors"].append({"id": image_id, "error": message})
                else:
                    result_path = results_dir / "{}.json".format(stem)
                    result_path.write_text(
                        json.dumps(structure, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    row["result_file"] = str(result_path)
                    summary["succeeded"] += 1

            summary["called"] += 1
            log_file.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "id": image_id,
                        "source_image_ref": image_ref,
                        "ok": row["error"] is None,
                        "elapsed_seconds": row["elapsed_seconds"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            log_file.flush()
            summary["results"].append(row)

    return summary


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(
        description="Google Cloud Vision OCR 데모 평가기 (기본 dry-run)"
    )
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--local-dir", default=str(GOOGLE_DIR))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--language-hint",
        default=None,
        help="예: ko — **선택 설정**. 힌트 사용/미사용 차이는 평가 때 비교한다.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="이번 실행의 호출 상한. 실제 호출에는 필수. 누적 상한({})과는 별개다.".format(
            CUMULATIVE_CALL_CAP
        ),
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="실제 호출한다. **--max-calls 와 함께 지정해야 한다.** (기본은 dry-run)",
    )
    args = parser.parse_args(argv)

    if args.run and args.max_calls is None:
        parser.error(
            "실제 호출은 `--run` 과 `--max-calls` 를 **둘 다** 지정해야 한다. "
            "(실행 모드: dry-run)"
        )
    if args.max_calls is not None and args.max_calls <= 0:
        parser.error("--max-calls 는 1 이상이어야 한다")

    try:
        images = load_manifest(Path(args.manifest))
        summary = run_probe(
            images,
            local_dir=Path(args.local_dir),
            run_id=args.run_id,
            max_calls=args.max_calls,
            dry_run=not args.run,
            language_hint=args.language_hint,
            timeout=args.timeout,
        )
    except (
        ProbeError,
        client.BudgetExceeded,
        client.BudgetUnverified,
        client.BudgetLedgerCorrupt,
        client.VisionError,
    ) as exc:
        print("데모 실행 전 검증 실패: {}".format(exc))
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
