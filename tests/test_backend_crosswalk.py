"""PHASE 9-4A — Backend 승인 코드 crosswalk.

검증 범위
  - 승인 매핑 2건이 정확히 성립한다
  - STP(concept) 코드는 AI① 이 반환하지 않는다
  - 미승인 내부 ID 는 전부 unmapped(None) 로 남는다
  - AI① taxonomy ID 와 legacy API 응답이 불변이다
  - frozen data/*.json 과 golden expected 가 불변이다
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.integration import backend_crosswalk as cw
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import reset_repository
from app.taxonomy import (
    ERROR_TYPE_CATALOG,
    MISCONCEPTION_CATALOG,
    all_skill_ids,
    load_skill_catalog,
    validate_all,
)

client = TestClient(app)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CROSSWALK_PATH = PROJECT_ROOT / "app" / "integration" / "backend_crosswalk.py"

# Backend 가 아직 승인하지 않은 것이 확인된 ID (구제02 결정)
UNAPPROVED_SKILL_IDS = [
    "transposition",
    "arithmetic",
    "linear_equation",
    "polynomial_multiplication",
    "comprehension",
    "set_operation",
    "function_concept",
]
UNAPPROVED_MISCONCEPTION_IDS = ["3.1", "1.1", "1.2", "2.2", "2.4", "3.2", "5.1"]


# ===========================================================================
# 1. distribution → SKL-1-004
# ===========================================================================

def test_distribution_maps_to_skl_1_004():
    assert cw.skill_code("distribution") == "SKL-1-004"


def test_skill_mapping_table_is_exactly_one_approved_entry():
    """Backend 승인분만 존재해야 한다. 임의 추가 금지."""
    assert cw.APPROVED_SKILL_CODES == {"distribution": "SKL-1-004"}


def test_skill_code_returns_none_for_none_and_empty():
    assert cw.skill_code(None) is None
    assert cw.skill_code("") is None


def test_is_approved_skill_code():
    assert cw.is_approved_skill_code("SKL-1-004") is True
    assert cw.is_approved_skill_code("SKL-1-002") is False
    assert cw.is_approved_skill_code(None) is False


# ===========================================================================
# 2. 2.1 → MIS-1-004
# ===========================================================================

def test_misconception_2_1_maps_to_mis_1_004():
    assert cw.misconception_code("2.1") == "MIS-1-004"


def test_misconception_mapping_table_is_exactly_one_approved_entry():
    assert cw.APPROVED_MISCONCEPTION_CODES == {"2.1": "MIS-1-004"}


def test_misconception_code_returns_none_for_none_and_empty():
    assert cw.misconception_code(None) is None
    assert cw.misconception_code("") is None


def test_is_approved_misconception_code():
    assert cw.is_approved_misconception_code("MIS-1-004") is True
    assert cw.is_approved_misconception_code("MIS-1-003") is False


# ===========================================================================
# 3. Backend 가 STP를 별도로 받지 않음
# ===========================================================================

def test_module_has_no_concept_function():
    """concept 변환 '함수'가 없어야 한다.

    'misconception_code' 안에 'concept'이 부분 문자열로 들어가므로
    부분일치로 판정하지 않고 공개 함수 목록을 통째로 고정한다.
    """
    import inspect

    functions = {
        name
        for name, obj in vars(cw).items()
        if not name.startswith("_") and inspect.isfunction(obj)
    }
    assert functions == {
        "skill_code",
        "misconception_code",
        "mapped_skill_codes",
        "mapped_misconception_codes",
        "is_approved_skill_code",
        "is_approved_misconception_code",
    }
    assert not any(name.endswith("concept_code") for name in functions)
    assert not any(name.startswith(("stp_", "concept_")) for name in functions)


def test_module_source_contains_no_stp_code():
    source = CROSSWALK_PATH.read_text(encoding="utf-8")
    assert "STP-" not in source


def test_no_callable_emits_stp():
    """어떤 공개 함수 호출 결과도 STP 로 시작하는 값을 만들지 않는다."""
    scalar_probes: list[object] = [None, "", "distribution", "2.1", "transposition"]
    for name in ("skill_code", "misconception_code"):
        for probe in scalar_probes:
            result = getattr(cw, name)(probe)
            assert not (isinstance(result, str) and result.startswith("STP-")), (name, probe)
    for name in ("mapped_skill_codes", "mapped_misconception_codes"):
        for probe in (None, [], ["distribution"], ["2.1"], ["transposition", "3.1"]):
            result = getattr(cw, name)(probe)
            assert not any(value.startswith("STP-") for value in result), (name, probe)
    for name in ("is_approved_skill_code", "is_approved_misconception_code"):
        for probe in scalar_probes + ["SKL-1-004", "MIS-1-004"]:
            assert getattr(cw, name)(probe) in (True, False), (name, probe)


def test_output_never_contains_concept_key():
    """crosswalk 결과 dict 에 concept 키가 생기지 않는다."""
    assert not hasattr(cw, "to_backend_target")
    assert set(cw.APPROVED_SKILL_CODES) == {"distribution"}
    assert all("concept" not in key.lower() for key in cw.APPROVED_SKILL_CODES)
    assert all("concept" not in key.lower() for key in cw.APPROVED_MISCONCEPTION_CODES)


# ===========================================================================
# 4. 미승인 ID는 전부 unmapped
# ===========================================================================

@pytest.mark.parametrize("skill_id", UNAPPROVED_SKILL_IDS)
def test_unapproved_skill_ids_are_unmapped(skill_id):
    assert cw.skill_code(skill_id) is None
    assert cw.skill_code(skill_id) is cw.UNMAPPED


@pytest.mark.parametrize("misconception_id", UNAPPROVED_MISCONCEPTION_IDS)
def test_unapproved_misconception_ids_are_unmapped(misconception_id):
    assert cw.misconception_code(misconception_id) is None


def test_arithmetic_slip_policy_has_no_misconception():
    """계산 오류는 오개-concept을 부여하지 않으므로 Backend 코드도 없다."""
    from app.pipeline.classifier import _TAXONOMY_BINDING

    error_type, _, misconception_id = _TAXONOMY_BINDING["calculation"]
    assert error_type == "calculation"
    assert misconception_id is None
    assert cw.misconception_code(misconception_id) is None


def test_comprehension_has_no_misconception():
    from app.pipeline.classifier import _TAXONOMY_BINDING

    _, _, misconception_id = _TAXONOMY_BINDING["comprehension"]
    assert misconception_id is None
    assert cw.misconception_code(misconception_id) is None


def test_every_internal_skill_is_unmapped_except_distribution():
    """taxonomy 전체를 훑어 승인된 1건 외에는 전부 None 이어야 한다."""
    for skill_id in all_skill_ids(load_skill_catalog()):
        expected = "SKL-1-004" if skill_id == "distribution" else None
        assert cw.skill_code(skill_id) == expected, skill_id


def test_every_misconception_is_unmapped_except_2_1():
    for entry in MISCONCEPTION_CATALOG.misconceptions:
        expected = "MIS-1-004" if entry.misconception_id == "2.1" else None
        assert cw.misconception_code(entry.misconception_id) == expected, entry.misconception_id


def test_unknown_ids_never_invent_a_code():
    for fake in ("ZZZ", "999", "distribution_omit", "sign_not_flipped", "2.1.0", "SIGN_ERROR"):
        assert cw.skill_code(fake) is None, fake
        assert cw.misconception_code(fake) is None, fake


def test_sign_error_never_used_as_official_code():
    """원본 AI 태그 SIGN_ERROR 는 MIS 코드처럼 쓰지 않는다.

    모듈 docstring 에는 정책 설명용으로 등장할 수 있으므로, 실제 코드 값과
    런타임 조회 결과만 검사한다.
    """
    import ast

    assert cw.misconception_code("SIGN_ERROR") is None

    tree = ast.parse(CROSSWALK_PATH.read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    code_values = set(cw.APPROVED_SKILL_CODES.values()) | set(
        cw.APPROVED_MISCONCEPTION_CODES.values()
    )
    assert literals.isdisjoint(code_values) or literals & code_values == code_values
    for value in code_values:
        assert value.startswith(("SKL-", "MIS-"))
        assert "SIGN_ERROR" not in value


def test_code_values_are_backend_codes_not_ai_tags():
    for code in list(cw.APPROVED_SKILL_CODES.values()) + list(
        cw.APPROVED_MISCONCEPTION_CODES.values()
    ):
        assert code.split("-")[0] in ("SKL", "MIS"), code


def test_code_format_is_validated_at_import():
    """공식 코드 형식이 아니면 import 단계에서 실패해야 한다."""
    source = CROSSWALK_PATH.read_text(encoding="utf-8")
    assert "_CODE_PATTERN" in source
    # import 는 이미 성공했으므로, 잘못된 값이 들어가지 않았음을 확인
    import re as _re

    pattern = _re.compile(r"^(?:SKL|MIS)-\d+-\d+$")
    for code in list(cw.APPROVED_SKILL_CODES.values()) + list(
        cw.APPROVED_MISCONCEPTION_CODES.values()
    ):
        assert pattern.match(code), code


# ===========================================================================
# 5. 리스트 헬퍼
# ===========================================================================

def test_mapped_skill_codes_filters_unapproved():
    assert cw.mapped_skill_codes(["distribution", "transposition"]) == ["SKL-1-004"]
    assert cw.mapped_skill_codes(["transposition", "arithmetic"]) == []
    assert cw.mapped_skill_codes(None) == []
    assert cw.mapped_skill_codes([]) == []


def test_mapped_skill_codes_deduplicates():
    assert cw.mapped_skill_codes(["distribution", "distribution"]) == ["SKL-1-004"]


def test_mapped_misconception_codes_filters_unapproved():
    assert cw.mapped_misconception_codes(["2.1", "3.1"]) == ["MIS-1-004"]
    assert cw.mapped_misconception_codes(["3.1"]) == []
    assert cw.mapped_misconception_codes(None) == []


# ===========================================================================
# 6. 내부 taxonomy ID 불변 + legacy API 불변
# ===========================================================================

def test_crosswalk_does_not_alter_taxonomy_files():
    """frozen JSON 을 읽기만 하고, crosswalk 는 그 값을 바꾸지 않는다."""
    for name in ("skills.json", "error_types.json", "misconceptions.json"):
        raw = (PROJECT_ROOT / "data" / name).read_text(encoding="utf-8")
        json.loads(raw)  # 유효성만 확인 (쓰기 없음)
    assert validate_all() == []


def test_taxonomy_sizes_unchanged():
    assert len(all_skill_ids(load_skill_catalog())) == 31
    assert len(MISCONCEPTION_CATALOG.misconceptions) == 19
    assert len(ERROR_TYPE_CATALOG.error_types) == 4


def test_golden_expected_files_unchanged():
    for name in ("golden_20.json", "golden_100.json"):
        path = PROJECT_ROOT / "tests" / "golden" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "cases" in data and len(data["cases"]) > 0, name


def test_legacy_analyze_solution_response_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert body["skill"] == "distribution"
    assert body["misconception_id"] == "2.1"
    # Backend 코드가 응답에 섞이지 않는다
    assert "skill_code" not in body
    assert "misconception_code" not in body
    assert "SKL-" not in json.dumps(body)
    assert "MIS-" not in json.dumps(body)


def test_attempt_api_response_unchanged():
    """Legacy taxonomy ID 필드는 불변이고, Backend 코드는 새 필드로만 추가된다.

    PHASE 9-7 이후 Attempt 응답은 기존 26 + Backend 코드 5 = 31 이다.
    """
    analyzer.reset_knowledge()
    reset_repository()
    try:
        r = client.post(
            "/api/v1/attempts/A1/analyze",
            json={
                "problem_id": "P1",
                "problem_latex": "2(x-3)=6",
                "problem_skills": ["linear_equation", "distribution"],
                "student_id": "S1",
                "solution_text": "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5",
            },
        )
        body = r.json()
        assert r.status_code == 200
        assert len(body) == 33  # 32 + review_reasons (additive)
        assert body["misconception_id"] == "2.1"
        assert body["skill"] == "distribution"
        assert set(body["misconception_tags"]) == {"2.1"}
        # PHASE 9-7: approved 코드는 새 필드로만, 기존 taxonomy ID 는 그대로
        assert body["official_skill_codes"] == ["SKL-1-004"]
        assert body["official_misconception_codes"] == ["MIS-1-004"]
        assert body["misconception_tag"] == "2.1"
        assert body["provisional_skill_codes"] == []
        assert body["provisional_misconception_codes"] == []
        # taxonomy ID 를 Backend 코드로 덮어쓰지 않는다
        assert body["skill"] != "SKL-1-004"
        assert body["misconception_id"] != "MIS-1-004"
        legacy_fields = {
            key: value
            for key, value in body.items()
            if key
            not in {
                "official_skill_codes",
                "official_misconception_codes",
                "provisional_skill_codes",
                "provisional_misconception_codes",
            }
        }
        payload = json.dumps(legacy_fields)
        assert "SKL-" not in payload
        assert "MIS-" not in payload
    finally:
        analyzer.reset_knowledge()
        reset_repository()


def test_attempt_detail_and_history_unchanged():
    analyzer.reset_knowledge()
    reset_repository()
    try:
        posted = client.post(
            "/api/v1/attempts/A2/analyze",
            json={
                "problem_id": "P1",
                "problem_latex": "2(x-3)=6",
                "problem_skills": ["linear_equation", "distribution"],
                "student_id": "S1",
                "solution_text": "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5",
            },
        ).json()
        detail = client.get(
            "/api/v1/attempts/A2/analyses/"
            + posted["analysis_id"].replace("#", "%23")
        ).json()
        assert len(detail) == 34  # 26 + 5 + raw_result + final_answer_present + review_reasons
        # GET 은 POST 와 같은 매핑을 재생성한다
        for key in (
            "official_skill_codes",
            "official_misconception_codes",
            "provisional_skill_codes",
            "provisional_misconception_codes",
            "misconception_tag",
        ):
            assert detail[key] == posted[key], key
        history = client.get("/api/v1/students/S1/history").json()
        assert len(history["items"][0]) == 23  # 이력 항목은 additive 대상 아님
        top = client.get("/api/v1/students/S1/misconceptions/top").json()
        assert top["items"][0]["misconception_id"] == "2.1"
        assert "MIS-" not in json.dumps(top)
    finally:
        analyzer.reset_knowledge()
        reset_repository()


# ===========================================================================
# 7. 구조 제약 — 기존 모듈은 이 모듈을 import 하지 않는다
# ===========================================================================

def test_existing_modules_do_not_import_crosswalk():
    """변환은 경계에서만. 기존 API/파이프라인은 이 모듈을 알지 않는다.

    PHASE 9-7 이후 예외는 하나뿐이다 — `main.py` 는 Backend 전달 경계이므로
    `backend_adapter` 를 통해 간접적으로만 변환을 요청한다. `backend_crosswalk`
    를 직접 import 하지는 않는다.
    """
    forbidden = (
        "app/schemas.py",
        "app/taxonomy.py",
        "app/pipeline/session.py",
        "app/pipeline/analyzer.py",
        "app/pipeline/classifier.py",
        "app/pipeline/repository.py",
        "app/pipeline/knowledge.py",
        "app/pipeline/verifier.py",
        "app/pipeline/parser.py",
    )
    for relative in forbidden + ("app/main.py",):
        tree = ast.parse((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert not any("backend_crosswalk" in name for name in imported), relative
        if relative != "app/main.py":
            assert not any("app.integration" in name for name in imported), relative
            assert not any("backend_adapter" in name for name in imported), relative


def test_public_api_surface_is_intentional():
    assert set(cw.__all__) == {
        "APPROVED_SKILL_CODES",
        "APPROVED_MISCONCEPTION_CODES",
        "UNMAPPED",
        "misconception_code",
        "skill_code",
        "mapped_skill_codes",
        "mapped_misconception_codes",
        "is_approved_skill_code",
        "is_approved_misconception_code",
    }


def test_route_count_unchanged():
    paths = {
        r.path
        for r in app.routes
        if getattr(r, "path", "") not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
    }
    assert paths == {
        "/health",
        "/analyze-solution",
        "/api/v1/attempts/{attempt_id}/analyze",
        "/api/v1/attempts/{attempt_id}/ocr",
        "/api/v1/attempts/{attempt_id}/analyses",
        "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
        "/api/v1/students/{student_id}/history",
        "/api/v1/students/{student_id}/misconceptions/top",
    }