"""PHASE 9-5 — transposition( 이항) 오류 세분화.

판정 조건 자체는 그대로 두고 최종 분류에서 두 원인을 구분한다.

  d_lhs + d_rhs == 0                      → 부호 미변경      → 3.1
  한쪽에서 뺀 항을 반대쪽에 반영하지 않음  → 양변 조작 비대칭 → 3.2

3.1 과 3.2 의 taxonomy typical_error_subtype 은 둘 다 sign_not_flipped 이므로
새 ID 나 새 subtype 을 만들지 않는다. Backend MIS-2-001/MIS-2-002 는
외부 응답에 연결하지 않는다(공식 seed 확정 전).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.classifier import (
    MISCONCEPTION_ASYMMETRY,
    MISCONCEPTION_TRANSPOSITION,
    _SKILL_BY_KIND,
    _TAXONOMY_BINDING,
    _is_transposition_error,
    _transposition_cause,
    classify,
)
from app.pipeline.parser import parse_line
from app.pipeline.repository import reset_repository
from app.taxonomy import misconception_by_id

client = TestClient(app)


# ===========================================================================
# 1. 부호 미변경 → 3.1
# ===========================================================================


def test_sign_not_flipped_is_3_1():
    """한쪽에서 뺀 만큼 반대쪽에 같은 부호로 더함 → 부호 미변경."""
    info = classify("3x+5=20", ["3x+5=20", "3x=25"], 1)
    assert info.error_type == "concept_error"
    assert info.error_subtype == "sign_not_flipped"
    assert info.misconception_id == "3.1"
    assert info.misconception_ids == ("3.1",)


def test_sign_not_flipped_binding():
    assert _TAXONOMY_BINDING["transposition_sign"] == (
        "concept_error",
        "sign_not_flipped",
        "3.1",
    )
    assert _SKILL_BY_KIND["transposition_sign"] == "transposition"


def test_sign_not_flipped_cause():
    prev = parse_line("3x+5=20")
    cur = parse_line("3x=25")
    assert _transposition_cause(prev, cur) == "transposition_sign"


def test_sign_not_flipped_variants():
    for problem, steps, index in [
        ("3x+5=20", ["3x+5=20", "3x=25"], 1),   # 5 를 뺀 만큼 더함(부호 미반전)
        ("2x+6=14", ["2x+6=14", "2x=20"], 1),   # 6 을 뺀 만큼 더함
        ("5x-3=12", ["5x-3=12", "5x=9"], 1),    # -3 을 뺀 만큼 더함
    ]:
        info = classify(problem, steps, index)
        assert info.misconception_id == "3.1", (problem, steps)


def test_legacy_transposition_alias_still_maps_to_3_1():
    """하위호환: 기존 'transposition' kind 는 3.1 과 동일하다."""
    assert _TAXONOMY_BINDING["transposition"] == (
        "concept_error",
        "sign_not_flipped",
        "3.1",
    )
    assert _SKILL_BY_KIND["transposition"] == _SKILL_BY_KIND["transposition_sign"]


# ===========================================================================
# 2. 항 누락 / 비대칭 조작 → 3.2
# ===========================================================================


def test_term_not_carried_over_is_3_2():
    """한쪽에서 항을 제거했는데 반대쪽에 반영하지 않음 → 양변 조작 비대칭."""
    info = classify("2x+6=14", ["2x+6=14", "2x=14"], 1)
    assert info.error_type == "concept_error"
    assert info.error_subtype == "sign_not_flipped"
    assert info.misconception_id == "3.2"
    assert info.misconception_ids == ("3.2",)


def test_asymmetry_binding():
    assert _TAXONOMY_BINDING["transposition_asymmetry"] == (
        "concept_error",
        "sign_not_flipped",
        MISCONCEPTION_ASYMMETRY,
    )
    assert _SKILL_BY_KIND["transposition_asymmetry"] == "transposition"


def test_asymmetry_skill_ids_come_from_taxonomy():
    info = classify("2x+6=14", ["2x+6=14", "2x=14"], 1)
    assert info.skill_ids == tuple(misconception_by_id("3.2").related_skill_ids)
    assert "transposition" in info.skill_ids


def test_asymmetry_cause():
    prev = parse_line("2x+6=14")
    cur = parse_line("2x=14")
    assert _transposition_cause(prev, cur) == "transposition_asymmetry"


def test_3_1_and_3_2_are_distinct():
    assert MISCONCEPTION_TRANSPOSITION != MISCONCEPTION_ASYMMETRY
    assert MISCONCEPTION_TRANSPOSITION == "3.1"
    assert MISCONCEPTION_ASYMMETRY == "3.2"


def test_3_2_exists_in_taxonomy_without_new_id():
    """3.2 는 이미 등록되어 있고 새 ID 를 만들지 않았다."""
    entry = misconception_by_id("3.2")
    assert entry.misconception_id == "3.2"
    assert entry.typical_error_subtypes == ["sign_not_flipped"]


# ===========================================================================
# 3. 오분류 방지 — 정상 이항 / 계산 실수
# ===========================================================================


def test_correct_transposition_is_not_an_error():
    for problem, steps, index in [
        ("3x+5=20", ["3x+5=20", "3x=15"], 1),
        ("2x+6=14", ["2x+6=14", "2x=8"], 1),
    ]:
        info = classify(problem, steps, index)
        assert info.error_type == "calculation", (problem, steps)
        assert info.misconception_id is None, (problem, steps)


def test_calculation_still_has_no_misconception():
    info = classify("5x=15", ["5x=15", "x=4"], 1)
    assert info.error_type == "calculation"
    assert info.error_subtype == "arithmetic_slip"
    assert info.misconception_id is None
    assert info.misconception_ids == ()


def test_one_side_operation_is_still_calculation():
    """한쪽에만 연산 적용은 항 누락이 아니다(기존 정책 유지)."""
    info = classify("0.5x=5", ["0.5x=5", "x=5"], 1)
    assert info.error_type == "calculation"
    assert info.misconception_id is None


def test_no_transposition_cause_returns_none():
    assert _transposition_cause(parse_line("5x=15"), parse_line("x=4")) is None
    assert _transposition_cause(None, None) is None


def test_predicate_behaviour_unchanged():
    """_is_transposition_error 판정 결과는 세분화 이전과 동일해야 한다."""
    cases = [
        ("3x+5=20", "3x=25", True),      # 부호 미변경
        ("2x+6=14", "2x=14", True),      # 항 누락
        ("2x+6=14", "2x=8", False),      # 정상
        ("5x=15", "x=4", False),         # 계산 실수
        ("0.5x=5", "x=5", False),        # 한쪽 연산
    ]
    for prev_raw, cur_raw, expected in cases:
        prev = parse_line(prev_raw)
        cur = parse_line(cur_raw)
        assert _is_transposition_error(prev, cur) is expected, (prev_raw, cur_raw)


def test_distribution_still_wins_over_transposition():
    """분배 오류 판정이 이항보다 우선이다(기존 우선순위 유지)."""
    info = classify("2(x-3)=6", ["2(x-3)=6", "2x-3=6"], 1)
    assert info.misconception_id == "2.1"


def test_comprehension_unchanged():
    info = classify("2x+5=13", ["2x+5=15", "2x=10", "x=5"], 0)
    assert info.error_type == "comprehension"
    assert info.misconception_id is None


# ===========================================================================
# 4. 다중 오류 · 연쇄 오류
# ===========================================================================


def test_multi_error_with_asymmetry():
    """연쇄 오류에서 첫 오류가 비대칭이면 3.2 로 분류된다."""
    analyzer.reset_knowledge()
    reset_repository()
    try:
        r = client.post(
            "/api/v1/attempts/M1/analyze",
            json={
                "problem_id": "P1",
                "problem_latex": "2x+6=14",
                "problem_skills": ["linear_equation", "transposition"],
                "student_id": "S1",
                "solution_text": "2x+6=14\n2x=14\nx=7",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["analysis_status"] == "ANALYZED"
        assert body["misconception_id"] == "3.2"
        assert set(body["misconception_tags"]) == {"3.2"}
        assert body["error_type"] == "concept_error"
    finally:
        analyzer.reset_knowledge()
        reset_repository()


def test_multi_error_with_sign_not_flipped():
    analyzer.reset_knowledge()
    reset_repository()
    try:
        r = client.post(
            "/api/v1/attempts/M2/analyze",
            json={
                "problem_id": "P2",
                "problem_latex": "3x+5=20",
                "problem_skills": ["linear_equation", "transposition"],
                "student_id": "S1",
                "solution_text": "3x+5=20\n3x=25\nx=8.33",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["misconception_id"] == "3.1"
        assert set(body["misconception_tags"]) == {"3.1"}
    finally:
        analyzer.reset_knowledge()
        reset_repository()


def test_errors_list_keeps_suppressed_downstream():
    analyzer.reset_knowledge()
    reset_repository()
    try:
        r = client.post(
            "/api/v1/attempts/M3/analyze",
            json={
                "problem_id": "P3",
                "problem_latex": "2(x-3)=6",
                "problem_skills": ["linear_equation", "distribution"],
                "student_id": "S1",
                "solution_text": "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["misconception_id"] == "2.1"
        assert body["error_type"] == "concept_error"
        assert "errors" in body and "observations" in body
    finally:
        analyzer.reset_knowledge()
        reset_repository()


# ===========================================================================
# 5. API 회귀
# ===========================================================================


def test_analyze_solution_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "3x+5=20", "solution_text": "3x+5=20\n3x=25"},
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert body["misconception_id"] == "3.1"
    assert body["error_type"] == "concept_error"
    assert "concept_code" not in body
    assert "MIS-" not in str(body)


def test_analyze_solution_asymmetry_returns_3_2():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2x+6=14", "solution_text": "2x+6=14\n2x=14"},
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert body["misconception_id"] == "3.2"
    assert body["skill"] == "transposition"


def test_backend_crosswalk_not_connected_to_new_misconception():
    """3.2 는 Backend 코드에 연결하지 않는다."""
    from app.integration import backend_crosswalk as cw

    assert cw.misconception_code("3.2") is None
    assert cw.misconception_code("3.1") is None
    assert set(cw.APPROVED_MISCONCEPTION_CODES.values()) == {"MIS-1-004"}


def test_taxonomy_files_untouched():
    import json
    from pathlib import Path

    from app.taxonomy import validate_all

    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "data" / "misconceptions.json").read_text(encoding="utf-8"))
    ids = [m["misconception_id"] for m in data["misconceptions"]]
    assert len(ids) == 19
    assert "3.1" in ids and "3.2" in ids
    assert validate_all() == []


def test_taxonomy_binding_table_shape():
    assert set(_TAXONOMY_BINDING) == {
        "distribution",
        "transposition",
        "transposition_sign",
        "transposition_asymmetry",
        "comprehension",
        "calculation",
        # A6 P1 — 표현식 모드의 형태(target_form) 위반.
        "wrong_form",
    }
    for kind, (_, _, misconception_id) in _TAXONOMY_BINDING.items():
        if kind in ("comprehension", "calculation", "wrong_form"):
            assert misconception_id is None, kind