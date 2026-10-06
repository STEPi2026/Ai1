"""S9 회귀 — taxonomy validation 강화.

추가된 검증:
  1) typical_error_subtypes 가 해당 misconception의 typical_error_types 에 속하는가
     (subtype의 '존재 여부'만 보는 것으로는 부족 — 부모 error_type 관계를 본다)
  2) related_skill_ids 가 taxonomy에 존재하는 Skill인가
     (group node 는 '개념적 관계'이므로 허용 — PHASE 2 정책과 동일)
  3) name 이 비어 있지 않은가
  4) example_patterns 가 비어 있거나 blank 항목을 포함하지 않는가

frozen taxonomy(data/*.json)은 절대 수정하지 않는다.
모든 실패 케이스는 tmp_path 에 복사본을 만들어 그때만 손상시킨다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.taxonomy import (
    DATA_DIR,
    ErrorTypeCatalog,
    MisconceptionCatalog,
    SkillCatalog,
    check_misconception_integrity,
    load_error_type_catalog,
    load_misconception_catalog,
    load_skill_catalog,
    validate_all,
)


def _write_taxonomy(dest: Path) -> None:
    """실제 taxonomy을 그대로 복사한다 (수정하지 않는다)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("skills.json", "error_types.json", "misconceptions.json"):
        shutil.copyfile(DATA_DIR / name, dest / name)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def taxonomy_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    _write_taxonomy(d)
    return d


# ---------------------------------------------------------------------------
# 기준선: 원본 복사본은 통과해야 한다 (fixture 자체가 유효한지 확인)
# ---------------------------------------------------------------------------

def test_pristine_copy_passes_validation(taxonomy_dir):
    assert validate_all(taxonomy_dir) == []


# ---------------------------------------------------------------------------
# 1) subtype -> 부모 error_type 관계
# ---------------------------------------------------------------------------

def test_valid_subtype_parent_relationship_passes(taxonomy_dir):
    """기준 케이스: distribution_omit 은 concept_error 소속이고 선언도 일치."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "2.1")
    assert target["typical_error_subtypes"] == ["distribution_omit"]
    assert "concept_error" in target["typical_error_types"]
    assert validate_all(taxonomy_dir) == []


def test_subtype_not_belonging_to_declared_error_type_fails(taxonomy_dir):
    """C. distribution_omit 을 calculation 오개념에 붙이면 실패해야 한다."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["typical_error_types"] = ["calculation"]  # 실제 부모는 concept_error
    target["typical_error_subtypes"] = ["distribution_omit"]
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("distribution_omit" in i and "concept_error" in i for i in issues), issues
    assert any("typical_error_types 에 없음" in i for i in issues), issues


def test_unknown_subtype_fails(taxonomy_dir):
    """B. taxonomy에 없는 subtype 은 실패해야 한다."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["typical_error_subtypes"] = ["NOT_A_SUBTYPE"]
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("NOT_A_SUBTYPE" in i and "없음" in i for i in issues), issues


def test_subtype_existence_alone_is_not_enough(taxonomy_dir):
    """subtype이 '존재'하더라도 부모 관계가 다르면 실패해야 한다 (핵 요구)."""
    ecat = load_error_type_catalog(taxonomy_dir)
    scat = load_skill_catalog(taxonomy_dir)
    mcat = MisconceptionCatalog(
        misconceptions=[
            {
                "misconception_id": "9.9",
                "name": "테스트",
                "description": "테스트",
                "related_skill_ids": ["arithmetic"],
                "typical_error_types": ["procedure"],  # sign_not_flipped 의 부모는 concept_error
                "typical_error_subtypes": ["sign_not_flipped"],
                "example_patterns": ["x+1=2"],
            }
        ]
    )
    issues = check_misconception_integrity(mcat, scat, ecat)
    assert any("sign_not_flipped" in i and "concept_error" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 2) related_skill_ids 무결성
# ---------------------------------------------------------------------------

def test_unknown_related_skill_fails(taxonomy_dir):
    """A. 존재하지 않는 Skill 참조는 실패."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "2.1")
    target["related_skill_ids"] = ["distribution", "NOT_EXIST"]
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("NOT_EXIST" in i and "related_skill_ids" in i for i in issues), issues


def test_group_node_in_related_skill_ids_is_allowed(taxonomy_dir):
    """F. group node(factoring)는 개념적 관계이므로 허용되어야 한다."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "2.3")
    target["related_skill_ids"] = ["factoring"]  # bkt_eligible=false group node
    _write(taxonomy_dir / "misconceptions.json", raw)
    assert validate_all(taxonomy_dir) == [], "group node 참조가 거부되었다"

    # 단, Knowledge State 추적은 여전히 거부된다 (두 관심사의 분리)
    from app.taxonomy import SkillNotTrackableError, validate_tracking_skill

    with pytest.raises(SkillNotTrackableError):
        validate_tracking_skill("factoring")


# ---------------------------------------------------------------------------
# 3) name 검증
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", ["", "   ", "\t\n"])
def test_blank_name_fails(taxonomy_dir, bad_name):
    """D. 빈 name / 공백만 있는 name 은 실패."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["name"] = bad_name
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("name" in i and "비어" in i for i in issues), issues


def test_valid_name_passes(taxonomy_dir):
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["name"] = "음수 부호 처리 오해"
    _write(taxonomy_dir / "misconceptions.json", raw)
    assert validate_all(taxonomy_dir) == []


# ---------------------------------------------------------------------------
# 4) example_patterns 검증
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [[""], ["   "], ["ok", ""], ["ok", "  "]])
def test_blank_example_pattern_fails(taxonomy_dir, bad):
    """E. blank example pattern 은 실패."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["example_patterns"] = bad
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("example_patterns" in i for i in issues), issues


def test_empty_example_pattern_list_fails(taxonomy_dir):
    """설계 결정: 현재 19개 오개념이 전부 예시 2개 이상을 갖고 있어
    최소 1개를 요구한다(재사용성 확보)."""
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["example_patterns"] = []
    _write(taxonomy_dir / "misconceptions.json", raw)

    issues = validate_all(taxonomy_dir)
    assert any("example_patterns 가 비어 있음" in i for i in issues), issues


def test_single_example_pattern_is_accepted(taxonomy_dir):
    raw = _read(taxonomy_dir / "misconceptions.json")
    target = next(m for m in raw["misconceptions"] if m["misconception_id"] == "1.1")
    target["example_patterns"] = ["3-(-5)=-2"]
    _write(taxonomy_dir / "misconceptions.json", raw)
    assert validate_all(taxonomy_dir) == []


# ---------------------------------------------------------------------------
# frozen taxonomy 자체가 계속 유효한가 (아이디어 두자루 검증)
# ---------------------------------------------------------------------------

def test_frozen_taxonomy_still_valid():
    assert validate_all() == []


def test_frozen_misconceptions_all_have_names_and_examples():
    mcat = load_misconception_catalog()
    for m in mcat.misconceptions:
        assert m.name.strip(), m.misconception_id
        assert m.example_patterns, m.misconception_id
        assert all(p.strip() for p in m.example_patterns), m.misconception_id


def test_frozen_subtype_parent_relationships_consistent():
    """frozen taxonomy의 모든 오개념이 실제 부모 error_type 을 선언하는지."""
    ecat: ErrorTypeCatalog = load_error_type_catalog()
    parent = {st.error_subtype_id: e.error_type_id for e in ecat.error_types for st in e.subtypes}
    mcat: MisconceptionCatalog = load_misconception_catalog()
    for m in mcat.misconceptions:
        for st in m.typical_error_subtypes:
            assert parent[st] in m.typical_error_types, (m.misconception_id, st)


def test_frozen_related_skill_ids_all_exist():
    scat: SkillCatalog = load_skill_catalog()
    known = {s.skill_id for s in scat.skills + scat.expansion_skills}
    for m in load_misconception_catalog().misconceptions:
        for sid in m.related_skill_ids:
            assert sid in known, (m.misconception_id, sid)
