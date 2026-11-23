"""AI② 전달 패키지 export 테스트 (scripts/export_contract.py).

- tmp_path 에만 생성 (저장소 오염 없음)
- 실제 앱(TestClient)으로 예시를 재현하므로 수동 편집이 끼어들 수 없다
- 인증 override 는 스크립트 종료 시 원복되어야 한다
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from export_contract import (  # noqa: E402
    REVIEW_REASON_CODES,
    export_package,
)

from app.integration.internal_auth import require_internal_auth  # noqa: E402
from app.main import app  # noqa: E402

EXPECTED_PATHS = {
    "/health",
    "/analyze-solution",
    "/api/v1/attempts/{attempt_id}/analyze",
    "/api/v1/attempts/{attempt_id}/ocr",
    "/api/v1/attempts/{attempt_id}/analyses",
    "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
    "/api/v1/students/{student_id}/history",
    "/api/v1/students/{student_id}/skills",
    "/api/v1/students/{student_id}/misconceptions/top",
}

EXPECTED_EXAMPLES = {
    "complete": {
        "analysis_status": "ANALYZED",
        "correct": True,
        "final_answer_present": True,
        "review_reasons": [],
    },
    "partial": {
        "analysis_status": "ANALYZED",
        "final_answer_present": False,
        "review_reasons": [],
    },
    "wrong_final": {
        "analysis_status": "ANALYZED",
        "correct": False,
        "final_answer_present": True,
        "review_reasons": [],
    },
    "solution_block": {
        "analysis_status": "REVIEW_REQUIRED",
        "correct": None,
        "final_answer_present": None,
        "review_reasons": ["solution_block_notation"],
    },
}


def _export(tmp_path: Path, name: str = "pkg") -> Path:
    out_dir = tmp_path / name
    export_package(out_dir)
    return out_dir


def test_package_has_four_files_and_valid_json(tmp_path):
    out_dir = _export(tmp_path)
    names = {path.name for path in out_dir.iterdir()}
    assert names == {"openapi.json", "examples.json", "판정한계_목록.md", "README.md"}

    openapi = json.loads((out_dir / "openapi.json").read_text(encoding="utf-8"))
    examples = json.loads((out_dir / "examples.json").read_text(encoding="utf-8"))
    assert isinstance(openapi, dict)
    assert "paths" in openapi
    assert set(examples) == {"generated_at", "contract", "examples"}
    assert (out_dir / "판정한계_목록.md").read_text(encoding="utf-8")
    assert "AI① → AI② 전달 패키지" in (out_dir / "README.md").read_text(encoding="utf-8")


def test_openapi_contains_all_nine_paths(tmp_path):
    out_dir = _export(tmp_path)
    openapi = json.loads((out_dir / "openapi.json").read_text(encoding="utf-8"))
    assert set(openapi["paths"]) == EXPECTED_PATHS
    assert "/api/v1/students/{student_id}/skills" in openapi["paths"]


def test_openapi_response_has_final_answer_fields(tmp_path):
    out_dir = _export(tmp_path)
    openapi = json.loads((out_dir / "openapi.json").read_text(encoding="utf-8"))
    props = openapi["components"]["schemas"]["AnalyzeAttemptResponse"]["properties"]
    assert "final_answer_present" in props
    assert "review_reasons" in props


def test_review_reason_codes_are_fixed(tmp_path):
    out_dir = _export(tmp_path)
    examples = json.loads((out_dir / "examples.json").read_text(encoding="utf-8"))
    assert examples["contract"]["review_reasons"] == [
        "solution_block_notation",
        "function_notation_in_solution",
        "equation_line_in_expression_mode",
        "expression_problem_not_expression",
    ]
    assert list(REVIEW_REASON_CODES) == examples["contract"]["review_reasons"]
    assert examples["contract"]["analysis_status"] == [
        "ANALYZED",
        "REVIEW_REQUIRED",
        "UNKNOWN",
    ]


def test_examples_match_expected_table(tmp_path):
    out_dir = _export(tmp_path)
    payload = json.loads((out_dir / "examples.json").read_text(encoding="utf-8"))
    examples = {item["name"]: item for item in payload["examples"]}
    assert set(examples) == set(EXPECTED_EXAMPLES)
    for name, expected in EXPECTED_EXAMPLES.items():
        body = examples[name]["response"]
        for key, value in expected.items():
            assert body[key] == value, f"{name}.{key}"
        # 요청/응답 전체 필드가 담긴다 — analysis_id 마스킹 금지
        assert examples[name]["request"]["solution_text"]
        assert body["analysis_id"]
        assert "final_answer_present" in body


def test_auth_override_is_restored(tmp_path):
    before = app.dependency_overrides.get(require_internal_auth)
    _export(tmp_path)
    # 스크립트가 설치한 override 는 남지 않고, 실행 전 상태가 그대로 복원된다
    assert app.dependency_overrides.get(require_internal_auth) is before


def _without_timestamps(payload: dict) -> dict:
    """생성 시각(generated_at)과 응답 attempted_at 은 실행마다 달라 비교에서 제외한다."""
    normalized = json.loads(json.dumps(payload))
    for example in normalized.get("examples", []):
        example["response"].pop("attempted_at", None)
    normalized.pop("generated_at", None)
    return normalized


def test_examples_are_deterministic_except_timestamp(tmp_path):
    first = _export(tmp_path, "pkg1")
    second = _export(tmp_path, "pkg2")
    payload1 = json.loads((first / "examples.json").read_text(encoding="utf-8"))
    payload2 = json.loads((second / "examples.json").read_text(encoding="utf-8"))
    assert payload1["generated_at"].endswith("Z")
    assert payload2["generated_at"].endswith("Z")
    assert _without_timestamps(payload1) == _without_timestamps(payload2)
