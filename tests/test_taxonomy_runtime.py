"""S3 회귀 — taxonomy runtime 통합.

classifier의 하드코딩 skill/misconception 값을 frozen taxonomy 조회로 대체했다.
이 테스트는 (1) 모든 바인딩이 taxonomy에 실재하고 (2) 기존 4 skill·2 misconception
동작이 그대로임을 고정한다. taxonomy JSON은 수정하지 않는다.
"""
from __future__ import annotations

import pytest

from app.pipeline.classifier import (
    MISCONCEPTION_DISTRIBUTION,
    MISCONCEPTION_TRANSPOSITION,
    _SKILL_BY_KIND,
    _TAXONOMY_BINDING,
    classify,
)
from app.taxonomy import (
    ERROR_SUBTYPES_BY_TYPE,
    LEGACY_MISCONCEPTIONS,
    LEGACY_SKILL_IDS,
    is_active_error_subtype,
    is_known_skill,
    is_valid_error_subtype,
    is_valid_misconception_id,
    related_skill_ids,
    skill_by_id,
)

# 실제 관측 가능한 4가지 오류 유형 시나리오
SCENARIOS = {
    "distribution": ("2(x-3)=6", ["2(x-3)=6", "2x-3=6"], 1),
    "transposition": ("3x+5=20", ["3x+5=20", "3x=25"], 1),
    "comprehension": ("2x+5=13", ["2x+5=15", "2x=10", "x=5"], 0),
    "calculation": ("5x=15", ["5x=15", "x=4"], 1),
}


def _classify(kind: str):
    problem, steps, idx = SCENARIOS[kind]
    return classify(problem, steps, idx)


# ---------------------------------------------------------------------------
# 1. 바인딩이 taxonomy에 실재하는가
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(_TAXONOMY_BINDING))
def test_bound_skill_exists_in_taxonomy(kind):
    assert is_known_skill(_SKILL_BY_KIND[kind])


@pytest.mark.parametrize("kind", sorted(_TAXONOMY_BINDING))
def test_bound_error_type_and_subtype_pair_is_valid(kind):
    error_type, error_subtype, _ = _TAXONOMY_BINDING[kind]
    assert error_type in ERROR_SUBTYPES_BY_TYPE, f"{kind}: 없는 error_type {error_type}"
    assert is_valid_error_subtype(error_type, error_subtype), (
        f"{kind}: {error_subtype} 는 {error_type} 의 subtype이 아님"
    )


@pytest.mark.parametrize("kind", sorted(_TAXONOMY_BINDING))
def test_bound_subtype_is_active(kind):
    _, error_subtype, _ = _TAXONOMY_BINDING[kind]
    assert is_active_error_subtype(error_subtype), f"{error_subtype} 가 future 상태"


@pytest.mark.parametrize("kind", sorted(_TAXONOMY_BINDING))
def test_bound_misconception_exists_and_matches_error_type(kind):
    error_type, error_subtype, misconception_id = _TAXONOMY_BINDING[kind]
    if misconception_id is None:
        return
    assert is_valid_misconception_id(misconception_id)
    related = related_skill_ids(misconception_id)
    assert related, f"{misconception_id} 에 related_skill_ids가 없다"
    # 오개념이 연결된 skill은 모두 taxonomy에 존재해야 한다
    for sid in related:
        assert is_known_skill(sid)


# ---------------------------------------------------------------------------
# 2. 기존 4 skill / 2 misconception 동작 보존
# ---------------------------------------------------------------------------

def test_existing_four_skills_preserved():
    for kind, expected in [
        ("distribution", "distribution"),
        ("transposition", "transposition"),
        ("comprehension", "linear_equation"),
        ("calculation", "arithmetic"),
    ]:
        assert _classify(kind).skill == expected


def test_existing_two_misconceptions_preserved():
    assert MISCONCEPTION_DISTRIBUTION == "2.1"
    assert MISCONCEPTION_TRANSPOSITION == "3.1"
    assert _classify("distribution").misconception_id == "2.1"
    assert _classify("transposition").misconception_id == "3.1"


def test_legacy_constants_still_match_taxonomy():
    assert set(LEGACY_SKILL_IDS) == set(_SKILL_BY_KIND.values())
    for mid, name in LEGACY_MISCONCEPTIONS.items():
        assert mid in {MISCONCEPTION_DISTRIBUTION, MISCONCEPTION_TRANSPOSITION}
        from app.taxonomy import misconception_by_id

        assert name in misconception_by_id(mid).name


# ---------------------------------------------------------------------------
# 3. error_subtype / skill_ids / misconception_ids 결정 규칙
# ---------------------------------------------------------------------------

def test_distribution_evidence_links_misconception_2_1():
    info = _classify("distribution")
    assert info.error_type == "concept_error"
    assert info.error_subtype == "distribution_omit"
    assert info.misconception_ids == ("2.1",)
    assert info.skill_ids == tuple(related_skill_ids("2.1"))
    assert "distribution" in info.skill_ids


def test_transposition_evidence_links_misconception_3_1():
    info = _classify("transposition")
    assert info.error_type == "concept_error"
    assert info.error_subtype == "sign_not_flipped"
    assert info.misconception_ids == ("3.1",)
    assert "transposition" in info.skill_ids


def test_comprehension_has_subtype_but_no_misconception():
    info = _classify("comprehension")
    assert info.error_type == "comprehension"
    assert info.error_subtype == "misread_problem"
    assert info.misconception_id is None
    assert info.misconception_ids == ()
    assert info.skill_ids == ("linear_equation",)


def test_calculation_does_not_get_forced_misconception():
    """요구 G: 단순 계산 오류에 오개념을 억지로 붙이지 않는다."""
    info = _classify("calculation")
    assert info.error_type == "calculation"
    assert info.error_subtype == "arithmetic_slip"
    assert info.misconception_id is None
    assert info.misconception_ids == (), "계산 오류에 오개념이 붙었다"
    assert info.skill_ids == ("arithmetic",)


# ---------------------------------------------------------------------------
# 4. classifier가 taxonomy 밖 값을 생성하지 않는가
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(SCENARIOS))
def test_every_classify_output_is_within_taxonomy(kind):
    info = _classify(kind)
    assert is_known_skill(info.skill)
    assert info.error_type in ERROR_SUBTYPES_BY_TYPE
    assert is_valid_error_subtype(info.error_type, info.error_subtype)
    for sid in info.skill_ids:
        assert is_known_skill(sid)
    for mid in info.misconception_ids:
        assert is_valid_misconception_id(mid)
    for mid in ([info.misconception_id] if info.misconception_id else []):
        assert is_valid_misconception_id(mid)


def test_skill_ids_are_trackable_or_just_conceptual():
    """skill_ids는 개념 분류이므로 group node를 담을 수 있다(문제 6)."""
    info = _classify("distribution")
    for sid in info.skill_ids:
        assert is_known_skill(sid)
    # Knowledge State에는 primary skill(skill)만 들어간다
    assert skill_by_id(info.skill).bkt_eligible is True


# ---------------------------------------------------------------------------
# 5. confidence 보존 (기존 계약)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kind,expected",
    [
        ("distribution", 0.9),
        ("transposition", 0.9),
        ("comprehension", 0.85),
        ("calculation", 0.75),
    ],
)
def test_confidence_unchanged(kind, expected):
    assert _classify(kind).confidence == expected
