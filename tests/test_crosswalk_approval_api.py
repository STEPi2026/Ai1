"""2026-11-23 Backend 승인 crosswalk 6건의 실 API 경로 검증.

- SKL-1-002/003/005/023/025/026 : 200으로 분석되고 대표 코드가 출력된다
- SKL-1-007/008 : AI① 대응 Skill 이 없어 422 E_UNKNOWN_SKILL_CODE 유지
- N:1 입력(003, 026)은 대표 코드(002, 025)로 출력된다
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import reset_knowledge
from app.pipeline.repository import reset_repository

client = TestClient(app)
URL = "/api/v1/attempts/{}/analyze"

EXPANSION = {
    "problem_latex": "(x+2)(x-3)",
    "solution_text": "(x+2)(x-3)\nx^2-x-6",
    "target_form": "expanded",
}
FACTORING = {
    "problem_latex": "x^2-x-6",
    "solution_text": "x^2-x-6\n(x-3)(x+2)",
    "target_form": "factored",
}

CASES = [
    ("C-002", "SKL-1-002", EXPANSION, "polynomial_addition", ["SKL-1-002"]),
    ("C-003", "SKL-1-003", EXPANSION, "polynomial_addition", ["SKL-1-002"]),
    ("C-005", "SKL-1-005", EXPANSION, "polynomial_multiplication", ["SKL-1-005"]),
    ("C-023", "SKL-1-023", FACTORING, "factoring_common", ["SKL-1-023"]),
    ("C-025", "SKL-1-025", FACTORING, "factoring_formula", ["SKL-1-025"]),
    ("C-026", "SKL-1-026", FACTORING, "factoring_formula", ["SKL-1-025"]),
]


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    reset_repository()
    yield
    reset_knowledge()
    reset_repository()


@pytest.mark.parametrize("attempt_id,code,problem,skill,official", CASES)
def test_approved_codes_are_accepted(attempt_id, code, problem, skill, official):
    r = client.post(
        URL.format(attempt_id),
        json={
            "problem_id": "P-" + attempt_id,
            "problem_skills": [code],
            "student_id": "S1",
            "problem_type": "expression",
            **problem,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis_status"] == "ANALYZED"
    assert body["skill"] == skill
    # N:1 공유 Skill 은 대표 코드 1개로 출력한다.
    assert body["official_skill_codes"] == official
    assert body["provisional_skill_codes"] == []


@pytest.mark.parametrize("code", ["SKL-1-007", "SKL-1-008"])
def test_unmapped_codes_remain_422(code):
    r = client.post(
        URL.format("C-" + code),
        json={
            "problem_id": "P-" + code,
            "problem_skills": [code],
            "student_id": "S1",
            "problem_type": "expression",
            **EXPANSION,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL_CODE"


def test_shared_codes_resolve_to_same_skill():
    r = client.post(
        URL.format("C-SHARED"),
        json={
            "problem_id": "P-C-SHARED",
            "problem_skills": ["SKL-1-002", "SKL-1-003"],
            "student_id": "S1",
            "problem_type": "expression",
            **EXPANSION,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skill"] == "polynomial_addition"
    assert body["official_skill_codes"] == ["SKL-1-002"]
