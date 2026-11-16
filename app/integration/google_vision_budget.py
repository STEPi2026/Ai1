"""Local cumulative call guard shared by the Vision app provider and eval probe.

This is a conservative project-local demo limit, not a billing-account quota.
The billing account's remaining free units must still be checked externally.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOOGLE_DIR = PROJECT_ROOT / "eval" / "_local" / "google_vision"
BUDGET_LEDGER_PATH = GOOGLE_DIR / "call_budget.jsonl"
LOCK_PATH = GOOGLE_DIR / "call_budget.lock"
CUMULATIVE_CALL_CAP = 40
LOCK_TIMEOUT = 60.0
LOCK_STALE_AFTER = 300.0


class BudgetExceeded(RuntimeError):
    """The project's cumulative demo call cap has been reached."""


class BudgetLockError(RuntimeError):
    """The call-budget ledger could not be locked safely."""


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


def used_calls(ledger_path: Path = BUDGET_LEDGER_PATH) -> int:
    """Return reserved call slots, including timed-out/uncertain attempts."""
    path = Path(ledger_path)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def check_cumulative_budget(
    planned: int,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
) -> int:
    """Early preflight check; reserve_call_slot remains the race-safe guard."""
    used = used_calls(ledger_path)
    if used + planned > cap:
        raise BudgetExceeded(
            "누적 호출 상한을 넘는다 — 이미 {}회 사용, 이번 계획 {}회, 상한 {}회. "
            "더 호출하지 않는다.".format(used, planned, cap)
        )
    return used


def reserve_call_slot(
    run_id: str,
    image_id: str,
    *,
    ledger_path: Path = BUDGET_LEDGER_PATH,
    lock_path: Path = LOCK_PATH,
    cap: int = CUMULATIVE_CALL_CAP,
) -> int:
    """Durably reserve one call before sending a paid/quota-consuming request."""
    ledger = Path(ledger_path)
    with FileLock(Path(lock_path)):
        used = used_calls(ledger)
        if used + 1 > cap:
            raise BudgetExceeded(
                "누적 호출 상한 도달 — 이미 {}회 사용, 상한 {}회. 더 호출하지 않는다."
                .format(used, cap)
            )
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "id": image_id,
                        "reserved_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                        "status": "reserved",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        return used + 1
