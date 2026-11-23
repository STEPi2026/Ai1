"""배포 스모크 스크립트 테스트 (scripts/smoke_deploy.py) — 네트워크 없음.

``httpx.MockTransport`` 로 서버 응답을 흉내 낸다. 실제 배포 서버는 호출하지
않으며, 키 문자열이 출력에 남지 않는지 capsys 로 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from smoke_deploy import (  # noqa: E402
    ENV_KEY,
    HEADER_NAME,
    main,
    run_smoke,
)

KEY = "dummy-smoke-key-not-real"
BASE = "https://smoke.example.com"

ANALYZE_OK = {
    "analysis_id": "SMOKE-1#1",
    "analysis_status": "ANALYZED",
    "correct": True,
    "final_answer_present": True,
    "review_reasons": [],
    "observations": [],
}
SKILLS_OK = {
    "student_id": "SMOKE-1",
    "engine": "rule",
    "skills": [
        {"skill_id": "linear_equation", "state": "learning", "mastery": 0.6}
    ],
    "count": 1,
}
HISTORY_OK = {"total": 1, "items": [{"attempt_id": "SMOKE-1"}]}
UNAUTHORIZED = {"detail": {"code": "E_AUTH_INVALID_KEY", "message": "no key"}}


class Server:
    """경로·헤더에 따라 응답을 만드는 fake 서버."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.health_status = 200
        self.docs_status = 404
        self.analyze_body = dict(ANALYZE_OK)
        self.skills_requires_key = True
        self.timeout_paths: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/health"):
            if path in self.timeout_paths:
                raise httpx.ReadTimeout("simulated timeout")
            return httpx.Response(self.health_status, json={"status": "ok"})
        if path.endswith("/docs"):
            return httpx.Response(self.docs_status)
        has_key = request.headers.get(HEADER_NAME) == KEY
        if path.endswith("/analyze"):
            return httpx.Response(200, json=self.analyze_body) if has_key else httpx.Response(401, json=UNAUTHORIZED)
        if path.endswith("/skills"):
            if not has_key and self.skills_requires_key:
                return httpx.Response(401, json=UNAUTHORIZED)
            return httpx.Response(200, json=SKILLS_OK)
        if path.endswith("/history"):
            return httpx.Response(200, json=HISTORY_OK) if has_key else httpx.Response(401, json=UNAUTHORIZED)
        return httpx.Response(404)


def _run(server: Server, argv=None) -> int:
    args = argv or ["--base-url", BASE]
    return main(args, transport=httpx.MockTransport(server.handler))


@pytest.fixture
def server() -> Server:
    return Server()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(ENV_KEY, KEY)


# ===========================================================================
# 정상 / 실패
# ===========================================================================

def test_all_checks_pass(server, capsys):
    assert _run(server) == 0
    out = capsys.readouterr().out
    assert "PASS 7 / FAIL 0" in out
    for name in ("health", "history (무키)", "analyze (키)", "skills (키)", "docs 비노출"):
        assert name in out


def test_health_500_fails(server, capsys):
    server.health_status = 500
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "health (무키): FAIL" in out
    assert "PASS 6 / FAIL 1" in out


def test_analyze_not_analyzed_fails(server, capsys):
    server.analyze_body = dict(ANALYZE_OK, analysis_status="REVIEW_REQUIRED")
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "analyze (키): FAIL" in out


def test_missing_final_answer_present_fails(server, capsys):
    body = dict(ANALYZE_OK)
    body.pop("final_answer_present")
    server.analyze_body = body
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "analyze (키): FAIL" in out
    assert "누락 필드 final_answer_present" in out


def test_unprotected_skills_is_failure(server, capsys):
    server.skills_requires_key = False
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "skills (무키): FAIL" in out


def test_docs_200_is_failure(server, capsys):
    server.docs_status = 200
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "docs 비노출: FAIL" in out


def test_timeout_is_recorded_and_checks_continue(server, capsys):
    server.timeout_paths = {"/health"}
    assert _run(server) == 1
    out = capsys.readouterr().out
    assert "health (무키): FAIL (ReadTimeout)" in out
    # 이후 검사는 계속 실행되어 6개가 PASS 다
    assert "PASS 6 / FAIL 1" in out


# ===========================================================================
# 키·URL 비노출 / 종료 코드
# ===========================================================================

def test_key_never_printed(server, capsys):
    assert _run(server) == 0
    captured = capsys.readouterr()
    assert KEY not in captured.out
    assert KEY not in captured.err
    # 헤더 이름은 상태 설명에 쓰이지 않는다
    assert HEADER_NAME not in captured.out


def test_missing_key_exits_2_without_requests(server, monkeypatch, capsys):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert _run(server) == 2
    assert server.calls == []
    captured = capsys.readouterr()
    assert "AI1_SMOKE_KEY" in captured.err


def test_only_host_is_printed(server, capsys):
    code = _run(
        server,
        ["--base-url", "https://smoke.example.com/private/path?token=abc"],
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "smoke.example.com" in out
    assert "https://" not in out
    assert "/private" not in out
    assert "token=" not in out


# ===========================================================================
# run_smoke 단위 (구조·상세 형식)
# ===========================================================================

def test_run_smoke_returns_seven_results_with_state_details(server):
    with httpx.Client(
        base_url=BASE, transport=httpx.MockTransport(server.handler)
    ) as client:
        results = run_smoke(
            client, student_id="SMOKE-T1", attempt_id="SMOKE-T1", key=KEY
        )
    assert len(results) == 7
    assert all(result.ok for result in results)
    assert [result.name for result in results] == [
        "health (무키)",
        "history (무키)",
        "analyze (키)",
        "skills (키)",
        "history (키)",
        "skills (무키)",
        "docs 비노출",
    ]
    # detail 에는 상태 코드/필드명만 있고 키가 없다
    joined = " ".join(result.detail for result in results)
    assert KEY not in joined
    assert "200" in joined and "404" in joined
