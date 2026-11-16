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


def test_identity_and_contradiction_are_not_equivalent():
    """항등식('모든 x') ↔ 모순('해 없음')은 동치가 아니다.

    sympy solve() 가 두 경우를 모두 [] 로 반환해 `_same_solution_set([], [])` 가
    True 가 되던 오탐의 회귀 테스트. 실측: `5x-3=5x-3` ↔ `5x-3=5x-2` 가 True 였다.
    """
    identity = parse_eq("(2x+1)+(3x-4)=5x-3")  # 좌변 = 5x-3 → 잔차 0
    contradiction = parse_eq("5x-3=5x-2")  # 잔차 -1 → 해 없음
    assert equivalent(identity, contradiction) is False
    assert equivalent(contradiction, identity) is False
    # 같은 종류끼리는 동치
    assert equivalent(identity, identity) is True
    assert equivalent(contradiction, contradiction) is True


def test_identity_problem_catches_wrong_step():
    """항등식 문제에서 모순 줄은 오답으로 잡아야 한다 (오탐 제거)."""
    v = verify("5x-3=5x-3", ["5x-3=5x-3", "5x-3=5x-2"])
    assert v.valid == [True, False]
    assert v.first_error_index == 1


def test_identity_versus_normal_equation_is_not_equivalent():
    """항등식은 '모든 실수', 일반 방정식은 특정 해 → 비동치."""
    assert equivalent(parse_eq("x=x"), parse_eq("x=1")) is False


def test_contradiction_versus_unknown_solution_set_is_held():
    """모순 ↔ 일반식은 일반식의 해 집합이 공집합인지 단정할 수 없어 보류한다."""
    assert equivalent(parse_eq("2x+1=2x+2"), parse_eq("x^2+1=0")) is None


def test_degenerate_classification_is_not_on_the_hot_path(monkeypatch):
    """정상 방정식 비교(양쪽에 해가 있음)에서는 잔차 분류를 호출하지 않는다.

    A-1 최초 구현은 모든 등식 쌍마다 분류를 2회 불러(expand 2회) 전체 테스트가
    80초 → 200초로 느려졌다. 분류는 '한쪽이라도 해 집합이 빈' 희귀 경로에서만
    수행해야 한다. 이 테스트가 그 비용 구조를 고정한다.
    """
    from app.pipeline import verifier as verifier_module

    calls: list = []
    original = verifier_module._residual_kind

    def spy(residual):
        calls.append(residual)
        return original(residual)

    monkeypatch.setattr(verifier_module, "_residual_kind", spy)
    # 보통 비교 — 양쪽 해가 있으므로 분류 불필요
    assert verifier_module.equivalent(parse_eq("2x=6"), parse_eq("x=3")) is True
    assert verifier_module.equivalent(parse_eq("x^2-5x+6=0"), parse_eq("(x-2)(x-3)=0")) is True
    assert calls == []
    # 항등식/모순이 섞이면 그때만 분류한다
    assert verifier_module.equivalent(parse_eq("x=x"), parse_eq("x=1")) is False
    assert calls


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
