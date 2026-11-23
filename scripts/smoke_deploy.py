"""배포된 AI① 서버의 계약 스모크 검사 (성공/실패 표만 출력).

사용법::

    $env:AI1_SMOKE_KEY = "<배포된 서버의 내부 키>"
    .venv\\Scripts\\python scripts\\smoke_deploy.py --base-url https://<host>

- 키는 ``AI1_SMOKE_KEY`` 환경변수에서만 읽는다 (CLI 인자로 받지 않는다).
- 출력에는 **host 만** 표시한다 — scheme/query/키/전체 URL 은 출력하지 않는다.
- 키가 없으면 검사 전에 중단하고 exit 2.
- 실패가 하나라도 있으면 exit 1, 모두 통과하면 exit 0.

검사 항목(7):
1. ``/health`` (무키) → 200 + ``status``
2. ``/students/{id}/history`` (무키) → 401 ``E_AUTH_INVALID_KEY``
   (키 미설정 서버의 503 ``E_AUTH_NOT_CONFIGURED`` 도 허용하되 표에 그대로 표시)
3. ``/attempts/{id}/analyze`` (키) → 200 ANALYZED, correct=true,
   final_answer_present=true, review_reasons=[]
4. ``/students/{id}/skills`` (키) → 200, count≥1, ``linear_equation`` 포함, engine=rule
5. ``/students/{id}/history`` (키) → 200, total≥1, items 비어 있지 않음
6. ``/students/{id}/skills`` (무키) → 401
7. ``/docs`` (무키) → 404 (운영 기본 비노출)

실제 서버는 테스트에서 호출하지 않는다 — ``httpx.MockTransport`` 로 검증한다.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

ENV_KEY = "AI1_SMOKE_KEY"
HEADER_NAME = "X-Internal-Api-Key"
DEFAULT_TIMEOUT = 10.0

E_AUTH_INVALID_KEY = "E_AUTH_INVALID_KEY"
E_AUTH_NOT_CONFIGURED = "E_AUTH_NOT_CONFIGURED"


@dataclass(frozen=True)
class CheckResult:
    """검사 1건. ``detail`` 에는 상태 코드/필드명만 넣는다 (URL·키 금지)."""

    name: str
    ok: bool
    detail: str


def _run_id(now: datetime | None = None) -> str:
    """실제 학생 데이터와 겹치지 않는 실행 식별자 (UTC)."""
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _detail_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return "본문 파싱 실패"
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            return detail["code"]
        if isinstance(detail, str):
            return "detail 문자열"
    return "code 없음"


def _safe_get(client: httpx.Client, path: str, **kwargs):
    """예외(타임아웃·연결 실패)를 돌려준다. 검사 루프를 멈추지 않는다."""
    try:
        return client.get(path, **kwargs)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        return exc


def _safe_post(client: httpx.Client, path: str, **kwargs):
    try:
        return client.post(path, **kwargs)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        return exc


def _failure(name: str, response) -> CheckResult:
    if isinstance(response, Exception):
        return CheckResult(name, False, f"{type(response).__name__}")
    return CheckResult(name, False, f"{response.status_code}, {_detail_code(response)}")


def _json_dict(response) -> dict:
    try:
        body = response.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def run_smoke(
    client: httpx.Client, *, student_id: str, attempt_id: str, key: str
) -> list[CheckResult]:
    """검사 7건을 실행한다. 키는 요청별 헤더로만 전달하고 결과에 남기지 않는다."""
    auth = {HEADER_NAME: key}
    results: list[CheckResult] = []

    # 1. health (무키)
    response = _safe_get(client, "/health")
    if isinstance(response, Exception):
        results.append(_failure("health (무키)", response))
    elif response.status_code != 200:
        results.append(_failure("health (무키)", response))
    else:
        exists = "status" in _json_dict(response)
        results.append(
            CheckResult("health (무키)", exists, f"200, status {'있음' if exists else '없음'}")
        )

    # 2. history (무키) → 401 (또는 fail-closed 503)
    response = _safe_get(client, f"/api/v1/students/{student_id}/history")
    if isinstance(response, Exception):
        results.append(_failure("history (무키)", response))
    else:
        code = _detail_code(response)
        ok = (response.status_code == 401 and code == E_AUTH_INVALID_KEY) or (
            response.status_code == 503 and code == E_AUTH_NOT_CONFIGURED
        )
        results.append(
            CheckResult("history (무키)", ok, f"{response.status_code}, {code}")
        )

    # 3. analyze (키)
    payload = {
        "problem_id": f"P-{attempt_id}",
        "problem_latex": "2x=6",
        "problem_skills": ["linear_equation"],
        "student_id": student_id,
        "solution_text": "2x=6\nx=3",
    }
    response = _safe_post(
        client, f"/api/v1/attempts/{attempt_id}/analyze", json=payload, headers=auth
    )
    if isinstance(response, Exception):
        results.append(_failure("analyze (키)", response))
    else:
        body = _json_dict(response)
        required = {
            "analysis_id",
            "analysis_status",
            "final_answer_present",
            "review_reasons",
            "observations",
        }
        missing = sorted(required - set(body))
        ok = (
            response.status_code == 200
            and not missing
            and body.get("analysis_status") == "ANALYZED"
            and body.get("correct") is True
            and body.get("final_answer_present") is True
            and body.get("review_reasons") == []
        )
        if missing:
            detail = f"{response.status_code}, 누락 필드 {','.join(missing)}"
        else:
            detail = (
                f"{response.status_code}, status={body.get('analysis_status')}"
                f", correct={body.get('correct')}"
                f", final_answer_present={body.get('final_answer_present')}"
            )
        results.append(CheckResult("analyze (키)", ok, detail))

    # 4. skills (키)
    response = _safe_get(
        client, f"/api/v1/students/{student_id}/skills", headers=auth
    )
    if isinstance(response, Exception):
        results.append(_failure("skills (키)", response))
    else:
        body = _json_dict(response)
        skill_ids = {
            item.get("skill_id")
            for item in body.get("skills", [])
            if isinstance(item, dict)
        }
        ok = (
            response.status_code == 200
            and isinstance(body.get("count"), int)
            and body.get("count", 0) >= 1
            and "linear_equation" in skill_ids
            and body.get("engine") == "rule"
        )
        results.append(
            CheckResult(
                "skills (키)",
                ok,
                f"{response.status_code}, count={body.get('count')}, engine={body.get('engine')}",
            )
        )

    # 5. history (키)
    response = _safe_get(
        client, f"/api/v1/students/{student_id}/history", headers=auth
    )
    if isinstance(response, Exception):
        results.append(_failure("history (키)", response))
    else:
        body = _json_dict(response)
        items = body.get("items")
        ok = (
            response.status_code == 200
            and isinstance(body.get("total"), int)
            and body.get("total", 0) >= 1
            and isinstance(items, list)
            and len(items) >= 1
        )
        results.append(
            CheckResult(
                "history (키)",
                ok,
                f"{response.status_code}, total={body.get('total')}, items={len(items) if isinstance(items, list) else '없음'}",
            )
        )

    # 6. skills (무키) → 401
    response = _safe_get(client, f"/api/v1/students/{student_id}/skills")
    if isinstance(response, Exception):
        results.append(_failure("skills (무키)", response))
    else:
        code = _detail_code(response)
        ok = response.status_code == 401 and code == E_AUTH_INVALID_KEY
        results.append(CheckResult("skills (무키)", ok, f"{response.status_code}, {code}"))

    # 7. docs (무키) → 404
    response = _safe_get(client, "/docs")
    if isinstance(response, Exception):
        results.append(_failure("docs 비노출", response))
    else:
        ok = response.status_code == 404
        results.append(CheckResult("docs 비노출", ok, f"{response.status_code}"))

    return results


def _host_only(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.hostname or "(host 없음)"


def _print_results(host: str, run: str, results: list[CheckResult]) -> None:
    print(f"배포 스모크 — host: {host} (run: {run})")
    for index, result in enumerate(results, start=1):
        mark = "PASS" if result.ok else "FAIL"
        print(f"{index}. {result.name}: {mark} ({result.detail})")
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed
    print(f"요약: PASS {passed} / FAIL {failed}")
    if failed:
        print("실패 항목: " + ", ".join(r.name for r in results if not r.ok))


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(
        description="배포 AI① 서버 계약 스모크 (성공/실패 표만 출력)"
    )
    parser.add_argument("--base-url", required=True, help="배포 서버 URL")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="요청 타임아웃(초, 기본 10)"
    )
    args = parser.parse_args(argv)

    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        print(
            f"[FAIL] {ENV_KEY} 환경변수가 비어 있습니다. 검사를 시작하지 않습니다.",
            file=sys.stderr,
        )
        return 2

    parsed = urlparse(args.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        print("[FAIL] --base-url 은 http(s) URL 이어야 합니다.", file=sys.stderr)
        return 2
    # 서버 루트만 쓴다 — 경로/쿼리가 붙어 있으면 요청 URL 결합이 깨진다.
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{parsed.hostname}:{parsed.port}"
    base_url = f"{parsed.scheme}://{netloc}"

    run = _run_id()
    student_id = f"SMOKE-{run}"
    with httpx.Client(
        base_url=base_url,
        timeout=args.timeout,
        transport=transport,
    ) as client:
        results = run_smoke(
            client, student_id=student_id, attempt_id=student_id, key=key
        )

    _print_results(_host_only(args.base_url), run, results)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
