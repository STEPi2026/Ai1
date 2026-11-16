"""PHASE 9-9C.9A 후속 — 허용 호스트 파싱 정책 테스트.

``AI1_OCR_ALLOWED_HOSTS`` 는 provider 의 ``urlsplit(url).hostname`` 과
exact match 된다. 스킴·경로·포트가 붙은 항목은 매칭될 수 없으므로
preflight(진단)와 provider(런타임)가 같은 판정을 쓰는지 고정한다.
"""
from __future__ import annotations

import pytest

from app.integration.host_policy import is_valid_host, split_allowed_hosts


@pytest.mark.parametrize("raw", [None, "", "   ", " , , "])
def test_unset_or_blank_yields_no_hosts(raw):
    assert split_allowed_hosts(raw) == ((), 0)


def test_valid_entries_are_trimmed_and_lowercased():
    hosts, invalid = split_allowed_hosts(
        " Backend.Example , other.example.invalid ,LOCALHOST "
    )
    assert hosts == ("backend.example", "other.example.invalid", "localhost")
    assert invalid == 0


@pytest.mark.parametrize(
    "bad",
    [
        "https://backend.example",
        "backend.example/path",
        "backend.example:443",
        "backend example",
        "back@end.example",
        "backend..example",
        "backend.example.",
        "-backend.example",
        "backend-.example",
        "back_end.example",
    ],
)
def test_non_bare_hostnames_are_invalid(bad):
    hosts, invalid = split_allowed_hosts(bad)
    assert hosts == ()
    assert invalid == 1
    assert is_valid_host(bad.strip().lower()) is False


@pytest.mark.parametrize("good", ["backend.example", "a-b.example", "x", "a.b.c"])
def test_bare_hostnames_are_valid(good):
    assert is_valid_host(good) is True


def test_mixed_entries_report_valid_hosts_and_invalid_count():
    hosts, invalid = split_allowed_hosts(
        "backend.example, https://bad.example, ok.example:443, other.example"
    )
    assert hosts == ("backend.example", "other.example")
    assert invalid == 2
