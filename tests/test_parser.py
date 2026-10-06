from app.pipeline.parser import normalize, parse_line, split_steps


def test_normalize_frac_and_braces():
    assert normalize(r"\frac{1}{2}") == "((1)/(2))"
    assert normalize("x^{2}") == "x**2"


def test_normalize_unicode_minus_and_spaces():
    assert normalize("2x − 6 = 6") == "2x-6=6"


def test_parse_line_equation():
    kind = parse_line("2(x-3)=6")
    assert kind is not None and kind[0] == "eq"


def test_parse_line_korean_returns_none():
    # 인식 실패는 오답이 아님 (ADR-03) — None 반환
    assert parse_line("보통 이렇게 품") is None


def test_parse_line_unbalanced_returns_none():
    assert parse_line("2x+=(3") is None


def test_split_steps_newline_and_arrow():
    assert split_steps("3x+5=20\n3x=15\nx=5") == ["3x+5=20", "3x=15", "x=5"]
    assert split_steps("3x+5=20 → 3x=15 → x=5") == ["3x+5=20", "3x=15", "x=5"]


def test_split_steps_numbering():
    assert split_steps("1) 3x+5=20\n2) x=5") == ["3x+5=20", "x=5"]


def test_split_steps_empty():
    assert split_steps("") == []
    assert split_steps(None) == []


def test_split_steps_decimal_not_numbering():
    """소수점으로 시작하는 식을 리스트 번호로 오판하면 안 됨 (100건 확장 시 발견)."""
    assert split_steps("0.5x+2=7\nx=10") == ["0.5x+2=7", "x=10"]
    assert split_steps("1.5x-3=6\nx=6") == ["1.5x-3=6", "x=6"]


def test_split_steps_numbering_with_dot_needs_space():
    assert split_steps("2. 3x+5=20") == ["3x+5=20"]
    assert split_steps("1) 3x+5=20") == ["3x+5=20"]
