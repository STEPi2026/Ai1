"""OCR 평가 지표 계산 (결정안 §4.2 A/B/C).

입력
    `eval/_local/labels.json`   — 정답 라벨 (스키마: `eval/labels.schema.json`)
    `eval/_local/results.jsonl` — 측정 1건씩 (샌드박스 호출 결과 + 변환 초안)

출력
    `eval/_local/report.json` + 표준출력 요약

**저장소에 아무것도 남기지 않는다** — 입력·출력은 모두 `eval/_local/` 아래다.
`tests/golden/` 에 실제 provider 결과를 고정하지 않는다 (회귀 테스트와 분리).

주의
    - 분모를 정확히 분리한다. 완전일치율의 분모와 동치 판정 **가능** 건의 분모는
      다르다(동치는 판정 불가한 케이스를 빼면 분자가 줄고 분모도 줄어든다).
    - 단계 경계 F1 은 **원본 줄 범위가 확보된 경우에만** 계산한다. 확보되지 않으면
      "경계 정확도"라고 부를 수 없으므로 `null` + 사유를 남긴다.
    - bbox IoU 는 **라벨된 region 과 대응되는 예측 region** 끼리만 계산한다.
      대응 키는 **원본 줄 인덱스**다 — provider 응답 id 를 키로 쓰지 않는다(§D).
    - 인쇄/필기 정확률은 **원본 줄(-region) 단위**로 계산한다.
      `problem_kind` 에서 **추론하지 않는다.** 대응할 줄이 없으면 점수를 만들지
      않고 `unavailable` + 사유를 남긴다.
    - 비용은 **추정치** 다. **12줄을 초과**하면 페이지 단가를 적용한다.
      줄 수는 **과금 대상 텍스트 행** 기준이다(도형 등은 세지 않는다).
    - "AI¹ 최종 오류 판정" 지표는 **변환 초안** 을 `/analyze` 에 넣어 계산한다.
      변환 규칙(M1/M2/M5/M6) 이 미확정이므로 이 지표는 **잠정** 이다.

**집계 규칙 (PHASE 9-9B.8 · 9-9B.9)**

    | 대상 | 규칙 |
    |---|---|
    | coverage | `samples_attempted / 전체 label`. **시도하지 않은 sample 은 실패로 세지 않는다** (`samples_unattempted` 로 따로 집계) |
    | per-sample 정확도 | **첫 번째 성공 결과** 기준. 성공 결과가 하나도 없는 sample 만 실패/미측정으로 센다 |
    | sample 실패율 | **분모는 실제 시도된 sample.** 시도된 sample 이 없으면 `null`. 전체 label 기준 값은 `end_to_end_failure_rate` 로 따로 낸다(부분 실행에서 과소 표시되므로) |
    | provider 실패 | 요청 자체가 실패한 시도. **분모 = 모든 시도** |
    | adapter 실패 | provider 는 성공했고 변환만 실패한 시도. **분모 = provider 성공한 시도** |
    | M16 안정성 비교 | **성공 호출만** 비교한다(provider·adapter 모두 성공). 성공이 2회 미만이면 `null`(미확인) |
    | 실패 집계 | `failures` = 성공 결과가 없는 **sample** 수 · `failed_attempts` = 실패한 **호출 시도** 수 (별도) |
    | latency · 비용 | **모든 실제 호출 시도** 기준. 반복 호출 비용을 누락하지 않는다 |
    | 반복 측정 | `(case_id, sample_id)` 그룹 기준 — **다른 이미지는 반복으로 비교하지 않는다** |
    | 비용 unknown | **provider 성공 + adapter 실패** 는 벤더가 과금했을 수 있지만 과금 대상 행 수를 알 수 없다 → **0 으로 누락하지 않고 unknown** 으로 집계하고 추정 합계와 함께 표시한다 |
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = EVAL_ROOT / "_local"
LABELS_PATH = LOCAL_DIR / "labels.json"
RESULTS_PATH = LOCAL_DIR / "results.jsonl"
REPORT_PATH = LOCAL_DIR / "report.json"

#: **이 값을 초과**하면(즉 13줄부터) 페이지 단가가 적용된다 — 결정안 §2 "12줄 초과".
PAGE_RATE_ROW_THRESHOLD = 12


# ---------------------------------------------------------------------------
# 라벨 키 규칙
# ---------------------------------------------------------------------------

def is_source_line_index_key(key: object) -> bool:
    """라벨 키가 **원본 줄 인덱스**인지.

    provider 응답 id(32자리 hex 등)는 반복 호출에서 안정성이 확인되지 않았으므로
    정답 라벨 키로 **사용하지 않는다** (결정안 §6 M16).
    """
    if isinstance(key, bool):
        return False
    if isinstance(key, int):
        return key >= 0
    if isinstance(key, str):
        return key.isdigit()
    return False


# ---------------------------------------------------------------------------
# 기하
# ---------------------------------------------------------------------------

def iou(a: list[float] | None, b: list[float] | None) -> float | None:
    """축 정렬 bbox IoU."""
    if not a or not b:
        return None
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    inter_w = max(0.0, min(ax2, bx2) - max(ax, bx))
    inter_h = max(0.0, min(ay2, by2) - max(ay, by))
    inter = inter_w * inter_h
    union = aw * ah + bw * bh - inter
    return (inter / union) if union > 0 else None


# ---------------------------------------------------------------------------
# 수학 동치 (AI¹ 과 같은 판정 기준)
# ---------------------------------------------------------------------------

def latex_equivalent(a: str | None, b: str | None) -> bool | None:
    """두 LaTeX 가 AI¹ 기준에서 동치인지 (None = 판정 불가)."""
    if not a or not b:
        return None
    from app.pipeline import parser, verifier

    node_a = parser.parse_line(a)
    node_b = parser.parse_line(b)
    if node_a is None or node_b is None:
        return None
    try:
        return verifier.equivalent(node_a, node_b, verifier._SolveBudget())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 단계 경계 (원본 줄 범위 기반)
# ---------------------------------------------------------------------------

def segment_f1(
    predicted: list[int], gold: list[int]
) -> float | None:
    """원본 줄 인덱스 집합의 F1 (순서 무시, 집합 매칭)."""
    if predicted is None or gold is None:
        return None
    pred_set, gold_set = set(predicted), set(gold)
    if not pred_set and not gold_set:
        return 1.0
    if not pred_set or not gold_set:
        return 0.0
    matched = len(pred_set & gold_set)
    precision = matched / len(pred_set)
    recall = matched / len(gold_set)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def kendall_tau(a: list[int], b: list[int]) -> float | None:
    """순서 일치도 (Kendall tau)."""
    if len(a) != len(b) or not a:
        return None
    concordant = discordant = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            sign = (a[i] - a[j]) * (b[i] - b[j])
            if sign > 0:
                concordant += 1
            elif sign < 0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------

def _line_signature(draft: dict) -> tuple:
    """원본 줄 단위 예측의 서명 (대응 안정성 비교용)."""
    return tuple(
        (
            region.get("source_line_index"),
            region.get("type"),
            region.get("kind"),
            region.get("text"),
        )
        for region in (draft.get("regions") or [])
    )


class ResultContractError(ValueError):
    """결과 행이 **입력 계약**을 위반했다.

    명시 상태 필드(``provider_succeeded`` · ``adapter_succeeded`` ·
    ``error_stage``)가 ``draft`` / ``error`` 와 모순되면 **조용히 재계산하지
    않고** 이 예외로 중단한다 — 잘못된 집계가 조용히 나오는 게 더 나쁘다.
    """


#: 새 형식 결과 행이 **명시**해야 하는 상태 필드.
EXPLICIT_OUTCOME_FIELDS = ("provider_succeeded", "adapter_succeeded", "error_stage")

#: `error_stage` 가 될 수 있는 값 — JSON null 또는 이 두 문자열뿐.
ERROR_STAGE_VALUES = ("provider", "adapter")


def _validate_state_types(row: dict) -> None:
    """상태 필드의 **JSON 타입**을 검증한다.

    Python truthiness 로 판정하면 `"false"`(비어 있지 않은 문자열) 가 참으로
    처리되어 **조용히 잘못된 집계**가 난다. 그래서 **JSON 타입**으로 검사한다.

    - ``provider_succeeded`` / ``adapter_succeeded`` — **JSON boolean 만** 허용
    - ``error_stage`` — JSON **null** 또는 정확히 ``"provider"`` / ``"adapter"``
    - ``error`` — **string** 또는 **null**
    """
    problems: list[str] = []
    for key in ("provider_succeeded", "adapter_succeeded"):
        value = row.get(key)
        if not isinstance(value, bool):
            problems.append(
                "{} 는 JSON boolean 이어야 한다 (받은 값: {!r})".format(key, value)
            )
    stage = row.get("error_stage")
    if stage is not None and not (
        isinstance(stage, str) and stage in ERROR_STAGE_VALUES
    ):
        problems.append(
            "error_stage 는 JSON null 또는 'provider'/'adapter' 문자열이어야 한다 "
            "(받은 값: {!r})".format(stage)
        )
    error = row.get("error")
    if error is not None and not isinstance(error, str):
        problems.append(
            "error 는 string 또는 null 이어야 한다 (받은 값: {!r})".format(error)
        )
    if problems:
        raise ResultContractError(
            "결과 행의 상태 필드 타입이 JSON 계약과 다르다 (id={!r}): {}".format(
                row.get("id"), "; ".join(problems)
            )
        )


def _validate_explicit_outcome(row: dict) -> None:
    """명시 상태 필드가 ``draft`` / ``error`` 와 모순되는지 확인한다."""
    has_draft = isinstance(row.get("draft"), dict) and bool(row.get("draft"))
    has_error = bool(row.get("error"))
    provider = bool(row["provider_succeeded"])
    adapter = bool(row["adapter_succeeded"])
    stage = row["error_stage"]

    problems: list[str] = []
    if adapter and not has_draft:
        problems.append("adapter_succeeded=True 인데 draft 가 없다")
    if not adapter and has_draft:
        problems.append("adapter_succeeded=False 인데 draft 가 있다")
    if not provider and adapter:
        problems.append("provider_succeeded=False 인데 adapter_succeeded=True 다")
    if provider and adapter and has_error:
        problems.append("두 단계 모두 성공인데 error 가 있다")
    # `error_stage` 의 **타입·허용값** 은 `_validate_state_types` 가 담당한다.
    if stage is None and not (provider and adapter):
        problems.append("error_stage 가 None 인데 실패 상태다")
    if stage == "provider" and provider:
        problems.append("error_stage='provider' 인데 provider_succeeded=True 다")
    if stage == "adapter" and (not provider or adapter):
        problems.append("error_stage='adapter' 인데 상태가 맞지 않는다")
    if problems:
        raise ResultContractError(
            "결과 행의 명시 상태 필드가 draft/error 와 모순된다 (id={!r}): {}".format(
                row.get("id"), "; ".join(problems)
            )
        )


def attempt_outcome(row: dict) -> dict:
    """호출 결과를 **provider / adapter 단계**로 정규화한다.

    - ``provider_succeeded`` — 벤더 호출이 성공해 응답을 받았는가
    - ``adapter_succeeded`` — 그 응답을 `OcrResult` 초안으로 변환했는가
    - ``error_stage`` — ``"provider"`` / ``"adapter"`` / ``None``

    **provider 성공 + adapter 실패** 는 provider 실패가 아니다 — 응답을 받았고
    원본도 저장되었으므로 호출 비용·지연이 발생했다.

    **입력 계약** — ``provider_succeeded`` / ``adapter_succeeded`` /
    ``error_stage`` 중 **하나라도** 있으면 새 형식 행으로 보고 **셋 다** 있어야
    한다. 명시 필드가 ``draft`` / ``error`` 와 **모순되면 조용히 재계산하지 않고**
    `ResultContractError` 로 중단한다.

    **JSON 타입 검증** — Python truthiness 로 판정하면 ``"false"``(비어 있지
    않은 문자열) 가 참으로 처리되어 조용히 잘못된 집계가 난다. 그래서
    ``provider_succeeded`` / ``adapter_succeeded`` 는 **JSON boolean 만**,
    ``error_stage`` 는 **null 또는 정확히 "provider"/"adapter"**,
    ``error`` 는 **string 또는 null** 이어야 한다. 위반하면 같은 예외로 중단한다.

    구버전 행(명시 필드 없음)에는 기존 호환 fallback 을 적용한다.
    """
    error = row.get("error")
    draft = row.get("draft")
    explicit = [key for key in EXPLICIT_OUTCOME_FIELDS if key in row]

    if explicit:
        if len(explicit) != len(EXPLICIT_OUTCOME_FIELDS):
            raise ResultContractError(
                "결과 행의 명시 상태 필드가 일부만 있다 — 셋 다 있어야 한다 "
                "(id={!r}): {}".format(
                    row.get("id"), ", ".join(sorted(explicit))
                )
            )
        _validate_state_types(row)
        _validate_explicit_outcome(row)
        return {
            "provider_succeeded": bool(row["provider_succeeded"]),
            "adapter_succeeded": bool(row["adapter_succeeded"]),
            "error_stage": row["error_stage"],
            "error": error,
        }

    # 구버전 행 — `error` / `draft` 유무로 추정한다(호환 fallback).
    if error is not None and not isinstance(error, str):
        raise ResultContractError(
            "error 는 string 또는 null 이어야 한다 (id={!r}): {!r}".format(
                row.get("id"), error
            )
        )
    provider_ok = (isinstance(draft, dict) and bool(draft)) or not error
    adapter_ok = (isinstance(draft, dict) and bool(draft)) and not error
    if provider_ok and adapter_ok:
        stage = None
    elif provider_ok:
        stage = "adapter"
    else:
        stage = "provider"
    return {
        "provider_succeeded": provider_ok,
        "adapter_succeeded": adapter_ok,
        "error_stage": stage,
        "error": error,
    }


def is_successful_row(row: dict) -> bool:
    """**성공 호출**인가 — provider 와 adapter 가 모두 성공했다.

    M16 안정성 비교와 per-sample 정확도 계산에 쓴다. 실패 호출은
    줄 인덱스 대응을 만들지 못하므로 비교 대상이 될 수 없다.
    """
    outcome = attempt_outcome(row)
    return outcome["provider_succeeded"] and outcome["adapter_succeeded"]


def repeated_measurement_groups(results: list[dict]) -> dict:
    """``(case_id, sample_id)`` 별 반복 측정 그룹 — **성공 호출만**, 2건 이상.

    **같은 이미지의 반복 호출끼리만** 묶는다. ``case_id`` 가 같아도
    ``sample_id`` 가 다르면 (다른 이미지) 반복 측정이 아니다.
    실패 호출은 줄 인덱스 대응이 없으므로 그룹에 넣지 않는다.
    """
    buckets: dict[tuple, list] = {}
    for row in results:
        if not is_successful_row(row):
            continue
        case_id = row.get("id")
        sample_id = row.get("sample_id") or case_id
        buckets.setdefault((case_id, sample_id), []).append(row)
    return {key: value for key, value in buckets.items() if len(value) >= 2}


def line_index_stability_report(results: list[dict]) -> dict:
    """같은 이미지를 **여러 번** 측정했을 때 원본 줄 인덱스 대응이 안정적인지.

    provider 응답 id 는 반복 호출마다 달라질 수 있다. 정답 라벨 키로 쓸 수 있는
    것은 **원본 줄 인덱스** 뿐인데, 그것조차 반복 호출에서 안정적인지는
    **실측으로 확인해야 한다** (결정안 §6 M16).

    반복 측정이 없는 케이스는 **미확인(unknown)** 으로 남긴다 — 안정하다고
    가정하지 않는다.

    **그룹은 ``(case_id, sample_id)`` 기준이다** — 서로 다른 이미지를 반복
    측정으로 비교하지 않는다.
    """
    groups = repeated_measurement_groups(results)
    successful = sum(1 for row in results if is_successful_row(row))
    failed = sum(1 for row in results if not is_successful_row(row))

    stable = unstable = 0
    details: list[dict] = []
    for (case_id, sample_id), rows in groups.items():
        signatures = [_line_signature(row.get("draft") or {}) for row in rows]
        if all(sig == signatures[0] for sig in signatures):
            stable += 1
        else:
            unstable += 1
            details.append(
                {
                    "id": case_id,
                    "sample_id": sample_id,
                    "measurements": len(rows),
                    "line_counts": [len(sig) for sig in signatures],
                }
            )

    if unstable:
        verdict = False
    elif stable:
        verdict = True
    else:
        verdict = None

    return {
        "source_line_index_stable": verdict,
        "cases_with_repeats": stable + unstable,
        "stable_cases": stable,
        "unstable_cases": unstable,
        "successful_measurements": successful,
        "failed_measurements": failed,
        "unstable_details": details,
        "note": (
            "provider 응답 id 는 안정성이 확인되지 않았으므로 정답 라벨 키로 쓰지 않는다. "
            "원본 줄 인덱스도 반복 호출에서 안정적이어야 라벨 키로 쓸 수 있다. "
            "**비교는 성공 호출만** 한다(error 가 없고 유효한 draft 가 있는 것). "
            "성공 호출이 2회 미만이면 None(미확인)이다 — 실패 호출을 반복으로 세지 않는다. "
            "그룹은 (case_id, sample_id) 기준이므로 서로 다른 이미지는 반복 측정으로 "
            "비교하지 않는다."
        ),
    }


def _printed_handwritten_score(label: dict, draft: dict) -> tuple[int, int, int, str | None]:
    """원본 줄( region's kind) 단위 인쇄/필기 채점.

    반환 ``(hit, comparable, undecided, unavailable_reason)``

    - 라벨 ``line_kinds`` 와 예측 ``line_kinds`` 를 **원본 줄 인덱스**로 맞춘다.
    - 대응되는 줄이 하나도 없으면 **점수를 만들지 않는다** (unavailable + 사유).
    - ``problem_kind`` 에서 **추론하지 않는다.**
    """
    gold = label.get("line_kinds") or {}
    predicted = draft.get("line_kinds") or {}

    if not gold:
        return 0, 0, 0, "label_line_kinds_missing"

    gold_numeric = {
        str(key): value for key, value in gold.items() if is_source_line_index_key(key)
    }
    if not gold_numeric:
        # provider id 로 키를 쓴 라벨 — **사용하지 않는다**
        return 0, 0, 0, "label_keys_not_source_line_index"

    hit = comparable = undecided = 0
    matched = 0
    for key, expected in gold_numeric.items():
        if key not in predicted:
            continue
        matched += 1
        actual = predicted.get(key)
        if actual is None:
            undecided += 1
            continue
        comparable += 1
        if actual == expected:
            hit += 1

    if comparable:
        return hit, comparable, undecided, None
    if matched == 0:
        return 0, 0, undecided, "no_overlapping_source_line_index"
    return 0, 0, undecided, "all_matched_lines_undecided"


def measure(labels: list[dict], results: list[dict], pricing: dict | None = None) -> dict:
    # **입력 계약 검증** — 명시 상태 필드가 `draft`/`error` 와 모순된 행은
    # 집계에 들어가기 전에 즉시 중단한다. 조용히 재계산하지 않는다.
    outcomes = [attempt_outcome(row) for row in results]

    # **측정 대상 행 집합** — label id 와 일치하는 행만 집계에 쓴다.
    # attempts_total · failed_attempts · provider/adapter 실패율이 **모두 이 집합**을
    # 분자·분모로 쓴다. (일치하지 않는 행은 아래에서 별도 집계)
    label_ids = {case["id"] for case in labels}
    measured_rows: list[dict] = []
    unmatched_rows: list[dict] = []
    unmatched_outcomes: list[dict] = []
    for row, outcome in zip(results, outcomes):
        if row.get("id") in label_ids:
            measured_rows.append(row)
        else:
            unmatched_rows.append(row)
            unmatched_outcomes.append(outcome)

    by_case: dict[str, list[dict]] = {}
    outcomes_by_case: dict[str, list[dict]] = {}
    for row, outcome in zip(results, outcomes):
        if row.get("id") in label_ids:
            by_case.setdefault(row.get("id"), []).append(row)
            outcomes_by_case.setdefault(row.get("id"), []).append(outcome)
    pricing = pricing or {}

    # A. OCR 정확률
    exact_hits = exact_comparable = 0
    equivalence_hits = equivalence_decidable = 0
    seg_scores: list[float] = []
    seg_unavailable = 0
    order_exact = order_comparable = 0
    order_tau: list[float] = []
    ious: list[float] = []
    kind_hits = kind_comparable = kind_undecided = 0
    kind_unavailable: dict[str, int] = {}
    label_key_not_source_line = 0
    region_links_unresolved = 0
    unreadable_tp = unreadable_fp = unreadable_fn = 0
    confidences: list[tuple[float, bool]] = []

    # B. AI¹ 임팩트
    final_match = final_comparable = 0
    misconception_polluted = 0
    review_required = 0

    # C. 운영
    latencies: list[float] = []
    failures = 0
    failed_attempts = 0
    provider_failed_attempts = 0
    adapter_failed_attempts = 0
    provider_succeeded_attempts = 0
    cost_total = 0.0
    cost_unknown = 0
    cost_unknown_provider_failed = 0
    cost_unknown_adapter_failed = 0
    cost_row_count_approximated = 0
    page_rate_cases = 0
    samples_attempted = 0
    unmatched_cost = 0.0
    unmatched_cost_unknown = 0

    for label in labels:
        rows = by_case.get(label["id"])
        if not rows:
            continue
        attempts = rows
        samples_attempted += 1
        outcomes_for_case = outcomes_by_case.get(label["id"], [])
        successes = [
            item for item, outcome in zip(attempts, outcomes_for_case)
            if outcome["provider_succeeded"] and outcome["adapter_succeeded"]
        ]
        failed_attempts += len(attempts) - len(successes)
        provider_succeeded_attempts += sum(
            1 for outcome in outcomes_for_case if outcome["provider_succeeded"]
        )
        provider_failed_attempts += sum(
            1 for outcome in outcomes_for_case if not outcome["provider_succeeded"]
        )
        adapter_failed_attempts += sum(
            1 for outcome in outcomes_for_case
            if outcome["provider_succeeded"] and not outcome["adapter_succeeded"]
        )

        # --- C. 운영 지표: **모든 실제 호출 시도** 기준 ---
        # 반복 호출 비용을 누락하지 않는다. 실패 시도도 시도로 센다.
        for attempt, outcome in zip(attempts, outcomes_for_case):
            elapsed = attempt.get("elapsed_seconds")
            if elapsed is not None:
                latencies.append(float(elapsed))
            attempt_draft = attempt.get("draft")
            if attempt_draft:
                attempt_rows, approximated = _billable_row_count(attempt_draft)
                if approximated:
                    cost_row_count_approximated += 1
                unit = _unit_cost(pricing, attempt_rows)
            else:
                # **provider 성공인데 draft 가 없다 = adapter 실패** →
                # 벤더는 과금했을 가능성이 크지만 과금 대상 행 수를 알 수 없다.
                # **0 으로 누락하지 않고** unknown 으로 집계한다.
                attempt_rows = None
                unit = None
                if outcome["provider_succeeded"]:
                    cost_unknown_adapter_failed += 1
                else:
                    cost_unknown_provider_failed += 1
            if unit is None:
                cost_unknown += 1
            else:
                cost_total += unit
                if attempt_rows > PAGE_RATE_ROW_THRESHOLD:
                    page_rate_cases += 1

        if not successes:
            # 성공 결과가 하나도 없는 sample → **실패/미측정**
            failures += 1
            continue

        # --- per-sample 정확도: **첫 번째 성공 결과** 기준 ---
        row = successes[0]

        draft = row.get("draft") or {}
        problem = draft.get("problem") or {}
        steps = draft.get("steps") or []
        regions = draft.get("regions") or []

        # --- A-1 문제 LaTeX: 완전일치 vs 동치(분모 분리) ---
        gold_latex = label.get("problem_latex_text")
        got_latex = problem.get("latex_text")
        if gold_latex and got_latex:
            exact_comparable += 1
            if gold_latex.strip() == got_latex.strip():
                exact_hits += 1
            verdict = latex_equivalent(gold_latex, got_latex)
            if verdict is not None:
                equivalence_decidable += 1
                if verdict:
                    equivalence_hits += 1

        # --- A-2 단계 경계: 원본 줄 범위가 있을 때만 ---
        gold_source_lines = label.get("step_source_lines")
        if gold_source_lines is None:
            seg_unavailable += 1
        else:
            predicted_source_lines = [
                step["source_line_index"]
                for step in steps
                if step.get("source_line_index") is not None
            ]
            if len(predicted_source_lines) != len(steps):
                seg_unavailable += 1  # 줄→단계 대응이 1:1 이 아니면 경계로 부를 수 없다
            else:
                score = segment_f1(predicted_source_lines, list(gold_source_lines))
                if score is not None:
                    seg_scores.append(score)

        # --- A-3 순서 ---
        gold_order = label.get("step_order") or []
        got_order = [step["step_no"] for step in steps]
        if gold_order and len(gold_order) == len(got_order):
            order_comparable += 1
            if gold_order == got_order:
                order_exact += 1
            tau = kendall_tau(gold_order, got_order)
            if tau is not None:
                order_tau.append(tau)

        # --- A-4 bbox IoU: **원본 줄 인덱스**로 대응되는 region 끼리만 ---
        gold_region_bbox = label.get("problem_region_bbox")
        if gold_region_bbox and draft.get("problem_bbox"):
            value = iou(gold_region_bbox, draft["problem_bbox"])
            if value is not None:
                ious.append(value)
        gold_by_index = label.get("region_bboxes") or {}
        if gold_by_index:
            if not any(is_source_line_index_key(key) for key in gold_by_index):
                label_key_not_source_line += 1
            for region in regions:
                index = region.get("source_line_index")
                if index is None or region.get("bbox") is None:
                    continue
                gold_box = gold_by_index.get(str(index))
                if gold_box is None:
                    continue
                value = iou(gold_box, region["bbox"])
                if value is not None:
                    ious.append(value)

        # --- A-5 인쇄/필기: **원본 줄( region) 기준**. problem_kind 에서 추론하지 않는다 ---
        hit, comparable, undecided, reason = _printed_handwritten_score(label, draft)
        kind_hits += hit
        kind_comparable += comparable
        kind_undecided += undecided
        if reason is not None:
            kind_unavailable[reason] = kind_unavailable.get(reason, 0) + 1

        # --- M14: 미해결 step→region 연결 건수 (OcrResult 에 region 식별자가 없어 null) ---
        region_links_unresolved += sum(
            1 for link in (draft.get("step_region_links") or []) if not link.get("linked")
        )

        # --- A-6 unreadable 판정 ---
        gold_unreadable = set(label.get("unreadable_steps") or [])
        got_unreadable = {step["step_no"] for step in steps if step.get("unreadable")}
        unreadable_tp += len(gold_unreadable & got_unreadable)
        unreadable_fp += len(got_unreadable - gold_unreadable)
        unreadable_fn += len(gold_unreadable - got_unreadable)

        # --- A-7 confidence calibration ---
        gold_step_latex = label.get("step_latex_texts") or []
        for step in steps:
            confidence = step.get("confidence")
            if confidence is None:
                continue
            index = step["step_no"] - 1
            if index < len(gold_step_latex):
                verdict = latex_equivalent(gold_step_latex[index], step.get("latex_text"))
                if verdict is not None:
                    confidences.append((float(confidence), bool(verdict)))

        # --- B. AI¹ 임팩트 ---
        analysis = row.get("analysis")
        if analysis is not None:
            expected = label.get("expected_error") or {}
            if "error_step" in expected:
                final_comparable += 1
                if (
                    analysis.get("error_step") == expected.get("error_step")
                    and analysis.get("error_type") == expected.get("error_type")
                ):
                    final_match += 1
            gold_mis = set(expected.get("misconception_ids") or [])
            got_mis = set(analysis.get("misconception_ids") or [])
            if got_mis - gold_mis:
                misconception_polluted += 1
            if analysis.get("analysis_status") == "REVIEW_REQUIRED":
                review_required += 1

    # label 과 일치하지 않는 행 — **집계에서 제외**하되 건수와 비용을 따로 보고한다.
    # (실제 호출이었을 수 있으므로 돈을 조용히 버리지 않는다)
    for row, outcome in zip(unmatched_rows, unmatched_outcomes):
        attempt_rows, approximated = _billable_row_count(row.get("draft") or {})
        unit = _unit_cost(pricing, attempt_rows)
        if unit is None:
            unmatched_cost_unknown += 1
        else:
            unmatched_cost += unit

    def ratio(numerator, denominator):
        return (numerator / denominator) if denominator else None

    return {
        "counts": {
            "labels": len(labels),
            "measured": len(by_case),
            "measurements_total": len(results),
            "attempts_total": len(measured_rows),
            "samples_attempted": samples_attempted,
            "samples_unattempted": max(0, len(labels) - samples_attempted),
            "coverage": ratio(samples_attempted, len(labels)),
            "failed_attempts": failed_attempts,
            "provider_succeeded_attempts": provider_succeeded_attempts,
            "provider_failed_attempts": provider_failed_attempts,
            "adapter_failed_attempts": adapter_failed_attempts,
            "failures": failures,
            "unmatched_result_rows": len(unmatched_rows),
            "counting_note": (
                "failures 는 **성공 결과가 하나도 없는 sample** 수(sample 단위)다. "
                "failed_attempts 는 **실패한 호출 시도** 수(attempt 단위)다. "
                "**provider 실패**(요청 실패)와 **adapter 실패**(응답은 받았으나 변환 실패)는 "
                "분리 집계한다. latency·비용은 **모든 실제 호출 시도** 기준이라 반복 호출 "
                "비용이 빠지지 않는다."
            ),
            "row_set_note": (
                "attempts_total · failed_attempts · provider/adapter 실패율의 **분자·분모는 "
                "모두 같은 행 집합**을 쓴다 — label id 와 일치하는 결과 행만. "
                "일치하지 않는 행은 unmatched_result_rows 로 따로 세고 집계에서 제외한다."
            ),
            "coverage_note": (
                "coverage = 시도된 sample / 전체 label. **시도하지 않은 sample 은 "
                "실패로 세지 않고** samples_unattempted 로 따로 집계한다."
            ),
        },
        "A_ocr_accuracy": {
            "problem_latex_exact_match": ratio(exact_hits, exact_comparable),
            "problem_latex_exact_comparable": exact_comparable,
            "problem_latex_equivalence": ratio(equivalence_hits, equivalence_decidable),
            "problem_latex_equivalence_decidable": equivalence_decidable,
            "segment_f1_mean": statistics.fmean(seg_scores) if seg_scores else None,
            "segment_f1_unavailable_cases": seg_unavailable,
            "step_order_exact_match": ratio(order_exact, order_comparable),
            "step_order_comparable": order_comparable,
            "step_order_kendall_tau_mean": statistics.fmean(order_tau) if order_tau else None,
            "bbox_iou_median": statistics.median(ious) if ious else None,
            "bbox_iou_matched_regions": len(ious),
            "printed_handwritten_accuracy": ratio(kind_hits, kind_comparable),
            "printed_handwritten_comparable_lines": kind_comparable,
            "printed_handwritten_undecided_lines": kind_undecided,
            "printed_handwritten_unavailable_cases": sum(kind_unavailable.values()),
            "printed_handwritten_unavailable_reasons": kind_unavailable,
            "printed_handwritten_note": (
                "원본 줄( region's kind) 단위 채점이다. problem_kind 에서 추론하지 않는다. "
                "대응 가능한 줄이 없으면 점수를 만들지 않고 사유를 남긴다."
            ),
            "per_sample_note": (
                "per-sample 정확도는 **첫 번째 성공 결과** 기준이다. "
                "성공 결과가 하나도 없는 sample 은 실패/미측정으로 세고 점수를 만들지 않는다."
            ),
            "label_region_bbox_keys_not_source_line_index": label_key_not_source_line,
            "region_links_unresolved": region_links_unresolved,
            "region_links_note": (
                "OcrResult 에 region 식별자가 없어 step→region 연결은 미해결로 남는다(M14). "
                "provider line id 는 eval 초안(step_region_links)에 보존된다."
            ),
            "unreadable_precision": ratio(unreadable_tp, unreadable_tp + unreadable_fp),
            "unreadable_recall": ratio(unreadable_tp, unreadable_tp + unreadable_fn),
            "confidence_ece": _expected_calibration_error(confidences),
            "confidence_calibration_samples": len(confidences),
        },
        "B_ai1_impact": {
            "final_error_judgement_match": ratio(final_match, final_comparable),
            "final_error_judgement_comparable": final_comparable,
            "misconception_pollution_rate": ratio(misconception_polluted, final_comparable),
            "review_required_rate": ratio(review_required, final_comparable),
        },
        "C_operations": {
            "latency_p50": statistics.median(latencies) if latencies else None,
            "latency_p95": _percentile(latencies, 0.95) if latencies else None,
            "latency_attempts": len(latencies),
            "cost_estimate_usd_total": cost_total if cost_total else None,
            "cost_estimate_note": (
                "과금 대상 텍스트 행이 12줄을 **초과**하면(13줄부터) v3/pdf 페이지 단가"
                "($0.005)를 적용한 추정치다. 실제 청구액이 아니다. "
                "**모든 실제 호출 시도** 기준이라 반복 호출 비용이 빠지지 않는다. "
                "**이 합계는 하한값이다** — 아래 unknown 시도만큼 더 발생할 수 있다."
            ),
            "cost_estimate_usd_total_is_lower_bound": cost_unknown > 0,
            "cases_billed_at_page_rate": page_rate_cases,
            "cases_cost_unknown": cost_unknown,
            "cases_cost_unknown_provider_failed": cost_unknown_provider_failed,
            "cases_cost_unknown_adapter_failed": cost_unknown_adapter_failed,
            "cases_cost_unknown_note": (
                "provider 실패는 응답이 없어 과금 여부를 알 수 없고, "
                "**provider 성공 + adapter 실패는 벤더가 과금했을 수 있지만** 과금 대상 행 수를 "
                "원본 응답에서 추정하지 못했다. **어느 쪽도 0 으로 누락하지 않는다.**"
            ),
            "cases_cost_row_count_approximated": cost_row_count_approximated,
            "sample_failure_rate": ratio(failures, samples_attempted),
            "sample_failure_rate_note": (
                "**분모는 실제 시도된 sample** 이다. 시도된 sample 이 없으면 null. "
                "시도하지 않은 sample 은 포함하지 않는다."
            ),
            "end_to_end_failure_rate": ratio(failures, len(labels)),
            "end_to_end_failure_rate_note": (
                "분모가 **전체 label** 이다 — 시도하지 않은 sample 도 분모에 들어간다. "
                "부분 실행에서는 실패율이 과소 표시되므로 **sample_failure_rate 를 함께 본다.**"
            ),
            "provider_failure_rate": ratio(provider_failed_attempts, len(measured_rows)),
            "provider_failure_rate_note": (
                "분모 = **측정 대상 행 집합**(label id 일치)의 모든 시도. 요청 자체가 실패한 비율."
            ),
            "adapter_failure_rate": ratio(
                adapter_failed_attempts, provider_succeeded_attempts
            ),
            "adapter_failure_rate_note": (
                "분모 = **provider 성공한 시도**(같은 행 집합 기준). "
                "응답은 받았으나 변환에 실패한 비율."
            ),
            "attempt_failure_rate": ratio(failed_attempts, len(measured_rows)),
            "unmatched_cost_estimate_usd": unmatched_cost if unmatched_cost else None,
            "unmatched_cost_unknown": unmatched_cost_unknown,
            "unmatched_note": (
                "label id 와 일치하지 않는 결과 행은 **집계에서 제외**했다. "
                "그 행이 실제 호출이었다면 비용이 발생할 수 있으므로 추정치와 unknown 수를 "
                "따로 보고한다. 라벨 파일을 확인해야 한다."
            ),
        },
        "D_label_key_stability": line_index_stability_report(measured_rows),
    }


def _billable_row_count(draft: dict) -> tuple[int | None, bool]:
    """과금 대상 **텍스트 행** 수와, 근사값을 썼는지 여부.

    도형(diagram) 등은 세지 않는다. `billable_text_row_count` 가 없으면
    전체 줄 수로 **근사**하고 그 사실을 알린다.
    """
    exact = draft.get("billable_text_row_count")
    if exact is not None:
        return int(exact), False
    total = draft.get("source_line_count")
    if total is None:
        return None, False
    return int(total), True


def _unit_cost(pricing: dict, row_count: int | None) -> float | None:
    """1장당 추정 단가. **12줄 초과**면 페이지 단가."""
    per_image = pricing.get("per_image_usd")
    per_page = pricing.get("per_page_usd")
    if per_image is None or row_count is None:
        return None
    if row_count > PAGE_RATE_ROW_THRESHOLD and per_page is not None:
        return float(per_page)
    return float(per_image)


def _expected_calibration_error(pairs: list[tuple[float, bool]], bins: int = 10) -> float | None:
    if not pairs:
        return None
    total = len(pairs)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        bucket = [
            p
            for p in pairs
            if (low <= p[0] < high)
            or (index == bins - 1 and p[0] == 1.0)
        ]
        if not bucket:
            continue
        confidence = statistics.fmean(p[0] for p in bucket)
        accuracy = sum(1 for p in bucket if p[1]) / len(bucket)
        error += (len(bucket) / total) * abs(confidence - accuracy)
    return error


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main() -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="OCR 평가 지표 계산")
    parser.add_argument("--labels", default=str(LABELS_PATH))
    parser.add_argument("--results", default=str(RESULTS_PATH))
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args()

    payload = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        labels = payload.get("cases", [])
        pricing = payload.get("pricing") or {}
    else:
        labels = payload
        pricing = {}
    results = []
    result_path = Path(args.results)
    if result_path.exists():
        for line in result_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                results.append(json.loads(line))

    report = measure(labels, results, pricing)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print("리포트 저장: {}".format(args.out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
