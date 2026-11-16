"""A6 P1 표현식 모드 golden 회귀 (tests/golden/expression_cases.json).

케이스별 기대값(correct/analysis_status/error_step/error_type/error_subtype)을
그대로 고정한다. 기대값을 바꾸려면 계약 변경 근거가 먼저 있어야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline.analyzer import AnalysisError, analyze, reset_knowledge

_GOLDEN = Path(__file__).parent / "golden" / "expression_cases.json"
_DATA = json.loads(_GOLDEN.read_text(encoding="utf-8"))
_CASES = _DATA["cases"]
_REJECTED = _DATA["rejected_cases"]


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_expression_golden(case):
    result = analyze(
        problem_latex=case["problem_latex"],
        solution_text=case["solution_text"],
        student_id=f"golden-{case['id']}",
        problem_skill=_DATA["problem_skill"],
        problem_type=case["problem_type"],
        target_form=case["target_form"],
    )
    for key, expected in case["expected"].items():
        assert result[key] == expected, f"{case['id']}: {key}"


def test_golden_covers_review_and_form_violation():
    """회귀 셋이 판정 보류와 형태 위반을 모두 포함하는지 (셋이 비는 것 방지)."""
    statuses = {c["expected"]["analysis_status"] for c in _CASES}
    error_types = {c["expected"]["error_type"] for c in _CASES}
    assert {"ANALYZED", "REVIEW_REQUIRED"} <= statuses
    assert {"calculation", "procedure"} <= error_types


@pytest.mark.parametrize("case", _REJECTED, ids=[c["id"] for c in _REJECTED])
def test_expression_golden_rejected(case):
    """부등식·계약 오류는 분석 결과가 아니라 코드로 거절된다."""
    with pytest.raises(AnalysisError) as exc:
        analyze(
            problem_latex=case["problem_latex"],
            solution_text=case["solution_text"],
            student_id=f"golden-{case['id']}",
            problem_skill=_DATA["problem_skill"],
            problem_type=case["problem_type"],
            target_form=case["target_form"],
        )
    assert exc.value.code == case["expected_code"], case["id"]


def test_golden_rejected_covers_scope_and_contract_errors():
    codes = {c["expected_code"] for c in _REJECTED}
    assert "E_UNSUPPORTED_INEQUALITY" in codes
    assert "E_UNSUPPORTED_FUNCTION" in codes
    assert "E_INVALID_TARGET_FORM" in codes
    assert "E_INVALID_PROBLEM_TYPE" in codes
