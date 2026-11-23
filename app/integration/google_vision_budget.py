"""Local cumulative + monthly call guard shared by the Vision app provider and eval probe.

**이 모듈은 과금을 대신 계산하지 않는다.** Google Cloud Vision 의 무료 제공량과
과금 기준은 Google 콘솔·문서에서만 확인할 수 있으므로, 이 코드는 그 값을
**고정 상수로 추정하지 않는다**. 운영자가 확인한 값만 환경변수로 주입하고,
확인되지 않았으면 호출을 거부한다(fail-closed).

두 개의 상한을 **동시에** 적용한다.

- ``CUMULATIVE_CALL_CAP`` (40) — 프로젝트 전체 누적 상한. 기존 동작 유지.
- 월별 내부 상한 — 운영자가 확인한 월 무료 제공량보다 **낮게** 설정한 값.
  매월 1일(UTC) 기준으로 구분하며 이전 달 기록은 지우지 않는다.

예약은 Google 요청 **전에** 원장에 기록하고 ``flush`` + ``fsync`` 한다.
따라서 성공·실패·timeout·프로세스 중단을 포함한 **모든 시도**가 차감된다.
자동 재시도는 하지 않는다(재시도 정책 소유는 Backend).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOOGLE_DIR = PROJECT_ROOT / "eval" / "_local" / "google_vision"
BUDGET_LEDGER_PATH = GOOGLE_DIR / "call_budget.jsonl"
LOCK_PATH = GOOGLE_DIR / "call_budget.lock"
CUMULATIVE_CALL_CAP = 40
LOCK_TIMEOUT = 60.0
LOCK_STALE_AFTER = 300.0

#: Vision 요청 1건이 소비하는 OCR 기능(과금 단위).
#: AI① 은 ``DOCUMENT_TEXT_DETECTION`` 만 호출한다. 같은 이미지에 ``TEXT_DETECTION``
#: 을 함께 보내면 무료 unit 을 두 번 소비하므로 금지한다.
BILLING_UNIT_DOCUMENT_TEXT_DETECTION = "document_text_detection"
BILLING_UNIT_TEXT_DETECTION = "text_detection"
SUPPORTED_BILLING_UNITS = (
    BILLING_UNIT_DOCUMENT_TEXT_DETECTION,
    BILLING_UNIT_TEXT_DETECTION,
)

#: 운영자가 **Google 콘솔/문서에서 확인한** 값을 주입하는 환경변수.
#: 코드는 기본값을 갖지 않는다 — 미확인 상태가 기본이며, 그 상태는 호출 차단이다.
ENV_BILLING_UNIT = "AI1_OCR_BILLING_UNIT"
ENV_MONTHLY_FREE_QUOTA = "AI1_OCR_MONTHLY_FREE_QUOTA"
ENV_MONTHLY_CALL_CAP = "AI1_OCR_MONTHLY_CALL_CAP"
ENV_FREE_QUOTA_CONFIRMED = "AI1_OCR_FREE_QUOTA_CONFIRMED"

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MONTH_FORMAT = "%Y-%m"

BLOCK_LEDGER_UNVERIFIED = "ledger_unverified"
BLOCK_QUOTA_UNVERIFIED = "quota_unverified"
BLOCK_MONTHLY_CAP = "monthly_cap_reached"
BLOCK_CUMULATIVE_CAP = "cumulative_cap_reached"

_TRUTHY = {"1", "true", "yes", "on"}
_POSITIVE_INT = re.compile(r"^[1-9][0-9]*$")

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
_OUTCOMES = {OUTCOME_SUCCEEDED, OUTCOME_FAILED}


class BudgetError(RuntimeError):
    """호출 예산 판정과 관련된 모든 오류의 상위 타입."""


class BudgetExceeded(BudgetError):
    """누적 또는 월별 상한을 넘었다 — Google 요청을 보내지 않는다."""


class BudgetUnverified(BudgetError):
    """무료 한도가 확인되지 않았거나 설정이 안전하지 않다 — fail-closed."""


class BudgetLedgerCorrupt(BudgetError):
    """사용량 원장을 읽을 수 없거나 형식이 깨졌다 — fail-closed."""


class BudgetLockError(BudgetError):
    """The call-budget ledger could not be locked safely."""


@dataclass(frozen=True)
class MonthlyQuotaPolicy:
    """운영자가 확인한 월 무료 제공량과, 그보다 낮은 내부 상한.

    ``free_quota_units`` 는 Google 이 공지한 값(운영자 확인)이고,
    ``internal_call_cap`` 은 AI① 이 스스로 지키는 더 낮은 상한이다.
    두 값 모두 이 코드에 하드코딩하지 않는다.
    """

    free_quota_units: int
    internal_call_cap: int
    billing_unit: str

    def __post_init__(self):
        if not isinstance(self.free_quota_units, int) or self.free_quota_units <= 0:
            raise BudgetUnverified(
                f"{ENV_MONTHLY_FREE_QUOTA} 는 1 이상의 정수여야 한다."
            )
        if not isinstance(self.internal_call_cap, int) or self.internal_call_cap <= 0:
            raise BudgetUnverified(
                f"{ENV_MONTHLY_CALL_CAP} 는 1 이상의 정수여야 한다."
            )
        if self.internal_call_cap >= self.free_quota_units:
            # "무료 한도보다 낮은 내부 안전 한도" 를 설정 단계에서 강제한다.
            raise BudgetUnverified(
                f"{ENV_MONTHLY_CALL_CAP} 는 {ENV_MONTHLY_FREE_QUOTA} 보다 "
                "작아야 한다 (내부 안전 한도는 무료 한도보다 낮아야 한다)."
            )
        unit = (self.billing_unit or "").strip().lower()
        if unit not in SUPPORTED_BILLING_UNITS:
            raise BudgetUnverified(
                f"{ENV_BILLING_UNIT} 는 지원하는 OCR 기능 이름이어야 한다: "
                + ", ".join(SUPPORTED_BILLING_UNITS)
            )
        object.__setattr__(self, "billing_unit", unit)

    @classmethod
    def from_environment(
        cls,
        env=None,
        *,
        required_unit: str | None = BILLING_UNIT_DOCUMENT_TEXT_DETECTION,
    ) -> "MonthlyQuotaPolicy":
        """환경변수에서 정책을 만든다. 확인되지 않았으면 ``BudgetUnverified``."""
        env = os.environ if env is None else env

        if not _is_truthy(env.get(ENV_FREE_QUOTA_CONFIRMED)):
            raise BudgetUnverified(
                f"{ENV_FREE_QUOTA_CONFIRMED} 미확인 — Google 콘솔/문서에서 월 무료 "
                "제공량을 확인했다는 운영자 확인이 없으면 OCR 호출을 허용하지 않는다."
            )

        unit = (env.get(ENV_BILLING_UNIT) or "").strip().lower()
        if not unit:
            raise BudgetUnverified(
                f"{ENV_BILLING_UNIT} 미설정 — 과금 단위(document_text_detection / "
                "text_detection)를 구분해야 한다."
            )
        if unit not in SUPPORTED_BILLING_UNITS:
            raise BudgetUnverified(
                f"{ENV_BILLING_UNIT} 값이 지원 목록에 없다: "
                + ", ".join(SUPPORTED_BILLING_UNITS)
            )
        if required_unit is not None and unit != required_unit:
            raise BudgetUnverified(
                f"{ENV_BILLING_UNIT} 가 이 provider 의 호출 단위({required_unit})와 "
                "다르다 — 무료 한도와 실제 호출 단위가 일치하지 않으면 차단한다."
            )

        free_quota = _positive_int(env.get(ENV_MONTHLY_FREE_QUOTA), ENV_MONTHLY_FREE_QUOTA)
        call_cap = _positive_int(env.get(ENV_MONTHLY_CALL_CAP), ENV_MONTHLY_CALL_CAP)
        return cls(
            free_quota_units=free_quota,
            internal_call_cap=call_cap,
            billing_unit=unit,
        )

    def to_dict(self) -> dict:
        return {
            "billing_unit": self.billing_unit,
            "monthly_free_quota_units": self.free_quota_units,
            "monthly_internal_cap": self.internal_call_cap,
        }


@dataclass(frozen=True)
class BudgetStatus:
    """읽기 전용 사용량/차단 상태. 비밀값은 담지 않는다."""

    month: str
    billing_unit: str | None
    monthly_free_quota_units: int | None
    monthly_internal_cap: int | None
    monthly_reserved: int
    monthly_succeeded: int
    monthly_failed: int
    monthly_unknown: int
    monthly_internal_remaining: int | None
    cumulative_used: int
    cumulative_cap: int
    cumulative_remaining: int
    ledger_verified: bool
    blocked: bool
    blocked_reason: str | None
    ledger_path: str
    generated_at: str = field(default="")

    def to_dict(self) -> dict:
        return {
            "month": self.month,
            "billing_unit": self.billing_unit,
            "monthly_free_quota_units": self.monthly_free_quota_units,
            "monthly_internal_cap": self.monthly_internal_cap,
            # 이 값은 **AI① 내부 상한의 잔여량**이다. Google Billing 의 실제
            # 잔여 무료 unit 이 아니다(코드에서 확인할 수 없으므로 추정하지 않는다).
            "monthly_internal_remaining": self.monthly_internal_remaining,
            "monthly_reserved": self.monthly_reserved,
            "monthly_succeeded": self.monthly_succeeded,
            "monthly_failed": self.monthly_failed,
            "monthly_unknown": self.monthly_unknown,
            "cumulative_used": self.cumulative_used,
            "cumulative_cap": self.cumulative_cap,
            "cumulative_remaining": self.cumulative_remaining,
            "ledger_verified": self.ledger_verified,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "ledger_path": self.ledger_path,
            "generated_at": self.generated_at,
        }


class FileLock:
    """Exclusive lock file to serialize budget reservations across processes."""

    def __init__(self, path: Path = LOCK_PATH, timeout: float = LOCK_TIMEOUT):
        self.path = Path(path)
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(
                    str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                os.fsync(self._fd)
                return self
            except (FileExistsError, PermissionError) as exc:
                try:
                    lock_mtime = self.path.stat().st_mtime
                except FileNotFoundError:
                    # Another process released the lock between the failed
                    # exclusive create and stat; retry the create immediately.
                    continue
                except OSError as stat_exc:
                    raise BudgetLockError(
                        "Google Vision 호출 예산 잠금 파일을 확인하지 못했다."
                    ) from stat_exc
                try:
                    if time.time() - lock_mtime > LOCK_STALE_AFTER:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise BudgetLockError(
                        "Google Vision 호출 예산 잠금을 얻지 못했다; 동시 실행 여부를 확인한다."
                    ) from exc
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# 시간 · 검증 유틸
# ---------------------------------------------------------------------------

def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _TRUTHY


def _positive_int(raw: str | None, name: str) -> int:
    text = (raw or "").strip()
    if not _POSITIVE_INT.match(text):
        raise BudgetUnverified(f"{name} 는 1 이상의 정수여야 한다.")
    return int(text)


def _to_utc(now: float | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


def utc_month(now: float | datetime | None = None) -> str:
    """UTC 기준 월 키. 매월 1일 00:00 UTC 에 새 구간이 시작된다."""
    return _to_utc(now).strftime(MONTH_FORMAT)


def utc_timestamp(now: float | datetime | None = None) -> str:
    return _to_utc(now).strftime(TIMESTAMP_FORMAT)


def _month_of(entry: dict) -> str:
    return entry["reserved_at"][:7]


# ---------------------------------------------------------------------------
# 원장 읽기
# ---------------------------------------------------------------------------

def _read_lines(ledger_path: Path) -> list[str]:
    path = Path(ledger_path)
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BudgetLedgerCorrupt(
            "사용량 원장을 읽을 수 없다 ({}) — 호출을 차단한다.".format(
                type(exc).__name__
            )
        ) from None


def _parse_entries(ledger_path: Path = BUDGET_LEDGER_PATH) -> list[dict]:
    """원장을 엄격하게 파싱한다. 한 줄이라도 해석 불가면 ``BudgetLedgerCorrupt``."""
    entries: list[dict] = []
    for lineno, line in enumerate(_read_lines(ledger_path), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            raise BudgetLedgerCorrupt(
                "사용량 원장 {}번째 줄이 JSON 이 아니다 — 호출을 차단한다.".format(lineno)
            ) from None
        if not isinstance(entry, dict):
            raise BudgetLedgerCorrupt(
                "사용량 원장 {}번째 줄이 객체가 아니다 — 호출을 차단한다.".format(lineno)
            )
        kind = entry.get("kind", "reservation")
        if kind == "reservation":
            reserved_at = entry.get("reserved_at")
            if not isinstance(reserved_at, str) or not _valid_timestamp(reserved_at):
                raise BudgetLedgerCorrupt(
                    "사용량 원장 {}번째 줄에 유효한 reserved_at 이 없다 — "
                    "월별 사용량을 확정할 수 없어 호출을 차단한다.".format(lineno)
                )
            entries.append(entry)
        elif kind == "outcome":
            if not entry.get("call_id") or entry.get("status") not in _OUTCOMES:
                raise BudgetLedgerCorrupt(
                    "사용량 원장 {}번째 줄 outcome 형식이 잘못됐다 — "
                    "호출을 차단한다.".format(lineno)
                )
            entries.append(entry)
        else:
            raise BudgetLedgerCorrupt(
                "사용량 원장 {}번째 줄의 kind={!r} 를 알 수 없다 — "
                "호출을 차단한다.".format(lineno, kind)
            )
    return entries


def _valid_timestamp(text: str) -> bool:
    try:
        time.strptime(text, TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        return False
    return True


def _reservations(entries: list[dict]) -> list[dict]:
    return [entry for entry in entries if entry.get("kind", "reservation") == "reservation"]


def _outcomes_by_call(entries: list[dict]) -> dict[str, dict]:
    outcomes: dict[str, dict] = {}
    for entry in entries:
        if entry.get("kind") == "outcome":
            outcomes[entry["call_id"]] = entry
    return outcomes


def used_calls(ledger_path: Path = BUDGET_LEDGER_PATH) -> int:
    """지금까지 **예약된** 호출 슬롯 수.

    해석할 수 없는 줄은 보수적으로 1회로 센다(과소 집계 방지). 실제 호출 판정은
    :func:`reserve_call_slot` 이 엄격 파싱으로 다시 확인한다.
    """
    count = 0
    for line in _read_lines(ledger_path):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            count += 1
            continue
        if isinstance(entry, dict) and entry.get("kind") == "outcome":
            continue
        count += 1
    return count


def month_usage(month: str, ledger_path: Path = BUDGET_LEDGER_PATH) -> dict:
    """해당 월의 예약/성공/실패/미확인 건수 (성공·실패는 call_id 로 결합)."""
    entries = _parse_entries(ledger_path)
    outcomes = _outcomes_by_call(entries)
    reserved = [entry for entry in _reservations(entries) if _month_of(entry) == month]
    succeeded = failed = 0
    for entry in reserved:
        call_id = entry.get("call_id")
        outcome = outcomes.get(call_id) if call_id else None
        if outcome and outcome["status"] == OUTCOME_SUCCEEDED:
            succeeded += 1
        elif outcome and outcome["status"] == OUTCOME_FAILED:
            failed += 1
    return {
        "month": month,
        "reserved": len(reserved),
        "succeeded": succeeded,
        "failed": failed,
        "unknown": len(reserved) - succeeded - failed,
    }


def status(
    *,
    policy: MonthlyQuotaPolicy | None = None,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    cumulative_cap: int = CUMULATIVE_CALL_CAP,
    now: float | datetime | None = None,
) -> BudgetStatus:
    """사용량 조회 — 차단 여부와 사유를 포함한다. 읽기 전용."""
    month = utc_month(now)
    resolved_policy = policy
    if resolved_policy is None:
        try:
            resolved_policy = MonthlyQuotaPolicy.from_environment()
        except BudgetUnverified:
            resolved_policy = None

    ledger_verified = True
    usage = {"reserved": 0, "succeeded": 0, "failed": 0, "unknown": 0}
    cumulative_used = 0
    try:
        usage = month_usage(month, ledger_path)
        cumulative_used = len(_reservations(_parse_entries(ledger_path)))
    except BudgetLedgerCorrupt:
        ledger_verified = False
        try:
            cumulative_used = used_calls(ledger_path)
        except BudgetLedgerCorrupt:
            cumulative_used = 0

    monthly_cap = resolved_policy.internal_call_cap if resolved_policy else None
    monthly_remaining = (
        max(monthly_cap - usage["reserved"], 0) if monthly_cap is not None else None
    )

    blocked_reason = None
    if not ledger_verified:
        blocked_reason = BLOCK_LEDGER_UNVERIFIED
    elif resolved_policy is None:
        blocked_reason = BLOCK_QUOTA_UNVERIFIED
    elif usage["reserved"] >= resolved_policy.internal_call_cap:
        blocked_reason = BLOCK_MONTHLY_CAP
    elif cumulative_used >= cumulative_cap:
        blocked_reason = BLOCK_CUMULATIVE_CAP

    generated_at = utc_timestamp(now)
    return BudgetStatus(
        month=month,
        billing_unit=resolved_policy.billing_unit if resolved_policy else None,
        monthly_free_quota_units=(
            resolved_policy.free_quota_units if resolved_policy else None
        ),
        monthly_internal_cap=monthly_cap,
        monthly_reserved=usage["reserved"],
        monthly_succeeded=usage["succeeded"],
        monthly_failed=usage["failed"],
        monthly_unknown=usage["unknown"],
        monthly_internal_remaining=monthly_remaining,
        cumulative_used=cumulative_used,
        cumulative_cap=cumulative_cap,
        cumulative_remaining=max(cumulative_cap - cumulative_used, 0),
        ledger_verified=ledger_verified,
        blocked=blocked_reason is not None,
        blocked_reason=blocked_reason,
        ledger_path=str(ledger_path),
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# 상한 확인 · 예약
# ---------------------------------------------------------------------------

def check_cumulative_budget(
    planned: int,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
) -> int:
    """누적 상한만 확인하는 이전 API. 반환값은 누적 사용량."""
    used = used_calls(ledger_path)
    if used + planned > cap:
        raise BudgetExceeded(
            "누적 호출 상한을 넘는다 — 이미 {}회 사용, 이번 계획 {}회, 상한 {}회. "
            "더 호출하지 않는다.".format(used, planned, cap)
        )
    return used


def check_budget(
    planned: int,
    *,
    policy: MonthlyQuotaPolicy | None = None,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    cumulative_cap: int = CUMULATIVE_CALL_CAP,
    now: float | datetime | None = None,
) -> dict:
    """누적·월별 상한을 함께 확인한다(실행 전 사전 점검)."""
    resolved = policy or MonthlyQuotaPolicy.from_environment()
    entries = _parse_entries(ledger_path)
    reservations = _reservations(entries)
    cumulative_used = len(reservations)
    month = utc_month(now)
    monthly_used = sum(1 for entry in reservations if _month_of(entry) == month)

    if cumulative_used + planned > cumulative_cap:
        raise BudgetExceeded(
            "누적 호출 상한을 넘는다 — 이미 {}회, 이번 계획 {}회, 상한 {}회.".format(
                cumulative_used, planned, cumulative_cap
            )
        )
    if monthly_used + planned > resolved.internal_call_cap:
        raise BudgetExceeded(
            "이번 달 내부 상한을 넘는다 — 이번 달 {}회, 이번 계획 {}회, 상한 {}회.".format(
                monthly_used, planned, resolved.internal_call_cap
            )
        )
    return {
        "month": month,
        "monthly_used": monthly_used,
        "cumulative_used": cumulative_used,
    }


def reserve_call_slot(
    run_id: str,
    image_id: str,
    *,
    policy: MonthlyQuotaPolicy | None = None,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    lock_path: Path = LOCK_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
    call_id: str | None = None,
    now: float | datetime | None = None,
) -> int:
    """Google 요청 **전에** 호출 슬롯을 원자적으로 예약한다.

    - 잠금 안에서 누적·월별 사용량을 다시 읽고 두 상한을 함께 검사한다.
    - 확인되지 않은 정책이면 ``BudgetUnverified`` (fail-closed).
    - 원장이 깨졌으면 ``BudgetLedgerCorrupt`` (fail-closed).
    - 예약 기록은 flush + fsync — timeout·프로세스 중단도 차감된다.
    """
    resolved = policy or MonthlyQuotaPolicy.from_environment()
    call_id = call_id or uuid.uuid4().hex
    month = utc_month(now)
    stamp = utc_timestamp(now)

    ledger = Path(ledger_path)
    with FileLock(Path(lock_path)):
        entries = _parse_entries(ledger)
        reservations = _reservations(entries)
        cumulative_used = len(reservations)
        monthly_used = sum(1 for entry in reservations if _month_of(entry) == month)

        if cumulative_used + 1 > cap:
            raise BudgetExceeded(
                "누적 호출 상한 도달 — 이미 {}회 사용, 상한 {}회. 더 호출하지 않는다."
                .format(cumulative_used, cap)
            )
        if monthly_used + 1 > resolved.internal_call_cap:
            raise BudgetExceeded(
                "이번 달 내부 상한 도달 — 이번 달 {}회 사용, 상한 {}회. "
                "더 호출하지 않는다.".format(monthly_used, resolved.internal_call_cap)
            )

        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "kind": "reservation",
                        "run_id": run_id,
                        "call_id": call_id,
                        "id": image_id,
                        "reserved_at": stamp,
                        "month": month,
                        "billing_unit": resolved.billing_unit,
                        "status": "reserved",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        return cumulative_used + 1


def record_outcome(
    call_id: str,
    outcome: str,
    *,
    reason: str | None = None,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    lock_path: Path = LOCK_PATH,
    now: float | datetime | None = None,
) -> None:
    """호출 결과(성공/실패)를 원장에 덧붙인다. 예약 차감은 이미 끝났다.

    기록에 실패해도 예약은 남아 있으므로 **과금 안전은 예약 기록이 담당**한다.
    상태 조회에서 결과를 알 수 없는 예약은 ``unknown`` 으로 집계된다.
    """
    if outcome not in _OUTCOMES:
        raise BudgetError("outcome 은 succeeded/failed 중 하나여야 한다.")
    ledger = Path(ledger_path)
    with FileLock(Path(lock_path)):
        _parse_entries(ledger)  # 깨진 원장에는 덧붙이지 않는다
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "kind": "outcome",
                        "call_id": call_id,
                        "recorded_at": utc_timestamp(now),
                        "month": utc_month(now),
                        "status": outcome,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
