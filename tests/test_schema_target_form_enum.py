"""A6 P1 — target_form 의 JSON Schema enum 노출 계약.

스키마에는 문서용 enum 을 노출하되, 실제 값 검증은 파이프라인
(``validate_problem_mode``)이 커스텀 422 ``E_INVALID_TARGET_FORM`` 으로 수행한다.
이 둘이 어긋나면 소비자(Backend)가 스키마를 신뢰할 수 없으므로 고정한다.
"""
from __future__ import annotations

from app.pipeline.forms import ALLOWED_TARGET_FORMS
from app.schemas import (
    TARGET_FORM_VALUES,
    AnalyzeAttemptRequest,
    AnalyzeSolutionRequest,
)


def _target_form_schema(model) -> dict:
    return model.model_json_schema()["properties"]["target_form"]


def test_target_form_enum_is_exposed_in_string_branch():
    """enum 은 anyOf 의 string 분기에만 들어가고 null 허용은 유지된다."""
    for model in (AnalyzeSolutionRequest, AnalyzeAttemptRequest):
        schema = _target_form_schema(model)
        branches = schema["anyOf"]
        string_branch = next(b for b in branches if b.get("type") == "string")
        assert string_branch["enum"] == ["integer", "expanded", "factored"]
        assert string_branch["maxLength"] == 32
        assert {"type": "null"} in branches


def test_target_form_enum_matches_pipeline_allowed_values():
    """스키마 enum 과 파이프라인 허용값이 같은 집합이어야 한다."""
    assert set(TARGET_FORM_VALUES) == ALLOWED_TARGET_FORMS
    for model in (AnalyzeSolutionRequest, AnalyzeAttemptRequest):
        schema = _target_form_schema(model)
        string_branch = next(b for b in schema["anyOf"] if b.get("type") == "string")
        assert set(string_branch["enum"]) == ALLOWED_TARGET_FORMS


def test_enum_is_documentation_only():
    """Pydantic 은 임의 문자열을 통과시킨다 — 422 는 파이프라인이 만든다."""
    req = AnalyzeSolutionRequest(
        problem_latex="2x+3=7", solution_text="x=2", target_form="not-a-form"
    )
    assert req.target_form == "not-a-form"
