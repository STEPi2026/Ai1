from __future__ import annotations

import re

from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

# S15 — 허용 문자 정책.
# 문자 자체를 모두 허용하면 parse_expr이 'pi'→Pi, 'oo'→Infinity, 'E'→e, 'I'→I 등
# SymPy 특수 객체로 해석해 'pi=3'이 동치 비교에 들어간다. 공통수학1 MVP 범위에서
# 실제로 쓰는 기호만 명시적으로 허용한다.
#   - 변수: 한 글자 소문자/대문자 중 SymPy 특수 객체가 아닌 것
#   - 함수: sqrt (제곱근은 공통수학1 필수). 삼각/로그는 taxonomy상 future 영역이므로
#           새 문법을 임의로 추가하지 않고 차단한다.
# 숫자·연산자·괄호·공백·'='·','는 기존 그대로 허용한다.
_RESERVED_SYMPY_SINGLE = frozenset("EINOQS")  # E·I·N·O·Q·S 는 특수 객체
_ALLOWED_FUNCTION_NAMES = frozenset({"sqrt"})
_CHARSET = re.compile(r"^[0-9a-zA-Z+\-*/^().,\s=]+$")
_TOKEN = re.compile(r"[A-Za-z]+")


def _token_error(text: str) -> str | None:
    """허용되지 않은 기호가 있으면 사유를 반환한다."""
    for match in _TOKEN.finditer(text):
        token = match.group()
        if len(token) == 1:
            if token in _RESERVED_SYMPY_SINGLE:
                return f"예약 기호 '{token}' 는 사용할 수 없습니다"
            continue
        if token not in _ALLOWED_FUNCTION_NAMES:
            return f"허용되지 않은 기호 '{token}' (단일 변수 또는 {sorted(_ALLOWED_FUNCTION_NAMES)}만 가능)"
    return None


def _match_brace_group(s: str, start: int) -> tuple[str, int] | None:
    """s[start] == '{' 지점에서 중괄호 깊이를 세어 (내용, 닫는 괄호 다음 인덱스)."""
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i + 1
    return None


def _skip_space(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _expand_frac(s: str, start: int = 0) -> str:
    """s[start:] 구간의 \\frac{a}{b} 를 ((a)/(b)) 로 펼쳐 반환한다 (S14).

    중첩 분자(\\frac{\\frac{a}{b}}{c})와 중첩 분모(\\frac{a}{\\frac{b}{c}})를
    재귀로 처리한다. 형태가 깨진 \\frac는 그대로 남기고 다음 \\frac부터 다시
    찾는다(호출자가 문자 집합 검사로 unrecognized 처리).
    재귀 호출은 항상 현재 위치보다 앞으로 전진하므로 무한 재귀가 없다.
    """
    idx = s.find("\\frac", start)
    if idx == -1:
        return s[start:]
    literal_end = _skip_space(s, idx + len("\\frac"))
    group1 = _match_brace_group(s, literal_end)
    if group1 is None:
        return s[idx:literal_end] + _expand_frac(s, literal_end)
    second_at = _skip_space(s, group1[1])
    group2 = _match_brace_group(s, second_at)
    if group2 is None:
        return s[idx:literal_end] + _expand_frac(s, literal_end)
    numerator = _expand_frac(group1[0])
    denominator = _expand_frac(group2[0])
    return (
        s[start:idx]
        + f"(({numerator})/({denominator}))"
        + _expand_frac(s, group2[1])
    )


def normalize(text: str) -> str:
    s = text.strip()
    s = s.replace("$", "").replace("\\left", "").replace("\\right", "")
    s = s.replace("−", "-").replace("–", "-").replace("＝", "=")
    s = s.replace("×", "*").replace("÷", "/")
    s = re.sub(r"\\cdot|\\times", "*", s)
    s = _expand_frac(s)  # S14: brace matching 기반 (중첩 지원)
    s = re.sub(r"\^\{([^{}]*)\}", r"**\1", s)
    s = re.sub(r"\^(-?\d+)", r"**\1", s)
    s = s.replace("{", "(").replace("}", ")")
    return re.sub(r"\s+", "", s)


def _expr(raw: str):
    return parse_expr(raw, transformations=_TRANSFORMS, evaluate=True)


def parse_line(text: str):
    """줄 하나를 파싱한다.

    반환:
      ("eq", lhs, rhs)  등식
      ("expr", e)        등식이 아닌 식
      None               인식 실패 (오답으로 판정하지 않음 — ADR-03)
    """
    s = normalize(text)
    if not s or not _CHARSET.match(s):
        return None
    # S15: 알 수 없는 기호(pi·oo·E 등)는 파싱 전에 차단한다
    token_error = _token_error(s)
    if token_error is not None:
        return None
    if s.count("=") > 1:
        return None
    try:
        if s.count("=") == 1:
            lhs_raw, rhs_raw = s.split("=")
            if not lhs_raw or not rhs_raw:
                return None
            return ("eq", _expr(lhs_raw), _expr(rhs_raw))
        return ("expr", _expr(s))
    except Exception:
        return None  # 인식 실패는 오답이 아님 (ADR-03)


def split_steps(text: str | None) -> list[str]:
    """자유 서술 풀이 텍스트를 단계 목록으로 분리 (1단계: 규칙 기반).

    개행 / 화살표(→, ->, =>) / 번호(1), 2.) 구분자를 모두 단계 구분으로 인정한다.
    소수점 보호: '0.5x+2=7'처럼 점 뒤가 곧바로 숫자로 이어지면 번호로 보지 않는다.
    """
    if not text:
        return []
    parts = re.split(r"[\n\r]+|→|->|=>", text)
    steps: list[str] = []
    for part in parts:
        step = part.strip()
        step = re.sub(r"^\d+\)\s*", "", step)   # '1) 3x=6' 형태
        step = re.sub(r"^\d+\.\s+", "", step)   # '2. 3x=6' 형태 — 점 뒤 공백 필수
        if step:
            steps.append(step)
    return steps
