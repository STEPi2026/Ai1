"""PHASE 9-9C.8 — 내부 서비스 간 인증 guard 테스트.

계약(협의서 §3.1):
  - Header ``X-Internal-Api-Key``; 환경변수 ``AI1_INTERNAL_API_KEYS`` (쉼표
    다중 키, 교체 기간 구·신 동시 허용)
  - 누락/불일치 → 401 ``E_AUTH_INVALID_KEY``; 키 미설정/빈 설정 → fail-closed
    503 ``E_AUTH_NOT_CONFIGURED``
  - ``/health`` 만 예외, legacy 경로 포함 전부 보호
  - 인증 실패 시 provider 초기화·호출·budget ledger 예약이 전부 0 회
  - 키 값은 응답·오류에 노출되지 않는다; 비교는 constant-time

테스트에는 dummy 키만 사용한다(실제 Backend 키는 아직 전달되지 않았다).
conftest 의 autouse bypass 를 이 테스트들에서는 제거해 실제 guard 를 검증한다.
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.integration import internal_auth
from app.main import app

client = TestClient(app)

HEADER = internal_auth.HEADER_NAME
HEADER_ENV = internal_auth.ENV_KEYS

DUMMY_OLD_KEY = "dummy-test-key-old"
DUMMY_NEW_KEY = "dummy-test-key-new"

LEGACY_BODY = {"problem_latex": "2x=6", "solution_text": "2x=6\nx=3"}

# 보호 대상 경로 (협의서 §3.1 #6: /health 를 뺀 전부, legacy 포함).
# body 가 필요한 경로는 유효한 요청 본문을 함께 보낸다 — 401 이 파일 검증보다
# 먼저 나오는지 여부는 별도 테스트에서 고정한다.
PROTECTED_CASES = [
    ("GET", "/api/v1/attempts/A1/analyses", None),
    ("GET", "/api/v1/attempts/A1/analyses/X1", None),
    ("GET", "/api/v1/students/S1/history", None),
    ("GET", "/api/v1/students/S1/skills", None),
    ("GET", "/api/v1/students/S1/misconceptions/top", None),
    (
        "POST",
        "/api/v1/attempts/A1/ocr",
        {"image_reference": "https://backend.example.invalid/page1.jpg"},
    ),
    ("POST", "/analyze-solution", dict(LEGACY_BODY)),
]


@pytest.fixture()
def real_auth(monkeypatch):
    """conftest autouse 우회를 제거하고 dummy 키 환경변수를 설정한다."""
    monkeypatch.setenv(HEADER_ENV, f"{DUMMY_OLD_KEY},{DUMMY_NEW_KEY}")
    app.dependency_overrides.pop(internal_auth.require_internal_auth, None)
    yield (DUMMY_OLD_KEY, DUMMY_NEW_KEY)
    app.dependency_overrides.pop(internal_auth.require_internal_auth, None)


@pytest.fixture()
def unconfigured_auth(monkeypatch):
    monkeypatch.delenv(HEADER_ENV, raising=False)
    app.dependency_overrides.pop(internal_auth.require_internal_auth, None)
    yield None
    app.dependency_overrides.pop(internal_auth.require_internal_auth, None)


def _call(method, path, json_body=None, key=None):
    headers = {HEADER: key} if key is not None else {}
    request = getattr(client, method.lower())
    kwargs = {"headers": headers}
    if json_body is not None:
        kwargs["json"] = json_body
    return request(path, **kwargs)


# ===========================================================================
# 1. 키 없음/오류 키
# ===========================================================================

@pytest.mark.parametrize("method,path,json_body", PROTECTED_CASES)
def test_every_route_excluding_health_requires_key(real_auth, method, path, json_body):
    r = _call(method, path, json_body)
    assert r.status_code == 401, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "E_AUTH_INVALID_KEY"
    assert set(detail) == {"code", "message"}


def test_wrong_key_is_401(real_auth):
    r = _call("POST", "/analyze-solution", LEGACY_BODY, key="definitely-wrong-dummy")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "E_AUTH_INVALID_KEY"


def test_health_is_exempt_from_auth(unconfigured_auth):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_precedes_body_validation(real_auth):
    """인증은 요청 본문 검증보다 선행한다 — 요청당 검사 1회 규칙."""
    r = client.post("/analyze-solution", json={})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "E_AUTH_INVALID_KEY"

    # PROTECTED_CASES 는 OCR/legacy/조회 경로를 덮는다. attempt 분석 경로도
    # 별도로 확인해 "7개 라우트 전부" 주장이 테스트와 어긋나지 않게 한다.
    r = client.post(
        "/api/v1/attempts/A1/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": "2(x-3)=6",
            "problem_skills": ["SKL-1-004"],
            "student_id": "S1",
            "solution_text": "2x-6=6",
        },
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "E_AUTH_INVALID_KEY"


# ===========================================================================
# 2. 키 미설정 → fail-closed 503
# ===========================================================================

@pytest.mark.parametrize("value", [None, "", " ", ",,", " , , "])
def test_unconfigured_keys_fail_closed_503(unconfigured_auth, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(HEADER_ENV, value)
    r = _call("POST", "/analyze-solution", LEGACY_BODY)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "E_AUTH_NOT_CONFIGURED"
    assert set(detail) == {"code", "message"}
    # fail-closed: 결과 본문을 꾸며내지 않는다
    assert "analysis_status" not in r.json()["detail"]


# ===========================================================================
# 3. 정상 키·회전(구·신 동시 허용)
# ===========================================================================

def test_valid_key_returns_200(real_auth):
    r = _call("POST", "/analyze-solution", LEGACY_BODY, key=DUMMY_NEW_KEY)
    assert r.status_code == 200, r.text


def test_old_and_new_keys_are_both_accepted_during_rotation(real_auth):
    for key in (DUMMY_OLD_KEY, DUMMY_NEW_KEY):
        r = _call("POST", "/analyze-solution", LEGACY_BODY, key=key)
        assert r.status_code == 200, r.text


def test_key_rotation_reflected_without_restart(real_auth, monkeypatch):
    r = _call("POST", "/analyze-solution", LEGACY_BODY, key="dummy-test-key-c")
    assert r.status_code == 401
    monkeypatch.setenv(HEADER_ENV, f"{DUMMY_OLD_KEY},dummy-test-key-c")
    r = _call("POST", "/analyze-solution", LEGACY_BODY, key="dummy-test-key-c")
    assert r.status_code == 200, r.text


# ===========================================================================
# 4. 인증 실패 시 provider·budget 부작용 0 회
# ===========================================================================

def test_auth_failure_runs_no_provider_or_budget(monkeypatch, real_auth):
    import app.main as main_module
    from app.integration import google_vision as gv
    from app.integration import google_vision_budget as budget

    probes = {"require_ocr_provider": 0, "from_environment": 0, "budget_lock": 0}

    def probe_require():
        probes["require_ocr_provider"] += 1
        return None

    def probe_from_environment(*args, **kwargs):
        probes["from_environment"] += 1
        return object()

    def probe_lock_enter(self):
        # reserve_call_slot 은 FileLock 을 통해서만 ledger 에 예약한다.
        # 이 진입이 0회면 budget 예약도 0회다.
        probes["budget_lock"] += 1
        return self

    monkeypatch.setattr(main_module, "require_ocr_provider", probe_require)
    monkeypatch.setattr(gv.GoogleVisionProvider, "from_environment", probe_from_environment)
    monkeypatch.setattr(budget.FileLock, "__enter__", probe_lock_enter)

    r = client.post(
        "/api/v1/attempts/A1/ocr",
        json={"image_reference": "https://backend.example.invalid/page1.jpg"},
    )

    assert r.status_code == 401
    assert probes["require_ocr_provider"] == 0
    assert probes["from_environment"] == 0
    assert probes["budget_lock"] == 0


# ===========================================================================
# 5. 키·헤더 값 비노출
# ===========================================================================

def test_key_values_never_appear_in_auth_responses(real_auth):
    secret_marker = "dummy-test-DO-NOT-LEAK-VALUE"
    for key in (secret_marker, DUMMY_OLD_KEY):
        r = _call("POST", "/analyze-solution", LEGACY_BODY, key=key)
        assert secret_marker not in r.text
        assert DUMMY_OLD_KEY not in r.text
        assert DUMMY_NEW_KEY not in r.text


def test_unconfigured_503_leaks_no_key(unconfigured_auth):
    r = _call("POST", "/analyze-solution", LEGACY_BODY, key="whatever")
    assert r.status_code == 503
    assert "whatever" not in r.text


# ===========================================================================
# 6. constant-time 비교 (정적 검사)
# ===========================================================================

def test_comparison_is_constant_time_and_checks_all_keys():
    source = inspect.getsource(internal_auth.require_internal_auth)
    assert "compare_digest" in source
    # 모든 설정 키를 대상으로 비교하되 match 에서 조기 탈출하지 않는다
    assert "for key in keys" in source
    assert "secrets" in inspect.getsource(internal_auth)


# ===========================================================================
# 7. /docs·/openapi.json 비노출 (인증 밖 기본 라우트)
# ===========================================================================

_DOCS_PROBE = (
    "from app.main import app;"
    "print(app.docs_url, app.redoc_url, app.openapi_url)"
)


def _probe_docs_with_env(value):
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("AI1_ENABLE_DOCS", None)
    if value is not None:
        env["AI1_ENABLE_DOCS"] = value
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    completed = subprocess.run(
        [sys.executable, "-c", _DOCS_PROBE],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_docs_routes_are_hidden_by_default():
    """키 없이 전체 스키마가 노출되지 않도록 기본은 docs/openapi 를 끈다."""
    assert _probe_docs_with_env(None) == "None None None"
    assert _probe_docs_with_env("0") == "None None None"


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_docs_routes_are_opt_in_for_local_development(value):
    assert _probe_docs_with_env(value) == "/docs /redoc /openapi.json"


# ===========================================================================
# O-06 갭 보강 (PHASE 9-9C.9A) — URL·키·원문 비노출의 코드 전체 정적 검사
# ===========================================================================

_LOGGING_STATEMENT = re.compile(
    r"^\s*(import logging\b|from logging\b|logging\.\w|print\(|logger\s*=)"
)


def test_app_code_has_no_logging_or_print_statements():
    """R-11("앱 코드에 logger/print 없음")·O-06 정적 방어 — app/ 전체를 스캔한다.

    협의서 §3.5 #20·R-11 은 URL·키·OCR 원문이 로그에 남지 않아야 하며 AI¹ 은
    로깅 자체가 없다. 어떤 라우트에도 `import logging`·`print` 등 출력
    문은 없어야 한다 (오류는 HTTP 오류 본문으로만 나간다).
    """
    from app import main as main_module

    app_dir = pathlib.Path(main_module.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _LOGGING_STATEMENT.match(line):
                offenders.append(f"{path.name}:{no}")
    assert offenders == []

