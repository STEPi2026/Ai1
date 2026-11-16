"""PHASE 9-9C.9A — preflight 구성 점검 테스트.

검증:
  - 6개 점검 코드가 모두 나오고, 각 결과가 code/ok/message 3필드뿐이다
  - 실패 케이스마다 더미 값(경로·호스트·키·형식)이 결과에 절대 노출되지 않는다
  - 존재하는 경로는 존재만 확인하고 **내용을 읽지 않는다** (더미 내용 마커 비노출)
  - 모듈이 네트워크 자원(socket/requests/urllib)을 import하지 않는다 (정적 검사)

모든 환경변수 조작은 monkeypatch 로 격리되고 세션 환경을 건드리지 않는다.
"""
from __future__ import annotations

import pathlib

import pytest

from app.integration import preflight

APP_DIR = pathlib.Path(preflight.__file__).resolve().parent.parent

# 테스트 전용 더미 값 (실제 키·실제 경로가 아니다).
DUMMY_KEYS = ["dummy-preflight-key-a", "dummy-preflight-key-b"]
DUMMY_HOST = "dummy-host.blackhole.invalid"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """6개 환경변수를 전부 지워 표준(설정 전) 상태로 만든다."""
    for name in (
        preflight.ENV_OCR_PROVIDER,
        "GOOGLE_APPLICATION_CREDENTIALS",
        preflight.ENV_TIMEOUT,
        preflight.ENV_ALLOWED_HOSTS,
        "AI1_INTERNAL_API_KEYS",
        preflight.ENV_DOCS,
    ):
        monkeypatch.delenv(name, raising=False)


def _by_code(checks, code):
    return [check for check in checks if check.code == code]


# ===========================================================================
# 구조
# ===========================================================================

def test_run_preflight_contains_all_six_checks(clean_env):
    checks = preflight.run_preflight()
    assert len(checks) == 6
    codes = {check.code for check in checks}
    assert codes == {
        "OCR_PROVIDER",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_VISION_TIMEOUT_SECONDS",
        "AI1_OCR_ALLOWED_HOSTS",
        "AI1_INTERNAL_API_KEYS",
        "AI1_ENABLE_DOCS",
    }


def test_results_have_exactly_three_fields(clean_env):
    for check in preflight.run_preflight():
        assert set(vars(check)) == {"code", "ok", "message"}
        assert isinstance(check.ok, bool)
        assert check.message


# ===========================================================================
# 기본(무설정) 상태 — S1-2 착수 전의 예상되는 미완 상태
# ===========================================================================

def test_default_state_fails_closed_items_and_keeps_https_only(clean_env):
    checks = preflight.run_preflight()
    # 1단계 착수 전에 있어야 할 fail-closed 3건
    for code in ("OCR_PROVIDER", "GOOGLE_APPLICATION_CREDENTIALS",
                 "AI1_INTERNAL_API_KEYS"):
        [check] = _by_code(checks, code)
        assert check.ok is False
        assert check.message  # 값 대신 환경변수/코드 이름만 언급
    # HTTPS-only 와 문서 비노출, 기본 timeout 은 그대로 ok
    for code in ("AI1_OCR_ALLOWED_HOSTS", "AI1_ENABLE_DOCS",
                 "GOOGLE_VISION_TIMEOUT_SECONDS"):
        [check] = _by_code(checks, code)
        assert check.ok is True


# ===========================================================================
# OCR provider
# ===========================================================================

def test_provider_google_vision_is_ok(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_OCR_PROVIDER, "google_vision")
    [check] = _by_code(preflight.run_preflight(), "OCR_PROVIDER")
    assert check.ok is True


def test_provider_wrong_value_is_not_echoed(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_OCR_PROVIDER, "mathpix")
    [check] = _by_code(preflight.run_preflight(), "OCR_PROVIDER")
    assert check.ok is False
    assert "mathpix" not in check.message


# ===========================================================================
# Google credentials — 경로 존재만 검사, 내용 미열람
# ===========================================================================

def test_credentials_existing_path_ok_and_content_never_read(
    clean_env, monkeypatch, tmp_path
):
    dummy = tmp_path / "dummy-sa.json"
    dummy.write_text('{"dummy-secret-content":"x"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(dummy))
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_APPLICATION_CREDENTIALS")
    assert check.ok is True
    # 값(경로)과 파일 내용(더미 마커) 어느 쪽도 결과에 없다
    assert str(dummy) not in check.message
    assert "dummy-secret-content" not in check.message


def test_credentials_missing_path_not_echoed(clean_env, monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\dummy\not-exist\dummy-sa.json",
    )
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_APPLICATION_CREDENTIALS")
    assert check.ok is False
    assert "not-exist" not in check.message
    assert "dummy-sa.json" not in check.message


def test_credentials_directory_path_rejected_without_echo(clean_env, monkeypatch, tmp_path):
    """디렉터리는 서비스 계정 JSON 이 아니다 — 존재만으로 통과시키지 않는다."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path))
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_APPLICATION_CREDENTIALS")
    assert check.ok is False
    assert str(tmp_path) not in check.message


# ===========================================================================
# Timeout — 파싱 · 0 < 값 <= 25, 값 비노출
# ===========================================================================

@pytest.mark.parametrize("bad", ["-3", "26", "999", "abc", "8.5s", "nan", "inf", "-inf"])
def test_timeout_bad_values_rejected_and_not_echoed(clean_env, monkeypatch, bad):
    monkeypatch.setenv(preflight.ENV_TIMEOUT, bad)
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_VISION_TIMEOUT_SECONDS")
    assert check.ok is False
    assert bad not in check.message


def test_timeout_zero_rejected(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_TIMEOUT, "0")
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_VISION_TIMEOUT_SECONDS")
    assert check.ok is False
    assert "positive" in check.message or "0보다" in check.message


def test_timeout_boundary_25_is_ok(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_TIMEOUT, "25")
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_VISION_TIMEOUT_SECONDS")
    assert check.ok is True


def test_timeout_valid_value_keeps_message_value_free(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_TIMEOUT, "12")
    [check] = _by_code(preflight.run_preflight(), "GOOGLE_VISION_TIMEOUT_SECONDS")
    assert check.ok is True
    assert "12" not in check.message


# ===========================================================================
# Allowed hosts — 파싱, 호스트 비노출
# ===========================================================================

def test_allowed_hosts_parsed_with_count(clean_env, monkeypatch):
    monkeypatch.setenv(
        preflight.ENV_ALLOWED_HOSTS,
        f"{DUMMY_HOST}, other.dummy.invalid",
    )
    [check] = _by_code(preflight.run_preflight(), "AI1_OCR_ALLOWED_HOSTS")
    assert check.ok is True
    assert "2" in check.message
    assert DUMMY_HOST not in check.message
    assert "other.dummy.invalid" not in check.message


def test_allowed_hosts_whitespace_only_treated_as_unset(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_ALLOWED_HOSTS, "  ,  ")
    [check] = _by_code(preflight.run_preflight(), "AI1_OCR_ALLOWED_HOSTS")
    assert check.ok is True
    assert DUMMY_HOST not in check.message


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
    ],
)
def test_allowed_hosts_rejects_non_bare_hostnames(clean_env, monkeypatch, bad):
    """provider 는 hostname exact match — 스킴/경로/포트가 붙으면 매칭되지 않는다."""
    monkeypatch.setenv(preflight.ENV_ALLOWED_HOSTS, bad)
    [check] = _by_code(preflight.run_preflight(), "AI1_OCR_ALLOWED_HOSTS")
    assert check.ok is False
    assert bad not in check.message


@pytest.mark.parametrize(
    "value", ["backend.example", "LOCALHOST", " sub.example ", "a-b.example", "x"]
)
def test_allowed_hosts_accepts_bare_hostnames(clean_env, monkeypatch, value):
    monkeypatch.setenv(preflight.ENV_ALLOWED_HOSTS, value)
    [check] = _by_code(preflight.run_preflight(), "AI1_OCR_ALLOWED_HOSTS")
    assert check.ok is True
    assert "1" in check.message
    assert value.strip() not in check.message


# ===========================================================================
# Internal keys — 설정 여부만, 키 비노출
# ===========================================================================

def test_internal_keys_reported_configured_without_echo(clean_env, monkeypatch):
    monkeypatch.setenv("AI1_INTERNAL_API_KEYS", ", ".join(DUMMY_KEYS))
    [check] = _by_code(preflight.run_preflight(), "AI1_INTERNAL_API_KEYS")
    assert check.ok is True
    assert "2" in check.message
    for key in DUMMY_KEYS:
        assert key not in check.message


def test_internal_keys_partial_only_counts_valid_entries(clean_env, monkeypatch):
    monkeypatch.setenv("AI1_INTERNAL_API_KEYS", DUMMY_KEYS[0] + ", ,  ")
    [check] = _by_code(preflight.run_preflight(), "AI1_INTERNAL_API_KEYS")
    assert check.ok is True
    assert "1" in check.message
    assert DUMMY_KEYS[0] not in check.message


# ===========================================================================
# Docs 기본 비노출
# ===========================================================================

def test_docs_enabled_is_flagged(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_DOCS, "1")
    [check] = _by_code(preflight.run_preflight(), "AI1_ENABLE_DOCS")
    assert check.ok is False


def test_docs_off_value_is_ok(clean_env, monkeypatch):
    monkeypatch.setenv(preflight.ENV_DOCS, "0")
    [check] = _by_code(preflight.run_preflight(), "AI1_ENABLE_DOCS")
    assert check.ok is True


# ===========================================================================
# 비노출·네트워크 0회 정적 방어
# ===========================================================================

def test_mixed_dummy_state_leaks_no_value_or_url_anywhere(clean_env, monkeypatch):
    """전부 더미 상태에서도 어떤 결과 필드에도 값·URL 스킴이 없다."""
    monkeypatch.setenv(preflight.ENV_OCR_PROVIDER, "mathpix")
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", r"C:\dummy\leak-test\sa.json"
    )
    monkeypatch.setenv(preflight.ENV_TIMEOUT, "40")
    monkeypatch.setenv(preflight.ENV_ALLOWED_HOSTS, DUMMY_HOST)
    monkeypatch.setenv("AI1_INTERNAL_API_KEYS", ", ".join(DUMMY_KEYS))
    monkeypatch.setenv(preflight.ENV_DOCS, "1")
    report = preflight.preflight_report()
    for serialized in [report] + [
        check.message for check in preflight.run_preflight()
    ]:
        for secret in (
            DUMMY_HOST, DUMMY_KEYS[0], DUMMY_KEYS[1],
            "mathpix", "leak-test", "sa.json",
        ):
            assert secret not in serialized
        assert "http://" not in serialized
        assert "https://" not in serialized


def test_module_imports_no_network_libraries():
    """preflight 모듈이 네트워크 자원을 import하지 않는다 (os/pathlib/dataclasses 만)."""
    source = pathlib.Path(preflight.__file__).read_text(encoding="utf-8")
    for banned in ("import socket", "import requests", "import urllib",
                   "import http", "from urllib", "from http", "import urllib3"):
        assert banned not in source
