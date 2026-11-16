"""A6 P1 — P1 범위 밖 표기 판정 (B4 확정분: 함수 표기).

P1 은 중학 다항식(등식·전개·인수분해)까지다. 함수 표기는 범위 밖이므로
판정하지 않고 거절한다.

왜 별도 판정이 필요한가:
    ``parse_line("f(x)=2x+1")`` 은 함수 호출이 아니라 **곱셈** ``f*x`` 로
    해석한다. 그래서 탐지 없이 두면 함수 문제를 방정식으로 오판해
    ``correct=true`` 를 낼 수 있다. 판정 불가를 오답/정답으로 승격하지 않는다는
    ADR-03 의 직접적인 위반 사례다.

탐지 범위 (의도적으로 좁게):
    - 관례적 함수 이름 ``f``/``g``/``h`` + ``(``
    - 이름 있는 함수 ``sin``·``cos``·``log`` 등 + ``(``
    - ``sqrt`` 는 P1 허용 함수이므로 제외
    ``a(b+c)`` 처럼 변수 a 와 괄호의 곱은 정상 계산이므로 잡지 않는다.
    따라서 ``f``/``g``/``h`` 를 변수로 쓰는 문제는 이 판정에 걸릴 수 있다
    (P1 교과 범위에서 쓰지 않는 문자 관례를 택했다 — 계약 문서 §13.6 참조).
"""
from __future__ import annotations

import re

from .inequality import normalize_for_detection

#: 함수 표기로 간주하는 이름. `sqrt` 는 P1 허용이므로 넣지 않는다.
FUNCTION_NAMES = (
    "arcsin",
    "arccos",
    "arctan",
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "log",
    "ln",
    "lim",
    "exp",
    "f",
    "g",
    "h",
)

# 긴 이름을 먼저 매칭하고, 이름 앞에 영문자가 오면 다른 식별자로 본다.
_FUNCTION_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(sorted(FUNCTION_NAMES, key=len, reverse=True))
    + r")\s*\("
)


def has_function_notation(latex: str) -> bool:
    """함수 표기(``f(x)`` 등)가 있으면 True."""
    if not latex:
        return False
    return _FUNCTION_RE.search(normalize_for_detection(latex)) is not None


__all__ = ["FUNCTION_NAMES", "has_function_notation"]
