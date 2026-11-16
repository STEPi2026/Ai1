"""A6 P1 — ``target_form`` 판정 (계약 §4).

형태는 문자열 모양이 아니라 **의미 구조**로 판정한다.

    integer  : 자유 기호가 없다            (12, -3/4 → O / 2x → X)
    expanded : 괄호/미전개 곱이 남아 있지 않다 (x^2+3x+2 → O / (x+1)(x+2) → X)
    factored : 상수가 아닌 인수의 곱이 2개 이상 (x(x+2) → O / x^2+2x → X)

``factored`` 는 느슨한 정책을 기본으로 한다(계약 §4.1). 완전 인수분해 강제
(``factored_irreducible``)는 값 추가 합의가 필요하므로 여기서 만들지 않는다.
"""
from __future__ import annotations

from sympy import Add

#: 허용되는 target_form 값. 새 값은 Backend·AI① 합의 후에만 추가한다.
ALLOWED_TARGET_FORMS = frozenset({"integer", "expanded", "factored"})


def _is_expanded(expr) -> bool:
    """괄호 안에 Add 가 남아 있지 않으면 전개된 형태다.

    ``expand(expr) - expr == 0`` 은 수학적 동치라서 항상 참이 되어 쓸 수 없다.
    구조를 봐야 한다 — 최상위가 아닌 Add 가 하나라도 있으면 미전개다.

    주의: SymPy 는 ``2*(x+1)`` 을 파싱 단계에서 ``2*x+2`` 로 자동 분배한다.
    형태 판정은 파싱된 식을 보므로 이 입력은 전개형으로 판정된다(의미 구조 기준).
    """
    return all(atom == expr for atom in expr.atoms(Add))


def _is_factored(expr) -> bool:
    """상수가 아닌 인수가 2개 이상인 곱(느슨한 인수분해)인가."""
    if expr.is_Mul:
        non_constant = [factor for factor in expr.args if factor.free_symbols]
        return len(non_constant) >= 2
    # (x+1)^2 처럼 반복 인수를 거듭제곱으로 쓴 경우도 인수분해 형태로 본다.
    if expr.is_Pow:
        exponent = expr.exp
        return bool(
            expr.base.free_symbols
            and getattr(exponent, "is_Integer", False)
            and exponent >= 2
        )
    return False


def form_matches(expr, target_form: str) -> bool:
    """식이 요구 형태를 만족하는가. 허용되지 않은 값은 호출 전에 걸러야 한다."""
    if target_form == "integer":
        return not expr.free_symbols
    if target_form == "expanded":
        return _is_expanded(expr)
    if target_form == "factored":
        return _is_factored(expr)
    raise ValueError(f"알 수 없는 target_form: '{target_form}'")


__all__ = ["ALLOWED_TARGET_FORMS", "form_matches"]
