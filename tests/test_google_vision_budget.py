"""Google Vision 호출 예산 가드 — 월별 내부 상한 + 누적 상한.

**실제 Google Vision API 를 호출하지 않는다.** 모든 테스트는 임시 원장과
가짜 시각을 쓰며, 저장소의 실제 원장(`eval/_local/...`)은 건드리지 않는다.

핵심 원칙(fail-closed):
  - 무료 한도가 확인되지 않았거나 설정이 안전하지 않으면 호출하지 않는다.
  - 원장을 읽을 수 없거나 형식이 깨졌으면 호출하지 않는다.
  - 실패·timeout 도 예약 단계에서 이미 차감됐으므로 상한에 포함된다.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integration import google_vision_budget as budget  # noqa: E402

OCTOBER_STAMP = "2026-10-15T12:00:00Z"
NOVEMBER_STAMP = "2026-11-02T12:00:00Z"
OCTOBER = datetime(2026, 10, 15, 12, tzinfo=timezone.utc).timestamp()
NOVEMBER = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()


def _policy(free=100, cap=10, unit=budget.BILLING_UNIT_DOCUMENT_TEXT_DETECTION):
    return budget.MonthlyQuotaPolicy(
        free_quota_units=free,
        internal_call_cap=cap,
        billing_unit=unit,
    )


def _paths(tmp_path):
    return tmp_path / "call_budget.jsonl", tmp_path / "call_budget.lock"


def _reserve(ledger, lock, *, now, policy=None, call_id=None, cap=40):
    return budget.reserve_call_slot(
        "r",
        "G01",
        policy=policy or _policy(),
        ledger_path=ledger,
        lock_path=lock,
        cap=cap,
        call_id=call_id,
        now=now,
    )


def _write_reservation(ledger, *, at, call_id, index=0):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": "reservation",
                    "call_id": call_id,
                    "run_id": "r",
                    "id": "G{:02d}".format(index),
                    "reserved_at": at,
                    "status": "reserved",
                }
            )
            + "\n"
        )


# ---------------------------------------------------------------------------
# 정책 검증 — 무료 한도/과금 단위를 추정하지 않는다
# ---------------------------------------------------------------------------

def test_policy_requires_all_environment_values():
    with pytest.raises(budget.BudgetUnverified):
        budget.MonthlyQuotaPolicy.from_environment(env={})

    env = {
        budget.ENV_BILLING_UNIT: "document_text_detection",
        budget.ENV_MONTHLY_FREE_QUOTA: "100",
        budget.ENV_MONTHLY_CALL_CAP: "10",
        budget.ENV_FREE_QUOTA_CONFIRMED: "1",
    }
    policy = budget.MonthlyQuotaPolicy.from_environment(env=env)
    assert policy.internal_call_cap == 10
    assert policy.free_quota_units == 100


def test_policy_requires_explicit_operator_confirmation():
    env = {
        budget.ENV_BILLING_UNIT: "document_text_detection",
        budget.ENV_MONTHLY_FREE_QUOTA: "100",
        budget.ENV_MONTHLY_CALL_CAP: "10",
    }
    with pytest.raises(budget.BudgetUnverified, match="CONFIRMED"):
        budget.MonthlyQuotaPolicy.from_environment(env=env)


def test_policy_internal_cap_must_be_lower_than_free_quota():
    with pytest.raises(budget.BudgetUnverified, match="작아야"):
        _policy(free=10, cap=10)
    with pytest.raises(budget.BudgetUnverified, match="작아야"):
        _policy(free=10, cap=11)


def test_policy_rejects_billing_unit_mismatch():
    env = {
        budget.ENV_BILLING_UNIT: "text_detection",
        budget.ENV_MONTHLY_FREE_QUOTA: "100",
        budget.ENV_MONTHLY_CALL_CAP: "10",
        budget.ENV_FREE_QUOTA_CONFIRMED: "1",
    }
    with pytest.raises(budget.BudgetUnverified, match="호출 단위"):
        budget.MonthlyQuotaPolicy.from_environment(env=env)


def test_policy_rejects_non_positive_quota_values():
    for raw in ("0", "-1", "abc", ""):
        env = {
            budget.ENV_BILLING_UNIT: "document_text_detection",
            budget.ENV_MONTHLY_FREE_QUOTA: raw,
            budget.ENV_MONTHLY_CALL_CAP: "10",
            budget.ENV_FREE_QUOTA_CONFIRMED: "1",
        }
        with pytest.raises(budget.BudgetUnverified):
            budget.MonthlyQuotaPolicy.from_environment(env=env)


def test_reservation_without_a_confirmed_policy_is_refused(tmp_path, monkeypatch):
    ledger, lock = _paths(tmp_path)
    for key in (
        budget.ENV_BILLING_UNIT,
        budget.ENV_MONTHLY_FREE_QUOTA,
        budget.ENV_MONTHLY_CALL_CAP,
        budget.ENV_FREE_QUOTA_CONFIRMED,
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(budget.BudgetUnverified):
        budget.reserve_call_slot("r", "G01", ledger_path=ledger, lock_path=lock)
    assert not ledger.exists()


# ---------------------------------------------------------------------------
# 상한
# ---------------------------------------------------------------------------

def test_monthly_cap_blocks_before_the_call(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=2)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy)
    with pytest.raises(budget.BudgetExceeded, match="이번 달"):
        _reserve(ledger, lock, now=NOVEMBER, policy=policy)
    assert budget.used_calls(ledger) == 2


def test_cumulative_cap_blocks_even_when_the_month_is_fresh(tmp_path):
    ledger, lock = _paths(tmp_path)
    for index in range(3):
        _write_reservation(
            ledger, at=OCTOBER_STAMP, call_id="old-{}".format(index), index=index
        )
    policy = _policy(free=1000, cap=100)
    with pytest.raises(budget.BudgetExceeded, match="누적"):
        budget.reserve_call_slot(
            "r", "G99", policy=policy, ledger_path=ledger, lock_path=lock, cap=3
        )
    assert budget.used_calls(ledger) == 3


def test_month_boundary_separates_usage_without_deleting_old_records(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=2)
    _reserve(ledger, lock, now=OCTOBER, policy=policy)
    _reserve(ledger, lock, now=OCTOBER, policy=policy)
    with pytest.raises(budget.BudgetExceeded):
        _reserve(ledger, lock, now=OCTOBER, policy=policy)
    # 새 달은 새 구간이지만, 이전 달 기록은 그대로 남아 있어야 한다.
    _reserve(ledger, lock, now=NOVEMBER, policy=policy)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy)
    assert budget.month_usage("2026-10", ledger)["reserved"] == 2
    assert budget.month_usage("2026-11", ledger)["reserved"] == 2
    assert budget.used_calls(ledger) == 4


def test_failed_attempts_still_count_against_the_cap(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=1)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy, call_id="first")
    budget.record_outcome(
        "first", budget.OUTCOME_FAILED, reason="Timeout", ledger_path=ledger,
        lock_path=lock, now=NOVEMBER,
    )
    usage = budget.month_usage("2026-11", ledger)
    assert usage["reserved"] == 1
    assert usage["failed"] == 1
    with pytest.raises(budget.BudgetExceeded):
        _reserve(ledger, lock, now=NOVEMBER, policy=policy)


def test_outcomes_are_reported_without_double_counting(tmp_path):
    ledger, lock = _paths(tmp_path)
    _reserve(ledger, lock, now=NOVEMBER, policy=_policy(), call_id="a")
    _reserve(ledger, lock, now=NOVEMBER, policy=_policy(), call_id="b")
    _reserve(ledger, lock, now=NOVEMBER, policy=_policy(), call_id="c")
    budget.record_outcome(
        "a", "succeeded", ledger_path=ledger, lock_path=lock, now=NOVEMBER
    )
    budget.record_outcome(
        "b", "failed", reason="PermissionDenied", ledger_path=ledger,
        lock_path=lock, now=NOVEMBER,
    )
    usage = budget.month_usage("2026-11", ledger)
    assert usage == {
        "month": "2026-11",
        "reserved": 3,
        "succeeded": 1,
        "failed": 1,
        "unknown": 1,
    }


def test_concurrent_reservations_never_exceed_the_monthly_cap(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=5)
    granted: list[int] = []
    refused: list[BaseException] = []

    def worker(index):
        try:
            budget.reserve_call_slot(
                "r", "G{:02d}".format(index), policy=policy,
                ledger_path=ledger, lock_path=lock, now=NOVEMBER,
            )
            granted.append(index)
        except budget.BudgetError as exc:  # BudgetExceeded / LockError
            refused.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 5
    assert len(refused) == 15
    assert budget.used_calls(ledger) == 5


# ---------------------------------------------------------------------------
# 손상된 원장 — 확정할 수 없으면 차단
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        '{"kind":"reservation","run_id":"r"}\n',                # reserved_at 없음
        '{"kind":"reservation","reserved_at":"2026-13-01T00:00:00Z"}\n',
        '{"kind":"unknown","reserved_at":"2026-11-01T00:00:00Z"}\n',
        '["not","an","object"]\n',
    ],
)
def test_corrupt_ledger_blocks_reservations(tmp_path, content):
    ledger, lock = _paths(tmp_path)
    ledger.write_text(content, encoding="utf-8")
    with pytest.raises(budget.BudgetLedgerCorrupt):
        _reserve(ledger, lock, now=NOVEMBER)


def test_unreadable_ledger_blocks_reservations(tmp_path):
    ledger, lock = _paths(tmp_path)
    ledger.mkdir(parents=True)  # 읽기 불가(디렉터리) 상황
    with pytest.raises(budget.BudgetLedgerCorrupt):
        _reserve(ledger, lock, now=NOVEMBER)


# ---------------------------------------------------------------------------
# 상태 조회
# ---------------------------------------------------------------------------

def test_status_reports_monthly_and_cumulative_usage(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=10)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy, call_id="a")
    _reserve(ledger, lock, now=NOVEMBER, policy=policy, call_id="b")
    _reserve(ledger, lock, now=OCTOBER, policy=policy, call_id="old")
    budget.record_outcome(
        "a", "succeeded", ledger_path=ledger, lock_path=lock, now=NOVEMBER
    )
    budget.record_outcome(
        "b", "failed", ledger_path=ledger, lock_path=lock, now=NOVEMBER
    )

    state = budget.status(
        policy=policy, ledger_path=ledger, cumulative_cap=40, now=NOVEMBER
    )
    assert state.month == "2026-11"
    assert state.monthly_reserved == 2
    assert state.monthly_succeeded == 1
    assert state.monthly_failed == 1
    assert state.monthly_unknown == 0
    assert state.monthly_internal_remaining == 8
    assert state.cumulative_used == 3
    assert state.cumulative_remaining == 37
    assert state.blocked is False
    assert state.blocked_reason is None


def test_status_reports_why_calls_are_blocked(tmp_path):
    ledger, lock = _paths(tmp_path)
    policy = _policy(free=100, cap=1)
    _reserve(ledger, lock, now=NOVEMBER, policy=policy)

    state = budget.status(policy=policy, ledger_path=ledger, now=NOVEMBER)
    assert state.blocked is True
    assert state.blocked_reason == budget.BLOCK_MONTHLY_CAP

    corrupt = tmp_path / "bad.jsonl"
    corrupt.write_text("broken\n", encoding="utf-8")
    state = budget.status(policy=policy, ledger_path=corrupt, now=NOVEMBER)
    assert state.blocked is True
    assert state.blocked_reason == budget.BLOCK_LEDGER_UNVERIFIED


def test_status_reports_unverified_quota_as_blocked(tmp_path, monkeypatch):
    for key in (
        budget.ENV_BILLING_UNIT,
        budget.ENV_MONTHLY_FREE_QUOTA,
        budget.ENV_MONTHLY_CALL_CAP,
        budget.ENV_FREE_QUOTA_CONFIRMED,
    ):
        monkeypatch.delenv(key, raising=False)
    state = budget.status(policy=None, ledger_path=tmp_path / "x.jsonl")
    # 정책이 없으면(환경변수 미확인) 호출 불가 사유를 알려 준다.
    assert state.blocked is True
    assert state.blocked_reason == budget.BLOCK_QUOTA_UNVERIFIED


# ---------------------------------------------------------------------------
# provider 연결 — 오류 매핑 · 자동 재시도 없음
# ---------------------------------------------------------------------------

IMAGE_URI = "https://backend.example.invalid/files/demo.png?sig=FAKE"


class _FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def document_text_detection(self, *, image, image_context, timeout, retry):
        self.calls += 1
        if self.error is not None:
            raise self.error
        raise AssertionError("이 테스트는 Vision 응답을 만들지 않는다")


class _FakeVision:
    class ImageSource:
        def __init__(self, *, image_uri):
            self.image_uri = image_uri

    class Image:
        def __init__(self, *, source=None, content=None):
            self.source = source
            self.content = content


def _provider(client, *, reserve_slot, record_call=None):
    from app.integration.google_vision import GoogleVisionProvider

    return GoogleVisionProvider(
        client,
        _FakeVision,
        language_hint=None,
        reserve_slot=reserve_slot,
        monthly_policy=_policy(),
        record_call=record_call,
        fetch_image=lambda uri: b"fake-image-bytes",
    )


def _request():
    from app.integration.ocr import OcrProviderRequest

    return OcrProviderRequest(attempt_id="G01", image_reference=IMAGE_URI)


def test_unverified_policy_maps_to_503_not_configured():
    from app.integration.ocr import OcrNotConfiguredError

    client = _FakeClient()

    def reserve(*args, **kwargs):
        raise budget.BudgetUnverified("quota unverified")

    with pytest.raises(OcrNotConfiguredError):
        _provider(client, reserve_slot=reserve).recognize(_request())
    assert client.calls == 0


def test_corrupt_ledger_maps_to_503_not_configured():
    from app.integration.ocr import OcrNotConfiguredError

    client = _FakeClient()

    def reserve(*args, **kwargs):
        raise budget.BudgetLedgerCorrupt("ledger broken")

    with pytest.raises(OcrNotConfiguredError):
        _provider(client, reserve_slot=reserve).recognize(_request())
    assert client.calls == 0


def test_failed_google_call_is_recorded_and_not_retried():
    from app.integration.ocr import OcrProviderError

    client = _FakeClient(error=RuntimeError("upstream timeout"))
    outcomes = []

    def reserve(*args, **kwargs):
        return 1

    provider = _provider(
        client,
        reserve_slot=reserve,
        record_call=lambda call_id, outcome, **kwargs: outcomes.append(
            (call_id, outcome, kwargs.get("reason"))
        ),
    )
    with pytest.raises(OcrProviderError):
        provider.recognize(_request())

    assert client.calls == 1  # 자동 재시도 없음
    assert len(outcomes) == 1
    assert outcomes[0][1] == "failed"
    assert outcomes[0][2] == "RuntimeError"


def test_successful_google_call_is_recorded_once():
    outcomes = []

    class OkClient(_FakeClient):
        def document_text_detection(self, *, image, image_context, timeout, retry):
            self.calls += 1
            from types import SimpleNamespace

            return SimpleNamespace(
                full_text_annotation=SimpleNamespace(text="x=1", pages=[])
            )

    client = OkClient()
    provider = _provider(
        client,
        reserve_slot=lambda *args, **kwargs: 1,
        record_call=lambda call_id, outcome, **kwargs: outcomes.append(outcome),
    )
    provider.recognize(_request())
    assert outcomes == ["succeeded"]
