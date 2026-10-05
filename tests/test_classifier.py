"""오류 분류 — 분배법칙/이항/오독/계산 매핑 검증.

검토 시 발견한 간극:
  - 좌변 중간의 괄호 (3x+2(x-1)) 미검출 → 좌변 전체 치환으로 수정
  - 음수 계수 (-2(x-3)) 미검출 → 계수 정규식에 부호 포함
  - 부호 오류 후보 누락 (2(x-3)→2x+3) → 후보 2종으로 확장
  - (S6) 좌변에 괄호가 여러 개면 첫 번째 것만 검사 → finditer로 전체 순회
"""
from app.pipeline.classifier import (
    _distribution_error_evidence,
    _is_distribution_error,
    classify,
)
from app.pipeline.parser import parse_line


def test_partial_distribution_mid_lhs():
    """좌변 중간의 괄호에서 일부만 분배된 경우."""
    info = classify("3x+2(x-1)=10", ["3x+2(x-1)=10", "3x+2x-1=10"], 1)
    assert info.error_type == "concept_error"
    assert info.skill == "distribution"
    assert info.misconception_id == "2.1"


def test_partial_distribution_negative_factor():
    """음수 계수 분배 오류: -2(x-3) → -2x-3."""
    info = classify("-2(x-3)=6", ["-2(x-3)=6", "-2x-3=6"], 1)
    assert info.skill == "distribution"


def test_distribution_sign_flip():
    """분배 시 부호 오류: 2(x-3) → 2x+3."""
    info = classify("2(x-3)=6", ["2(x-3)=6", "2x+3=6"], 1)
    assert info.skill == "distribution"


def test_distribution_full_second_term_omitted():
    """상수항 분배 누락의 기본형: 2(x-3) → 2x-3."""
    info = classify("2(x-3)=6", ["2(x-3)=6", "2x-3=6"], 1)
    assert info.skill == "distribution"


def test_transposition_still_detected():
    info = classify("3x+5=20", ["3x+5=20", "3x=25"], 1)
    assert info.error_type == "concept_error"
    assert info.skill == "transposition"
    assert info.misconception_id == "3.1"


def test_calculation_fallback():
    info = classify("5x=15", ["5x=15", "x=4"], 1)
    assert info.error_type == "calculation"
    assert info.skill == "arithmetic"


def test_comprehension_on_first_line_misread():
    info = classify("2x+5=13", ["2x+5=15", "2x=10", "x=5"], 0)
    assert info.error_type == "comprehension"
    assert info.skill == "linear_equation"


def test_classify_skips_unrecognized_prev():
    """바로 앞 줄이 인식 실패여도 그 앞의 판정 가능 줄을 기준선으로 삼는다."""
    # 1줄: 2x=6(문제 복사), 2줄: ???(인식실패), 3줄: 2x-3=6(분배 오류 아님 — 계산 오류)
    info = classify("2x=6", ["2x=6", "???", "x=5"], 2)
    # 기준선은 1줄(2x=6) → x=5는 단순 계산 오류
    assert info.error_type == "calculation"


def test_term_drop_is_transposition():
    """항 누락(한쪽에서 제거하고 반대쪽 미반영)은 이항 계열 오류다."""
    info = classify("2x+6=14", ["2x+6=14", "2x=14"], 1)
    assert info.skill == "transposition"


def test_one_side_operation_is_not_term_drop():
    """한쪽에만 연산 적용(0.5x=5 -> x=5)은 항 누락이 아니라 계산 오류."""
    info = classify("0.5x=5", ["0.5x=5", "x=5"], 1)
    assert info.error_type == "calculation"
    assert info.skill == "arithmetic"


# ---------------------------------------------------------------------------
# S6 — 좌변에 괄호가 여러 개일 때 전체를 검사한다
# ---------------------------------------------------------------------------

def test_distribution_error_in_second_parenthesis():
    """A. 두 번째 괄호의 상수항 분배 누락.

    2(x-1)+3(x-1)=4 → 2(x-1)+3x-1=4
    기존 search()는 첫 번째 괄호만 검사해 calculation으로 오분류했다.
    """
    info = classify("2(x-1)+3(x-1)=4", ["2(x-1)+3(x-1)=4", "2(x-1)+3x-1=4"], 1)
    assert info.error_type == "concept_error"
    assert info.skill == "distribution"
    assert info.misconception_id == "2.1"
    assert info.error_subtype == "distribution_omit"


def test_distribution_error_in_second_of_different_parens():
    """B-2. 두 번째 괄호(x+2)의 상수항 분배 오류, 첫 번째 괄호는 정상.

    2(x-1)+3(x+2)=10 → 2x-2+3x+2=10   (3*2=2로 오계산)
    """
    info = classify("2(x-1)+3(x+2)=10", ["2(x-1)+3(x+2)=10", "2x-2+3x+2=10"], 1)
    assert info.error_type == "concept_error"
    assert info.skill == "distribution"
    assert info.misconception_id == "2.1"


def test_second_parens_left_undistributed_is_not_an_error():
    """B-1. 첫 번째 괄호만 정상 전개하고 두 번째를 그대로 두는 단계.

    2(x-1)+3(x+2)=10 → 2x-2+3(x+2)=10 은 대수적으로 '동일'한 올바른 단계다
    (2(x-1) ≡ 2x-2). 따라서 distribution 오류로 판정해서는 안 된다.
    """
    assert _is_distribution_error(
        "2(x-1)+3(x+2)=10",
        parse_line("2(x-1)+3(x+2)=10"),
        parse_line("2x-2+3(x+2)=10"),
    ) is False


def test_no_false_positive_when_all_parens_expanded():
    """C. 모든 괄호가 정상 전개된 단계는 distribution으로 오탐하지 않는다."""
    assert _is_distribution_error(
        "2(x-1)+3(x-1)=4",
        parse_line("2(x-1)+3(x-1)=4"),
        parse_line("2x-2+3x-3=4"),
    ) is False
    info = classify("2(x-1)+3(x-1)=4", ["2(x-1)+3(x-1)=4", "2x-2+3x-3=4", "5x=5", "x=1"], 2)
    assert info.error_type == "calculation"


def test_three_parens_all_inspected():
    """괄호가 3개여도 마지막 것의 오류를 검출한다."""
    info = classify(
        "2(x-1)+3(x-2)+4(x-3)=20",
        ["2(x-1)+3(x-2)+4(x-3)=20", "2x-2+3x-6+4x-3=20"],
        1,
    )
    assert info.skill == "distribution"


def test_distribution_evidence_points_to_the_failing_group():
    """오류가 발생한 괄호 위치 evidence를 유지한다 (어느 그룹에서 틀렸는지)."""
    ev = _distribution_error_evidence(
        "2(x-1)+3(x-1)=4",
        parse_line("2(x-1)+3(x-1)=4"),
        parse_line("2(x-1)+3x-1=4"),
    )
    assert ev is not None
    start, end, factor, inner, variant = ev
    assert inner == "x-1"
    assert factor == "3", "두 번째 괄호(3(x-1))에서 발생한 오류여야 한다"
    assert (start, end) == (7, 13), "evidence는 두 번째 괄호 구간(3(x-1))을 가리켜야 한다"
    assert variant == "omit_constant"


def test_distribution_evidence_absent_for_calculation_error():
    """E. 분배 오류가 아니면 evidence가 없다 (단순 계산 오류를 분배로 보지 않음)."""
    assert (
        _distribution_error_evidence(
            "2(x+1)=8",
            parse_line("2(x+1)=8"),
            parse_line("2x+3=8"),
        )
        is None
    )
    info = classify("2(x+1)=8", ["2(x+1)=8", "2x+3=8"], 1)
    assert info.error_type == "calculation"
    assert info.skill == "arithmetic"
    assert info.misconception_id is None
    assert info.misconception_ids == ()


def test_single_distribution_behavior_preserved():
    """D. 기존 단일 괄호 distribution 오류 동작이 그대로다."""
    for prev, cur in [
        ("2(x-3)=6", "2x-3=6"),
        ("2(x-3)=6", "2x+3=6"),
        ("-2(x-3)=6", "-2x-3=6"),
        ("3x+2(x-1)=10", "3x+2x-1=10"),
        ("(x-3)*2=6", "(x-3)*2=6"),
    ]:
        assert _is_distribution_error(prev, parse_line(prev), parse_line(cur)) is (
            prev != cur
        ), f"{prev} -> {cur}"


def test_taxonomy_binding_applies_to_multi_paren_detection():
    """전체 순회로 찾은 오류도 taxonomy 바인딩을 따른다."""
    info = classify("2(x-1)+3(x-1)=4", ["2(x-1)+3(x-1)=4", "2(x-1)+3x-1=4"], 1)
    assert info.error_type == "concept_error"
    assert info.error_subtype == "distribution_omit"
    assert info.misconception_ids == ("2.1",)
    assert "distribution" in info.skill_ids
