"""PHASE 9-7 — Backend 전달용 Text Analysis Adapter 단위 테스트.

검증 범위
  - approved 매핑은 기존 crosswalk 를 그대로 재노출한다(중복 정의 없음)
  - provisional 표는 시드 밖 코드 제거로 비어 있고, 그 코드는 어디에도 나오지 않는다
  - unmapped 는 코드를 만들지 않는다 ([] 반환)
  - 중복 제거와 결과 순서(최초 등장 순)
  - 금지 코드(MIS-2-003 / MIS-2-004 / STP-*)를 절대 출력하지 않는다
  - 대표 오류 = 가장 낮은 step_no, confidence 를 합산하지 않는다
  - 식별자 정규화: 정수 → 십진 문자열, 접두사 없음, 문자열 보존
  - Backend Skill code 역변환과 422 대상 코드
  - 경계 규칙: 변환은 adapter 에서만, 내부 파이프라인은 모른다
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from app.integration import backend_adapter as ba
from app.integration import backend_crosswalk as cw
from app.taxonomy import MISCONCEPTION_CATALOG, all_skill_ids, load_skill_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = PROJECT_ROOT / "app" / "integration" / "backend_adapter.py"


class _FakeError:
    """ErrorRecord 를 흉내 내는 최소 객체 (step_no / misconception_ids 기준만 사용)."""

    def __init__(self, step_no: int, misconception_ids: list[str], confidence: float = 0.9):
        self.step_no = step_no
        self.misconception_ids = misconception_ids
        self.confidence = confidence


class _FakeAnalysis:
    def __init__(self, api: dict, errors: list):
        self.api_response = api
        self.errors = errors


# ===========================================================================
# 1. approved 매핑은 기존 crosswalk 소유 (이중 정의 금지)
# ===========================================================================

def test_approved_tables_are_the_crosswalk_objects():
    """approved 표를 복사하지 않는다 — 소스는 backend_crosswalk 단독."""
    assert ba.APPROVED_SKILL_CODES is cw.APPROVED_SKILL_CODES
    assert ba.APPROVED_MISCONCEPTION_CODES is cw.APPROVED_MISCONCEPTION_CODES


def test_approved_mapping_values_unchanged():
    assert ba.skill_code("distribution") == "SKL-1-004"
    assert ba.misconception_code("2.1") == "MIS-1-004"


# ===========================================================================
# 2. provisional 매핑 — 시드 밖 코드 제거로 비어 있다 (A2)
# ===========================================================================

def test_provisional_tables_are_empty_after_seed_scope_fix():
    """관계자료 xlsx 전용 코드(SKL-2-037, MIS-2-001/002)는 공식 Seed 에 없다.

    A2(2026-11-10) 결정: 시드 밖 코드는 내보내지 않고 역방향 수용도 하지 않는다.
    승인 후보가 생기면 이 표에 다시 채운다.
    """
    assert ba.PROVISIONAL_SKILL_CODES == {}
    assert ba.PROVISIONAL_MISCONCEPTION_CODES == {}


def test_seed_outside_codes_are_never_mapped():
    """시드 밖 코드는 official/provisional 어디에도 나오지 않는다."""
    assert ba.skill_code("transposition") is None
    assert ba.misconception_code("3.1") is None
    assert ba.misconception_code("3.2") is None
    assert ba.is_approved_skill_code("SKL-2-037") is False
    assert ba.is_approved_misconception_code("MIS-2-001") is False
    assert ba.is_provisional_skill_code("SKL-2-037") is False
    assert ba.is_provisional_misconception_code("MIS-2-001") is False
    # 역방향(problem_skills 수용)도 막힌다 — 기본 Skill 로 치환하지 않는다
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.skill_id_for_code("SKL-2-037")


def test_provisional_and_approved_never_collide():
    assert set(ba.PROVISIONAL_SKILL_CODES) & set(ba.APPROVED_SKILL_CODES) == set()
    assert set(ba.PROVISIONAL_MISCONCEPTION_CODES) & set(
        ba.APPROVED_MISCONCEPTION_CODES
    ) == set()
    assert set(ba.PROVISIONAL_SKILL_CODES.values()) & set(
        ba.APPROVED_SKILL_CODES.values()
    ) == set()
    assert set(ba.PROVISIONAL_MISCONCEPTION_CODES.values()) & set(
        ba.APPROVED_MISCONCEPTION_CODES.values()
    ) == set()


# ===========================================================================
# 3. unmapped — 코드를 만들어내지 않는다
# ===========================================================================

@pytest.mark.parametrize(
    "skill_id",
    ["linear_equation", "arithmetic", "polynomial_multiplication", "comprehension"],
)
def test_unmapped_skill_ids_stay_none(skill_id):
    assert ba.skill_code(skill_id) is None
    assert ba.official_skill_codes([skill_id]) == []
    assert ba.provisional_skill_codes([skill_id]) == []


@pytest.mark.parametrize("misconception_id", ["1.1", "2.2", "5.1", "comprehension"])
def test_unmapped_misconception_ids_stay_none(misconception_id):
    assert ba.misconception_code(misconception_id) is None
    assert ba.official_misconception_codes([misconception_id]) == []
    assert ba.provisional_misconception_codes([misconception_id]) == []


def test_every_taxonomy_skill_everywhere_is_either_approved_or_provisional_or_none():
    """taxonomy 전체를 훑어 approved/provisional 표에 없는 값은 None 이어야 한다."""
    known_skills = set(all_skill_ids(load_skill_catalog()))
    table_skills = set(ba.APPROVED_SKILL_CODES) | set(ba.PROVISIONAL_SKILL_CODES)
    assert table_skills <= known_skills, table_skills - known_skills


def test_every_taxonomy_misconception_mapped_value_exists():
    known = {m.misconception_id for m in MISCONCEPTION_CATALOG.misconceptions}
    table = set(ba.APPROVED_MISCONCEPTION_CODES) | set(ba.PROVISIONAL_MISCONCEPTION_CODES)
    assert table <= known, table - known


# ===========================================================================
# 4. 금지 코드 — MIS-2-003 / MIS-2-004 / STP-*
# ===========================================================================

def test_forbidden_codes_documented():
    assert set(ba.FORBIDDEN_CODES) == {"MIS-2-003", "MIS-2-004"}


def test_forbidden_codes_never_in_any_mapping_table():
    all_codes = (
        list(ba.APPROVED_SKILL_CODES.values())
        + list(ba.APPROVED_MISCONCEPTION_CODES.values())
        + list(ba.PROVISIONAL_SKILL_CODES.values())
        + list(ba.PROVISIONAL_MISCONCEPTION_CODES.values())
    )
    for forbidden in ba.FORBIDDEN_CODES:
        assert forbidden not in all_codes, forbidden


def test_arithmetic_slip_never_gets_a_misconception_code():
    """일반 계산 실수에 MIS-2-003 을 자동으로 붙이지 않는다."""
    assert ba.official_misconception_codes([]) == []
    assert ba.provisional_misconception_codes([]) == []
    # 계산 오류는 내부 오개념 ID 가 없다 → confirm 결과도 비고, 코드도 비고
    analysis = _FakeAnalysis({"skill_ids": ["arithmetic"]}, [])
    view = ba.text_analysis_view(analysis)
    assert view["official_misconception_codes"] == []
    assert view["provisional_misconception_codes"] == []
    assert view["misconception_tag"] is None


def test_comprehension_gets_no_arbitrary_misconception_code():
    analysis = _FakeAnalysis({"skill_ids": ["linear_equation"]}, [])
    view = ba.text_analysis_view(analysis)
    assert view["official_misconception_codes"] == []
    assert view["provisional_misconception_codes"] == []
    assert view["misconception_tag"] is None


def test_no_callable_emits_stp_or_division_by_zero():
    probes_ids = [None, "", "distribution", "2.1", "transposition", "3.1", "3.2"]
    for name in ("skill_code", "misconception_code"):
        for probe in probes_ids:
            result = getattr(ba, name)(probe)
            assert not (isinstance(result, str) and result.startswith("STP-"))
            assert result != "MIS-2-004"
    for name in (
        "official_skill_codes",
        "provisional_skill_codes",
        "official_misconception_codes",
        "provisional_misconception_codes",
    ):
        for probe in (None, [], ["distribution"], ["2.1"], ["transposition", "3.1", "3.2"]):
            result = getattr(ba, name)(probe)
            assert all(not v.startswith("STP-") for v in result)
            assert "MIS-2-004" not in result


def test_adapter_emits_no_stp_literal_in_mapping_values():
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    mapped = set(ba.PROVISIONAL_SKILL_CODES.values()) | set(
        ba.PROVISIONAL_MISCONCEPTION_CODES.values()
    )
    for code in mapped:
        assert code in literals, code
        assert not code.startswith("STP-")


# ===========================================================================
# 5. 배열 변환 — 중복 제거와 순서
# ===========================================================================

def test_approved_and_provisional_split():
    assert ba.official_skill_codes(["distribution", "transposition"]) == ["SKL-1-004"]
    assert ba.provisional_skill_codes(["distribution", "transposition"]) == []
    assert ba.official_misconception_codes(["2.1", "3.1"]) == ["MIS-1-004"]
    assert ba.provisional_misconception_codes(["2.1", "3.1"]) == []


def test_dedup_keeps_first_occurrence_order():
    assert ba.official_skill_codes(
        ["distribution", "transposition", "distribution"]
    ) == ["SKL-1-004"]
    assert ba.official_misconception_codes(["2.1", "3.1", "2.1"]) == ["MIS-1-004"]


def test_split_codes_dedup_keeps_first_occurrence_order():
    """provisional 표가 비어도 분리·중복 제거·순서 계약은 유지된다.

    실제 표에 후보가 다시 채워지면 이 경로가 그대로 쓰이므로, 매퍼를 주입해
    동작 자체를 고정한다(표 내용과 분리).
    """
    codes = {"a": "SKL-9-001", "b": "SKL-9-002"}
    official, provisional = ba._split_codes(
        ["b", "a", "b", "unknown"], lambda internal: codes.get(internal)
    )
    assert official == []
    assert provisional == ["SKL-9-002", "SKL-9-001"]


def test_empty_input_yields_empty_lists():
    for fn in (
        ba.official_skill_codes,
        ba.provisional_skill_codes,
        ba.official_misconception_codes,
        ba.provisional_misconception_codes,
    ):
        assert fn(None) == []
        assert fn([]) == []
        assert fn(()) == []


# ===========================================================================
# 6. 대표 오류 — 가장 낮은 step_no
# ===========================================================================

def test_primary_error_is_lowest_step_no():
    errors = [_FakeError(5, ["2.1"]), _FakeError(2, ["3.1"]), _FakeError(4, [])]
    assert ba.primary_error(errors).step_no == 2


def test_primary_error_confidence_is_not_averaged():
    """여러 오류의 confidence 를 합산·평균하지 않는다."""
    errors = [_FakeError(2, ["3.1"], 0.9), _FakeError(5, ["2.1"], 0.3)]
    assert ba.primary_error(errors).confidence == 0.9
    avg = (0.9 + 0.3) / 2
    assert ba.primary_error(errors).confidence != avg


def test_primary_error_none_when_no_errors():
    assert ba.primary_error([]) is None
    assert ba.primary_error(None) is None


def test_confirmed_misconception_ids_keeps_all_errors_sorted():
    errors = [_FakeError(5, ["2.1"]), _FakeError(2, ["3.2"]), _FakeError(4, ["3.1"])]
    assert ba.confirm_concept_ids(errors) == ["2.1", "3.1", "3.2"]
    assert ba.confirm_concept_ids([]) == []


# ===========================================================================
# 7. Backend 전달 뷰 (additive 5필드)
# ===========================================================================

def test_text_analysis_view_shape_and_keys():
    analysis = _FakeAnalysis(
        {"skill_ids": ["distribution", "linear_equation"], "skill": "distribution"},
        [_FakeError(2, ["2.1"]), _FakeError(3, ["3.1"])],
    )
    view = ba.text_analysis_view(analysis)
    assert set(view) == {
        "official_skill_codes",
        "official_misconception_codes",
        "provisional_skill_codes",
        "provisional_misconception_codes",
        "misconception_tag",
    }
    assert view["official_skill_codes"] == ["SKL-1-004"]
    assert view["provisional_skill_codes"] == []
    assert view["official_misconception_codes"] == ["MIS-1-004"]
    # 3.1 은 시드 밖 코드라 공식/잠정 배열에 나가지 않는다 (A2)
    assert view["provisional_misconception_codes"] == []
    assert view["misconception_tag"] == "2.1"


def test_text_analysis_view_tag_is_primary_error_only():
    """단수 태그는 대표 오류(최소 step_no)의 내부 오개념 ID 이다."""
    errors = [_FakeError(4, ["3.2"]), _FakeError(2, ["2.1"])]
    analysis = _FakeAnalysis({"skill_ids": ["distribution"]}, errors)
    view = ba.text_analysis_view(analysis)
    assert view["misconception_tag"] == "2.1"
    # 내부 ID 는 모두 보존되지만, Backend 코드는 시드에 있는 것만 나간다
    assert ba.confirm_concept_ids(errors) == ["2.1", "3.2"]
    assert view["official_misconception_codes"] == ["MIS-1-004"]
    assert view["provisional_misconception_codes"] == []


def test_text_analysis_view_is_deterministic_for_same_input():
    analysis = _FakeAnalysis(
        {"skill_ids": ["distribution", "transposition"]},
        [_FakeError(2, ["2.1"]), _FakeError(6, ["3.1", "3.2"])],
    )
    assert ba.text_analysis_view(analysis) == ba.text_analysis_view(analysis)


def test_text_analysis_view_handles_correct_solution_without_errors():
    analysis = _FakeAnalysis({"skill_ids": ["linear_equation"], "skill": "linear_equation"}, [])
    view = ba.text_analysis_view(analysis)
    assert view["misconception_tag"] is None
    assert view["official_misconception_codes"] == []
    assert view["provisional_misconception_codes"] == []
    # linear_equation 은 approved/provisional 표에 없다 → 추측하지 않는다
    assert view["official_skill_codes"] == []
    assert view["provisional_skill_codes"] == []


def test_text_analysis_view_falls_back_to_skill_when_skill_ids_missing():
    analysis = _FakeAnalysis({"skill": "distribution"}, [])
    assert ba.text_analysis_view(analysis)["official_skill_codes"] == ["SKL-1-004"]


# ===========================================================================
# 8. 식별자 정규화
# ===========================================================================

@pytest.mark.parametrize(
    "raw,expected",
    [(4821, "4821"), (0, "0"), (-7, "-7"), ("A1", "A1"), ("  A1  ", "A1")],
)
def test_normalize_backend_id(raw, expected):
    assert ba.normalize_backend_id(raw, "attempt_id") == expected


def test_normalize_int_adds_no_prefix():
    """접두사를 임의로 붙이지 않는다."""
    assert ba.normalize_backend_id(4821, "attempt_id") == "4821"
    assert not ba.normalize_backend_id(4821, "attempt_id").startswith(("t_", "s_", "T"))


def test_normalize_string_keeps_leading_zeros():
    """선행 0 을 지우면 다른 ID 와 합쳐지므로 보존한다."""
    assert ba.normalize_backend_id("004821", "attempt_id") == "004821"
    assert ba.normalize_backend_id("004821", "attempt_id") != ba.normalize_backend_id(
        4821, "attempt_id"
    )


@pytest.mark.parametrize("bad", [True, False, None, "", "   ", 3.5, [], {}])
def test_normalize_backend_id_rejects_bad_values(bad):
    with pytest.raises(ba.BackendIdError):
        ba.normalize_backend_id(bad, "student_id")


def test_backend_id_error_code_is_stable():
    assert ba.BackendIdError.code == "E_INVALID_BACKEND_ID"


# ===========================================================================
# 9. Backend Skill code 역변환
# ===========================================================================

@pytest.mark.parametrize("code,skill_id", [("SKL-1-004", "distribution")])
def test_skill_id_for_code(code, skill_id):
    assert ba.skill_id_for_code(code) == skill_id


def test_reverse_index_covers_both_tables():
    assert len(ba._SKILL_CODE_TO_ID) == len(ba.APPROVED_SKILL_CODES) + len(
        ba.PROVISIONAL_SKILL_CODES
    )


@pytest.mark.parametrize(
    "bad", ["SKL-9-999", "MIS-1-004", "SKL-1-4", "distribution", "SKL-2-037"]
)
def test_unknown_or_malformed_codes_raise(bad):
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.skill_id_for_code(bad)


def test_unknown_skill_code_error_code_is_stable():
    assert ba.UnknownSkillCodeError.code == "E_UNKNOWN_SKILL_CODE"


def test_resolve_problem_skills_mixes_codes_and_internal_ids():
    assert ba.resolve_problem_skills(
        ["SKL-1-004", "linear_equation"]
    ) == ["distribution", "linear_equation"]


def test_resolve_problem_skills_dedups():
    assert ba.resolve_problem_skills(["SKL-1-004", "distribution"]) == ["distribution"]


def test_resolve_problem_skills_passes_through_internal_ids():
    assert ba.resolve_problem_skills(["arithmetic"]) == ["arithmetic"]


@pytest.mark.parametrize("bad", [["SKL-9-999"], ["MIS-2-001"], [""], [123]])
def test_resolve_problem_skills_rejects_unknown_or_bad(bad):
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.resolve_problem_skills(bad)


def test_resolve_problem_skills_never_defaults_to_linear_equation():
    """모르는 코드에 기본 Skill 을 넣지 않는다."""
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.resolve_problem_skills(["SKL-9-999"])
    assert ba.resolve_problem_skills(None) == []


# ===========================================================================
# 10. 경계 규칙 — 변환은 adapter 에서만
# ===========================================================================

def test_pipeline_and_taxonomy_do_not_import_adapter():
    for relative in (
        "app/taxonomy.py",
        "app/schemas.py",
        "app/pipeline/session.py",
        "app/pipeline/analyzer.py",
        "app/pipeline/classifier.py",
        "app/pipeline/repository.py",
        "app/pipeline/knowledge.py",
        "app/pipeline/verifier.py",
        "app/pipeline/parser.py",
    ):
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "backend_adapter" not in source, relative
        assert "app.integration" not in source, relative


def test_main_uses_only_the_adapter_boundary():
    """main 은 adapter 만 알고 crosswalk 를 직접 쓰지 않는다."""
    source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "backend_adapter" in source
    assert "backend_crosswalk" not in source


def test_adapter_public_surface_is_intentional():
    assert set(ba.__all__) == {
        "APPROVED_MISCONCEPTION_CODES",
        "APPROVED_SKILL_CODES",
        "PROVISIONAL_MISCONCEPTION_CODES",
        "PROVISIONAL_SKILL_CODES",
        "FORBIDDEN_CODES",
        "UNKNOWN_SKILL_CODE",
        "BackendIdError",
        "NoTrackingSkillError",
        "UnknownSkillCodeError",
        "UnknownSkillIdError",
        "confirm_concept_ids",
        "is_approved_skill_code",
        "is_approved_misconception_code",
        "is_provisional_skill_code",
        "is_provisional_misconception_code",
        "misconception_code",
        "normalize_backend_id",
        "official_misconception_codes",
        "official_skill_codes",
        "primary_error",
        "provisional_misconception_codes",
        "provisional_skill_codes",
        "require_known_skills",
        "require_problem_skills",
        "resolve_problem_skills",
        "skill_code",
        "skill_id_for_code",
        "text_analysis_view",
    }
    functions = {
        name
        for name, obj in vars(ba).items()
        if not name.startswith("_") and inspect.isfunction(obj)
    }
    assert functions <= set(ba.__all__)
    assert not any(name.startswith(("stp_", "concept_code")) for name in functions)


def test_adapter_does_not_touch_weakness_or_state():
    """Backend 가 최종 상태를 소유하므로 adapter 는 상태/weakness 를 계산하지 않는다."""
    analysis = _FakeAnalysis(
        {"skill_ids": ["distribution"], "skill": "distribution", "state": "needs_practice"},
        [_FakeError(2, ["2.1"])],
    )
    view = ba.text_analysis_view(analysis)
    for forbidden_key in ("weakness", "weakness_score", "state", "mastery", "final_state"):
        assert forbidden_key not in view, forbidden_key
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "RuleStateEngine",
        "BKTEngine",
        "get_engine",
        "update_from_observation",
    ):
        assert forbidden not in source, forbidden


def test_3_2_scope_limitation_is_documented():
    """3.2 는 항 누락/일부 비대칭만 탐지한다 — 넓은 탐지 주장을 하지 않는다.

    A2 로 시드 밖 코드를 제거했으므로, 이 한계는 내부 detector 설명으로만 남고
    Backend 로 나가는 코드에는 붙지 않는다.
    """
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert "항 누락" in source
    assert "일방 곱셈" in source and "일방 나눗셈" in source
    assert "일방 곱셈·나눗셈은 3.2 로 분류되지 않는다" in source
    assert "일방 곱셈·나눗셈" not in str(ba.PROVISIONAL_MISCONCEPTION_CODES)
    # 시드 밖 코드 3.1 / 3.2 는 provisional 표에 없다 (A2)
    assert "3.1" not in ba.PROVISIONAL_MISCONCEPTION_CODES
    assert "3.2" not in ba.PROVISIONAL_MISCONCEPTION_CODES


def test_no_network_or_auth_dependency_added():
    """인증 토큰 규약·OCR 벤더·multipart 를 이 Phase 에 넣지 않는다."""
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    for forbidden in ("requests", "httpx", "multipart", "UploadFile", "api_key", "secret", "token"):
        assert forbidden not in source, forbidden


def test_frozen_data_and_golden_untouched_by_adapter():
    for name in ("skills.json", "error_types.json", "misconceptions.json"):
        json.loads((PROJECT_ROOT / "data" / name).read_text(encoding="utf-8"))
    for name in ("golden_20.json", "golden_100.json"):
        payload = json.loads((PROJECT_ROOT / "tests" / "golden" / name).read_text(encoding="utf-8"))
        assert payload["cases"], name