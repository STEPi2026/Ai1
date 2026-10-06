from __future__ import annotations

from dataclasses import dataclass, field

from sympy import Eq, Poly, count_ops, simplify, solve

from .parser import parse_line

# solve() 복잡도 가드 — 고차/비다항식은 풀이 시도 자체를 보류한다.
# 기획서 범위는 중학 방정식(최고 이차)이므로 여유 상한 4차.
# (검토 시 발견: x^99=1 입력이 solve()에서 무기한 hang → 판정 보류로 전환)
_MAX_DEGREE = 4
_MAX_OPS_NONPOLY = 300
# S2: 차수만으로 통과시키면 저차식도 solve() 비용이 급증한다(2차식 200단계 13초).
# 연산량/항 개수를 함께 검사해 '빠르지만 무거운' 입력을 걸러낸다.
_MAX_OPS = 120
_MAX_TERMS = 40
# S2: 요청 1건당 solve() 호출 횟수 예산. SymPy solve()는 실행 중 인터럽트가
# 어려워 wall-clock timeout 대신 결정론적 횟수 예산을 채택했다.
# 예산 소진 시 이후 비교는 False가 아니라 None(검증 불가)로 남긴다 (ADR-03).
# _MAX_STEPS(30) × 단계당 2회 = 60회에 여유를 둔다.
_MAX_SOLVE_CALLS = 80


@dataclass
class Verdict:
    steps: list[str]
    parsed: list
    valid: list[bool | None]     # steps[i]가 steps[i-1]로부터 유효한지 (None = 판정 보류)
    first_error_index: int | None  # 0-based
    unrecognized: list[int]      # 1-based 줄 번호
    # steps[i]를 대조한 기준 줄 번호(1-based). None이면 문제식이 기준이다.
    # Phase 6의 Error.ref_step_no가 이 값을 사용한다 (additive).
    ref_step_no: list[int | None] = field(default_factory=list)


def _free_symbols(*nodes) -> set:
    syms = set()
    for node in nodes:
        if node is None:
            continue
        if node[0] == "eq":
            syms |= node[1].free_symbols | node[2].free_symbols
        else:
            syms |= node[1].free_symbols
    return syms


def _too_complex(expr) -> bool:
    """solve() 호출 전 복잡도 검사. 과다하면 True (판정 보류 사유).

    S2 수정: 기존에는 다항식 차수가 _MAX_DEGREE 이하면 즉시 반환해
    count_ops 검사에 도달하지 못했다(가드가 사실상 무력). 이제 연산량과
    항 개수를 함께 검사한다.
    """
    if count_ops(expr) > _MAX_OPS:
        return True
    syms = sorted(expr.free_symbols, key=str)
    if not syms:
        return False
    try:
        poly = Poly(expr, *syms)
    except Exception:
        return count_ops(expr) > _MAX_OPS_NONPOLY  # 비다항식(분수·근 등)
    if poly.degree() > _MAX_DEGREE:
        return True
    return len(poly.terms()) > _MAX_TERMS


def _node_too_complex(node) -> bool:
    if node is None:
        return False
    if node[0] == "eq":
        try:
            return _too_complex(node[1] - node[2])
        except Exception:
            return True
    return _too_complex(node[1])


class _SolveBudget:
    """verify() 1회 호출에 대한 solve() 호출 횟수 예산 (S2).

    소진 이후의 비교는 False가 아니라 None(검정 불가)이 되어,
    예산 초과가 오답으로 오판되는 일이 없도록 한다 (ADR-03).
    """

    def __init__(self, limit: int = _MAX_SOLVE_CALLS) -> None:
        self.limit = limit
        self.used = 0

    def take(self, count: int = 1) -> bool:
        if self.used + count > self.limit:
            return False
        self.used += count
        return True


def _is_nonzero_multiple(a, b) -> bool:
    """a가 b의 '0이 아닌 상수배'인가 — 등식 동치의 충분조건 (S1).

    잔차가 서로 상수배이면 해 집합이 반드시 같으므로 solve() 없이 True를 확정한다.
    True만 반환하고 False를 반환하지 않는다: 실패 시 기존 solve 경로로 위임해
    1변수 판정(예: x^2=0 ↔ x=0 동치, x^2=16 ↮ x=4 비동치)을 그대로 보존한다.
    """
    try:
        if a == 0 or b == 0:
            return False
        ratio = simplify(a / b)
        if ratio.free_symbols:  # 아직 상수가 아님
            return False
        if ratio == 0:
            return False
        return ratio.is_real is not False
    except Exception:
        return False


def _normalize_solutions(sols) -> tuple[frozenset, bool] | None:
    """solve(..., dict=True) 결과를 비교 가능한 형태로 정규화.

    반환: (정규화한 해 집합, 매개변수 해 포함 여부). 정규화 불가 시 None.
    """
    normalized: set[tuple[tuple[str, str], ...]] = set()
    parametric = False
    for sol in sols:
        if not isinstance(sol, dict):
            return None
        items: list[tuple[str, str]] = []
        for sym, val in sol.items():
            try:
                val = simplify(val)
            except Exception:
                return None
            if val.free_symbols:
                parametric = True
            items.append((str(sym), str(val)))
        normalized.add(tuple(sorted(items)))
    return frozenset(normalized), parametric


def _multivariate_equivalent(a, b, syms, budget: _SolveBudget | None) -> bool | None:
    """다변수 등식 동치 판정 (S1).

    기존 잔차 차이 비교는 0이 아닌 상수배를 구분하지 못해
    'x+y=3 ↔ 2x+2y=6'을 비동치로 오판했다. 시스템 해 집합을 비교한다.
    미해석·조건부 결과는 추측하지 않고 None으로 남긴다 (ADR-03).
    """
    if budget is not None and not budget.take(2):
        return None
    try:
        sa = solve([Eq(a[1], a[2])], syms, dict=True)
        sb = solve([Eq(b[1], b[2])], syms, dict=True)
    except Exception:
        return None
    if not isinstance(sa, list) or not isinstance(sb, list):
        return None
    if not sa and not sb:
        return True  # 둘 다 해 없음(모순 등) → 동치
    if not sa or not sb:
        return False  # 한쪽만 해 없음 → 확정적 비동치
    na = _normalize_solutions(sa)
    nb = _normalize_solutions(sb)
    if na is None or nb is None:
        return None
    if na[0] == nb[0]:
        return True
    if na[1] or nb[1]:
        return None  # 매개변수 해의 파라미터 표현이 달라 단순 비교 불가
    return False


def _same_solution_set(a, b) -> bool:
    if len(a) != len(b):
        return False
    return all(any(simplify(x - y) == 0 for y in b) for x in a)


def equivalent(a, b, budget: _SolveBudget | None = None) -> bool | None:
    """두 단계가 수학적으로 동치인지.

    등식 쌍이면 동치 판정, 식 쌍이면 잔차 비교.
    판정할 수 없으면 None (오답으로 단정하지 않음 — ADR-03).

    S1: ① 잔차가 '0이 아닌 상수배'면 solve() 없이 True를 확정하고(fast path)
        ② 다변수는 시스템 해 집합을 비교한다. fast path가 False를 반환하지
        않으므로 1변수 기존 판정은 원천적으로 보존된다.
        (x^2=0 ↔ x=0 은 계속 동치, x^2=16 ↮ x=4 는 계속 비동치)
    """
    if a is None or b is None:
        return None
    if a[0] == "eq" and b[0] == "eq":
        syms = sorted(_free_symbols(a, b), key=str)
        if not syms:
            # 변수 없는 등식(모순 등)은 기존 잔차 비교 동작을 유지한다
            try:
                return simplify((a[1] - a[2]) - (b[1] - b[2])) == 0
            except Exception:
                return None
        if _node_too_complex(a) or _node_too_complex(b):
            return None  # solve()가 과도하게 오래 걸리는 입력 — 추측하지 않고 보류
        if _is_nonzero_multiple(a[1] - a[2], b[1] - b[2]):
            return True
        if len(syms) == 1:
            if budget is not None and not budget.take(2):
                return None
            try:
                return _same_solution_set(
                    solve(Eq(a[1], a[2]), syms[0]),
                    solve(Eq(b[1], b[2]), syms[0]),
                )
            except Exception:
                # solve() 실패 시에도 '잔차가 동일'은 동치의 충분조건이다.
                # (비충분조건인 잔차 '차이' 비교로 False를 만들지 않는다)
                try:
                    return simplify((a[1] - a[2]) - (b[1] - b[2])) == 0
                except Exception:
                    return None
        return _multivariate_equivalent(a, b, syms, budget)
    if a[0] == "expr" and b[0] == "expr":
        try:
            if _too_complex(a[1]) or _too_complex(b[1]):
                return None
            return simplify(a[1] - b[1]) == 0
        except Exception:
            return None
    return None  # 등식/식 혼합 — MVP에서는 보류


def verify(problem_latex: str, steps: list[str]) -> Verdict:
    """단계별 동치 판정 → valid[], 첫 오류 줄, 인식 실패 줄.

    - 등식이 아닌 줄(식 단독)과 파싱 실패 줄은 모두 unrecognized로 분리한다 (ADR-03).
    - 인식 실패 줄은 판정에서 제외하되, 그 뒤의 인식 성공 줄은 '바로 앞의 판정
      가능 줄'과 대조한다. 안 그러면 인식 실패 뒤의 오류가 통째로 누락된다.
    """
    if not steps:
        return Verdict(steps=[], parsed=[], valid=[], first_error_index=None, unrecognized=[])

    raw = [parse_line(s) for s in steps]
    problem = parse_line(problem_latex)
    if problem is not None and problem[0] != "eq":
        problem = None  # 문제식은 등식이어야 판정 가능

    # 등식만 판정 대상 — 식 단독 줄은 판정 불가로 간주 (unrecognized 포함)
    parsed = [p if (p is not None and p[0] == "eq") else None for p in raw]
    unrecognized = [i + 1 for i, p in enumerate(parsed) if p is None]

    budget = _SolveBudget()  # S2: 요청 1건당 solve() 호출 횟수 상한

    def _compare(i: int) -> tuple[bool | None, int | None]:
        """(동치 판정, 대조 기준 줄 번호 1-based). 기준이 없으면 None(문제식)."""
        if parsed[i] is None:
            return None, None
        j = i - 1
        while j >= 0 and parsed[j] is None:
            j -= 1  # 인식 실패 줄 건너뛰고 이전 판정 가능 줄과 대조
        if j >= 0:
            return equivalent(parsed[j], parsed[i], budget), j + 1
        if problem is None:
            return None, None
        return equivalent(problem, parsed[i], budget), None

    comparisons = [_compare(i) for i in range(len(steps))]
    valid = [v for v, _ in comparisons]
    ref_step_no = [r for _, r in comparisons]
    first_error = next((i for i, v in enumerate(valid) if v is False), None)
    return Verdict(
        steps=steps,
        parsed=parsed,
        valid=valid,
        first_error_index=first_error,
        unrecognized=unrecognized,
        ref_step_no=ref_step_no,
    )
