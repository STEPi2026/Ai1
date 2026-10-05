from app.pipeline.verifier import equivalent, verify


def test_correct_solution_all_valid():
    v = verify("2(x-3)=6", ["2(x-3)=6", "2x-6=6", "2x=12", "x=6"])
    assert v.first_error_index is None
    assert v.valid == [True, True, True, True]


def test_first_error_detected():
    v = verify("5x=15", ["5x=15", "x=4"])
    assert v.first_error_index == 1
    assert v.valid == [True, False]


def test_error_step_is_index_not_line():
    v = verify("4(x+2)=24", ["4(x+2)=24", "4x+8=24", "4x=16", "x=5"])
    assert v.first_error_index == 3


def test_unrecognized_line_is_none_not_error():
    # 인식 실패 줄은 오답으로 판정하지 않는다 (ADR-03)
    v = verify("2x=6", ["2x=6", "???인식불가", "x=3"])
    assert v.unrecognized == [2]
    assert v.valid[1] is None
    assert v.first_error_index is None


def test_equivalent_rearrangement():
    a = parse_eq("2x+7=3x-5")
    b = parse_eq("2x-3x=-5-7")
    assert equivalent(a, b) is True


def test_not_equivalent():
    a = parse_eq("2x=12")
    b = parse_eq("x=7")
    assert equivalent(a, b) is False


def parse_eq(text: str):
    from app.pipeline.parser import parse_line

    return parse_line(text)



def test_gap_bridging_detects_error_after_unrecognized():
    """인식 실패 줄 뒤의 오류도 그 앞 판정 가능 줄과 대조해 포착한다."""
    v = verify("2x=6", ["2x=6", "???", "x=5"])
    assert v.first_error_index == 2
    assert v.unrecognized == [2]


def test_gap_bridging_passes_when_consistent():
    v = verify("2x=6", ["2x=6", "???", "x=3"])
    assert v.first_error_index is None


def test_expression_line_is_unrecognized():
    """식 단독 줄(= 없는)은 판정 불가로 간주하고 unrecognized에 넣는다."""
    v = verify("2x=6", ["2x-6", "x=3"])
    assert v.unrecognized == [1]
    assert v.valid[0] is None
    assert v.valid[1] is True  # 문제식과 대조(bridge)
