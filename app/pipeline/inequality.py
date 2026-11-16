"""A6 P1 — 부등식 판정 (계약 §B1).

부등식은 P1 범위 밖이다. Backend 는 부등식일 때 ``problem_type`` 을 생략하고,
AI① 은 부등호가 있으면 분석하지 않고 ``E_UNSUPPORTED_INEQUALITY`` 로 거절한다.

**경계 매칭이 핵심이다.** 단순 문자열 포함 검사는 아래를 오탐한다.
    ``\\left(``   → ``\\le`` 포함
    ``\\gets``    → ``\\ge`` 포함
    ``\\ltimes``  → ``\\lt`` 포함
따라서 LaTeX 명령은 뒤에 영문자가 오지 않는 토큰 경계로만 매칭한다.
"""
from __future__ import annotations

import re

#: v1 공통 목록 (Backend 합의). 순서는 매칭에 영향을 주지 않는다.
INEQUALITY_SYMBOLS = ("<", ">", "≤", "≥", "≠", "≦", "≧")
INEQUALITY_COMMANDS = (
    "lt",
    "gt",
    "le",
    "leq",
    "ge",
    "geq",
    "ne",
    "neq",
    "leqslant",
    "geqslant",
)

# 긴 명령을 먼저 두고, 뒤에 영문자가 오면 다른 명령(예: \leqslant, \ltimes)으로 본다.
_INEQUALITY_RE = re.compile(
    r"\\(?:"
    + "|".join(sorted(INEQUALITY_COMMANDS, key=len, reverse=True))
    + r")(?![A-Za-z])"
    + r"|["
    + "".join(re.escape(s) for s in INEQUALITY_SYMBOLS)
    + r"]"
)

_SPACING_COMMANDS = re.compile(r"\\(?:;|,|!|:)")
_EQ_DECORATION = re.compile(r"\\(?:mathrm|text)\{\s*=\s*\}")


def normalize_for_detection(latex: str) -> str:
    """계약 §2.1 정규화 — 판정용 최소 정규화.

    수식 파싱용 ``parser.normalize`` 와 달리 식을 변형하지 않는다.
    여기서는 부등호 존재 여부만 보므로 ``\\left``/``\\right`` 제거와
    공백·간격 명령 제거만 한다.
    """
    s = latex.replace("$", "")
    s = re.sub(r"\\left|\\right", "", s)
    s = _SPACING_COMMANDS.sub("", s)
    s = _EQ_DECORATION.sub("=", s)
    s = s.replace("＝", "=")
    return re.sub(r"\s+", "", s)


def has_inequality_operator(latex: str) -> bool:
    """정규화 후 부등호 토큰이 하나라도 있으면 True."""
    if not latex:
        return False
    return _INEQUALITY_RE.search(normalize_for_detection(latex)) is not None


__all__ = [
    "INEQUALITY_COMMANDS",
    "INEQUALITY_SYMBOLS",
    "has_inequality_operator",
    "normalize_for_detection",
]
