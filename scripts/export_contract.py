"""AI② 전달 패키지를 **실제 앱**에서 재현 가능하게 생성한다 (수동 편집 금지).

사용법::

    .venv\\Scripts\\python scripts\\export_contract.py
    .venv\\Scripts\\python scripts\\export_contract.py --out-dir docs/AI2_전달_2026-11-23

기본 출력 디렉터리는 ``docs/AI2_전달_<오늘 날짜>/`` 이다. 생성 파일:

1. ``openapi.json`` — FastAPI 전체 OpenAPI 스키마
2. ``examples.json`` — 대표 시나리오 4종의 요청/응답(전체 필드)
3. ``판정한계_목록.md`` — ``docs/AI1_판정한계_목록.md`` 사본
4. ``README.md`` — 계약 요약·예시 목록·응답 필드·확인 방법

제약:
- 네트워크 호출 없음, 실제 Google Vision 호출 없음.
- 인증 guard 는 **스크립트 실행 중에만** dependency override 로 우회하고
  종료 시 원래 상태로 되돌린다(``finally``).
- 환경변수 값/키/토큰을 읽거나 출력하지 않는다.
- 기대 상태와 응답이 다르면 exit code 1 로 실패한다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.integration.internal_auth import require_internal_auth  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline.analyzer import reset_knowledge  # noqa: E402
from app.pipeline.repository import reset_repository  # noqa: E402
from app.pipeline.verifier import (  # noqa: E402
    REVIEW_EXPRESSION_EQ_LINE,
    REVIEW_EXPRESSION_PROBLEM_NOT_EXPR,
    REVIEW_FUNCTION_NOTATION,
    REVIEW_SOLUTION_BLOCK,
)
from app.schemas import AnalyzeAttemptResponse  # noqa: E402

LIMIT_LIST_PATH = ROOT / "docs" / "AI1_판정한계_목록.md"
ANALYZE_URL = "/api/v1/attempts/{attempt_id}/analyze"

# 계약에 노출되는 판정 보류 사유 코드 4종 (순서 고정).
REVIEW_REASON_CODES = (
    REVIEW_SOLUTION_BLOCK,
    REVIEW_FUNCTION_NOTATION,
    REVIEW_EXPRESSION_EQ_LINE,
    REVIEW_EXPRESSION_PROBLEM_NOT_EXPR,
)

ANALYSIS_STATUSES = ("ANALYZED", "REVIEW_REQUIRED", "UNKNOWN")

SCENARIOS = (
    {
        "name": "complete",
        "attempt_id": "AI2-COMPLETE",
        "request": {
            "problem_id": "P-COMPLETE",
            "problem_latex": "2x=6",
            "problem_skills": ["linear_equation"],
            "student_id": "S-AI2",
            "solution_text": "2x=6\nx=3",
        },
        "expect": {
            "analysis_status": "ANALYZED",
            "correct": True,
            "final_answer_present": True,
            "review_reasons": [],
        },
    },
    {
        "name": "partial",
        "attempt_id": "AI2-PARTIAL",
        "request": {
            "problem_id": "P-PARTIAL",
            "problem_latex": "x^2=9",
            "problem_skills": ["quadratic_equation"],
            "student_id": "S-AI2",
            "solution_text": "x^2=9\nx=3",
        },
        "expect": {
            "analysis_status": "ANALYZED",
            "final_answer_present": False,
            "review_reasons": [],
        },
    },
    {
        "name": "wrong_final",
        "attempt_id": "AI2-WRONG-FINAL",
        "request": {
            "problem_id": "P-WRONG-FINAL",
            "problem_latex": "2x=6",
            "problem_skills": ["linear_equation"],
            "student_id": "S-AI2",
            "solution_text": "2x=6\nx=999",
        },
        "expect": {
            "analysis_status": "ANALYZED",
            "correct": False,
            "final_answer_present": True,
            "review_reasons": [],
        },
    },
    {
        "name": "solution_block",
        "attempt_id": "AI2-SOLUTION-BLOCK",
        "request": {
            "problem_id": "P-SOLUTION-BLOCK",
            "problem_latex": "x^2=9",
            "problem_skills": ["quadratic_equation"],
            "student_id": "S-AI2",
            "solution_text": "x^2=9\nx=3\nx=-3",
        },
        "expect": {
            "analysis_status": "REVIEW_REQUIRED",
            "correct": None,
            "final_answer_present": None,
            "review_reasons": [REVIEW_SOLUTION_BLOCK],
        },
    },
)


class ContractExportError(RuntimeError):
    """기대 상태와 실제 응답이 다를 때 (스크립트 exit code 1)."""


def _skip_auth() -> None:
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_examples() -> list[dict]:
    """시나리오 4종을 TestClient 로 실행하고 기대 상태를 검증한다."""
    results: list[dict] = []
    previous = app.dependency_overrides.get(require_internal_auth)
    app.dependency_overrides[require_internal_auth] = _skip_auth
    try:
        client = TestClient(app)
        for scenario in SCENARIOS:
            reset_knowledge()
            reset_repository()
            response = client.post(
                ANALYZE_URL.format(attempt_id=scenario["attempt_id"]),
                json=scenario["request"],
            )
            if response.status_code != 200:
                raise ContractExportError(
                    "{}: HTTP {} (기대 200)".format(scenario["name"], response.status_code)
                )
            body = response.json()
            for key, expected in scenario["expect"].items():
                actual = body.get(key)
                if actual != expected:
                    raise ContractExportError(
                        "{}: {} 기대값 {!r}, 실제 {!r}".format(
                            scenario["name"], key, expected, actual
                        )
                    )
            results.append(
                {
                    "name": scenario["name"],
                    "attempt_id": scenario["attempt_id"],
                    "request": scenario["request"],
                    "response": body,
                }
            )
    finally:
        # 스크립트가 바꾼 override 만 되돌린다 (기존 override 가 있으면 복원).
        if previous is None:
            app.dependency_overrides.pop(require_internal_auth, None)
        else:
            app.dependency_overrides[require_internal_auth] = previous
    return results


def _example_row(example: dict) -> str:
    body = example["response"]
    reasons = body.get("review_reasons") or []
    return "| `{}` | `{}` | `{}` | `{}` | `{}` |".format(
        example["name"],
        body.get("analysis_status"),
        body.get("correct"),
        body.get("final_answer_present"),
        json.dumps(reasons, ensure_ascii=False),
    )


def _render_readme(generated_at: str, examples: list[dict], path_count: int) -> str:
    rows = "\n".join(_example_row(example) for example in examples)
    fields = ", ".join(AnalyzeAttemptResponse.model_fields)
    return """# AI① → AI② 전달 패키지 (자동 생성)

- 생성 시각: `{generated_at}`
- 생성 명령: `.venv\\Scripts\\python scripts\\export_contract.py --out-dir <이 폴더>`
- **수동 편집 금지** — 이 폴더의 파일은 실제 앱에서 스크립트로 생성됩니다.

## 1. 파일

| 파일 | 내용 |
|---|---|
| `openapi.json` | FastAPI 전체 OpenAPI 스키마 (경로 {path_count}개) |
| `examples.json` | 대표 시나리오 4종의 요청/응답 (전체 필드, `analysis_id` 포함) |
| `판정한계_목록.md` | `review_reasons` 코드 정의 사본 |

## 2. 대표 예시

| 이름 | analysis_status | correct | final_answer_present | review_reasons |
|---|---|---|---|---|
{rows}

## 3. 응답 필드 (`AnalyzeAttemptResponse`)

{fields}

핵심 판단 필드:
- `analysis_status`: ANALYZED / REVIEW_REQUIRED / UNKNOWN
- `correct`: 오류 미검출 여부 (ANALYZED에서만 true/false, 보류는 null)
- `final_answer_present`: 최종 답 제시 여부 (null=판단 불가, `correct`와 독립)
- `review_reasons`: 판정 보류 사유 코드 (정상이면 `[]`, 코드별 정의는 `판정한계_목록.md`)

## 4. 확인 방법

1. `examples.json`의 4종 `response`가 위 표와 같은지 확인한다.
2. `openapi.json`의 `components.schemas.AnalyzeAttemptResponse`에
   `final_answer_present`·`review_reasons`가 있는지 확인한다.
3. 응답 필드가 빠진 구버전은 `review_reasons=[]`, `final_answer_present=null`로 해석한다.
4. ANALYZED + 비어 있지 않은 `review_reasons`는 계약 위반으로 보고 보류한다.
""".format(
        generated_at=generated_at,
        path_count=path_count,
        rows=rows,
        fields=fields,
    )


def export_package(out_dir: Path) -> dict:
    """전달 패키지 4파일을 생성하고 요약을 반환한다 (덮어쓰기는 허용)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_at = _utc_now()
    examples = run_examples()
    openapi = app.openapi()

    files: dict[str, int] = {}

    openapi_path = out_dir / "openapi.json"
    openapi_path.write_text(
        json.dumps(openapi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files[openapi_path.name] = openapi_path.stat().st_size

    payload = {
        "generated_at": generated_at,
        "contract": {
            "analysis_status": list(ANALYSIS_STATUSES),
            "review_reasons": list(REVIEW_REASON_CODES),
        },
        "examples": examples,
    }
    examples_path = out_dir / "examples.json"
    examples_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files[examples_path.name] = examples_path.stat().st_size

    limit_path = out_dir / "판정한계_목록.md"
    shutil.copyfile(LIMIT_LIST_PATH, limit_path)
    files[limit_path.name] = limit_path.stat().st_size

    readme_path = out_dir / "README.md"
    readme_path.write_text(
        _render_readme(generated_at, examples, len(openapi.get("paths", {}))),
        encoding="utf-8",
    )
    files[readme_path.name] = readme_path.stat().st_size

    return {
        "out_dir": str(out_dir),
        "generated_at": generated_at,
        "files": files,
        "examples": examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI② 전달 패키지(OpenAPI·예시·판정한계·README) 생성"
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="출력 디렉터리 (기본: docs/AI2_전달_<YYYY-MM-DD>)",
    )
    args = parser.parse_args(argv)

    if args.out_dir is None:
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = ROOT / "docs" / f"AI2_전달_{today}"
    else:
        out_dir = Path(args.out_dir)

    existed = out_dir.exists() and any(out_dir.iterdir())
    try:
        result = export_package(out_dir)
    except ContractExportError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if existed:
        print(f"[warn] 기존 파일을 덮어썼습니다: {out_dir}")

    print(f"[ok] 출력: {result['out_dir']}")
    for name, size in result["files"].items():
        print(f"  - {name} ({size} bytes)")
    for example in result["examples"]:
        body = example["response"]
        print(
            "  · {name}: status={status} correct={correct} fap={fap} reasons={reasons}".format(
                name=example["name"],
                status=body.get("analysis_status"),
                correct=body.get("correct"),
                fap=body.get("final_answer_present"),
                reasons=json.dumps(body.get("review_reasons"), ensure_ascii=False),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
