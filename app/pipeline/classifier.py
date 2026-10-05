from __future__ import annotations

import re
from dataclasses import dataclass

from sympy import Add, simplify

from app.taxonomy import related_skill_ids, skill_by_id

from .parser import normalize, parse_line

# 오개념 트리 node_code는 frozen taxonomy(data/misconceptions.json)가
# source of truth다. 아래 값은 기존 코드 호환을 위해 유지하는 별칭이며,
# 실제 ID 유효성은 app.taxonomy 조회로 확인한다.
MISCONCEPTION_DISTRIBUTION = "2.1"   # 분배법칙 누락
MISCONCEPTION_TRANSPOSITION = "3.1"  # 이항 시 부호 미변경

# 기존 4개 skill은 응답 라벨을 그대로 유지한다(backward compatibility).
# taxonomy에 존재하지 않으면 아래 모듈 임포트 시점에 실패한다(fail fast).
SKILL_DISTRIBUTION = "distribution"
SKILL_TRANSPOSITION = "transposition"
SKILL_COMPREHENSION = "linear_equation"
SKILL_CALCULATION = "arithmetic"

# 좌변의 '계수(식)' 패턴 — 계수가 생략된 (x+1) 형태도 매칭 (factor=1로 간주)
_PAREN_FACTOR_RE = re.compile(r"(-?\d*)\*?\(([^()]+)\)")


@dataclass
class ErrorInfo:
    error_type: str   # calculation | concept_error | procedure | comprehension
    skill: str        # 기존 단일 primary skill (backward-compatible)
    misconception_id: str | None
    confidence: float
    # --- additive (S3) ---
    error_subtype: str = ""
    skill_ids: tuple[str, ...] = ()
    misconception_ids: tuple[str, ...] = ()


# 오류 유형 → taxonomy 바인딩. (error_type, error_subtype, misconception_id)
# 모든 값은 taxonomy에 정의된 ID만 사용하며, 유효성은
# tests/test_taxonomy_runtime.py가 고정한다.
# misconception은 실제 근거가 있을 때만 연결한다 — 단순 계산 오류는 빈 목록.
_TAXONOMY_BINDING: dict[str, tuple[str, str, str | None]] = {
    "distribution": ("concept_error", "distribution_omit", MISCONCEPTION_DISTRIBUTION),
    "transposition": ("concept_error", "sign_not_flipped", MISCONCEPTION_TRANSPOSITION),
    "comprehension": ("comprehension", "misread_problem", None),
    "calculation": ("calculation", "arithmetic_slip", None),
}
_SKILL_BY_KIND: dict[str, str] = {
    "distribution": SKILL_DISTRIBUTION,
    "transposition": SKILL_TRANSPOSITION,
    "comprehension": SKILL_COMPREHENSION,
    "calculation": SKILL_CALCULATION,
}
_CONFIDENCE_BY_KIND: dict[str, float] = {
    "distribution": 0.9,
    "transposition": 0.9,
    "comprehension": 0.85,
    "calculation": 0.75,
}

# 기존 4개 skill이 taxonomy에 실제로 존재하는지 import 시점에 확인한다.
# 하나라도 없으면 여기서 ImportError가 나므로 taxonomy↔코드 불일치가 조용히
# 번지는 일이 없다.
for _sid in _SKILL_BY_KIND.values():
    skill_by_id(_sid)
del _sid


def _error_info(kind: str) -> ErrorInfo:
    error_type, error_subtype, misconception_id = _TAXONOMY_BINDING[kind]
    skill = _SKILL_BY_KIND[kind]
    # skill_ids는 taxonomy의 related_skill_ids를 따른다(개념적 분류).
    # 오개념 근거가 없으면 primary skill 1개만 둔다.
    if misconception_id:
        skill_ids = tuple(related_skill_ids(misconception_id))
        misconception_ids: tuple[str, ...] = (misconception_id,)
    else:
        skill_ids = (skill,)
        misconception_ids = ()
    return ErrorInfo(
        error_type=error_type,
        skill=skill,
        misconception_id=misconception_id,
        confidence=_CONFIDENCE_BY_KIND[kind],
        error_subtype=error_subtype,
        skill_ids=skill_ids,
        misconception_ids=misconception_ids,
    )


def _split_first_top_level(inner: str) -> tuple[str, str] | None:
    depth = 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in "+-" and i > 0 and depth == 0:
            return inner[:i], inner[i:]
    return None


def _distribution_error_evidence(prev_raw: str, prev, cur) -> tuple | None:
    """좌변의 어느 괄호에서 분배 오류가 났는지 반환한다. 없으면 None.

    반환값: (start, end, factor, inner, variant_kind)

    대표 오류 2종을 인식한다:
      1) 상수항 분배 누락: a(b+c) → ab + c
      2) 부호 오류:        a(b-c) → ab + c

    S6: 좌변에 괄호가 여러 개 있어도 전부 검사한다. search()는 첫 매치만
    반환해 '2(x-1)+3(x-1) → 2(x-1)+3x-1' 같은 뒤쪽 괄호의 오류를 놓쳤다.
    finditer()로 모든 후보를 순회하되, 한 그룹씩만 치환해 비교하기 때문에
    '괄호가 여러 개다'와 무관하게 실제 등치 증거가 있을 때만 True가 된다.
    """
    if cur is None or cur[0] != "eq" or prev is None or prev[0] != "eq":
        return None
    try:
        if simplify(cur[2] - prev[2]) != 0:
            return None  # 우변까지 바뀐 단계는 분배 오류로 보지 않음
    except Exception:
        return None

    s = normalize(prev_raw)
    if s.count("=") != 1:
        return None
    lhs_raw = s.split("=", 1)[0]
    for m in _PAREN_FACTOR_RE.finditer(lhs_raw):
        factor_str = m.group(1) or "1"
        if factor_str == "-":
            factor_str = "-1"
        inner = m.group(2)
        split = _split_first_top_level(inner)
        if not split:
            continue
        first_term, rest = split
        variants = (
            (f"{factor_str}*({first_term})+({rest})", "omit_constant"),
            (f"{factor_str}*({first_term})-({rest})", "sign_error"),
        )
        for wrong, kind in variants:
            # 해당 괄호 하나만 치환해 비교 → 다른 그룹의 등치에는 영향이 없다
            wrong_lhs_raw = lhs_raw[: m.start()] + wrong + lhs_raw[m.end():]
            parsed = parse_line(wrong_lhs_raw)  # '=' 없음 → 식으로 파싱됨
            if parsed is None or parsed[0] != "expr":
                continue
            try:
                if simplify(cur[1] - parsed[1]) == 0:
                    return (m.start(), m.end(), factor_str, inner, kind)
            except Exception:
                continue
    return None


def _is_distribution_error(prev_raw: str, prev, cur) -> bool:
    return _distribution_error_evidence(prev_raw, prev, cur) is not None


def _is_removal_of(delta, expr) -> bool:
    """delta가 expr에서 항 하나를 '제거'(-t)한 결과인지 — 항 누락 판별용.

    +t 방향(기존 항에 같은 항을 더함)은 제외한다. 안 그러면 한쪽에만
    연산을 적용한 오류(예: 0.5x=5 → x=5)가 항 누락으로 오탐된다.
    """
    try:
        for t in Add.make_args(simplify(expr)):
            if simplify(delta + t) == 0:
                return True
    except Exception:
        return False
    return False


def _is_transposition_error(prev, cur) -> bool:
    """이항 계열 오류 — 부호 미변경(합 0) 또는 항 누락(한쪽만 변화)."""
    if prev is None or cur is None or prev[0] != "eq" or cur[0] != "eq":
        return False
    try:
        d_lhs = simplify(cur[1] - prev[1])
        d_rhs = simplify(cur[2] - prev[2])
    except Exception:
        return False
    if d_lhs == 0 and d_rhs == 0:
        return False
    if simplify(d_lhs + d_rhs) == 0:
        return True  # 부호 미변경: 한쪽에서 뺀 만큼 반대쪽에 더함
    # 항 누락: 한쪽에서 제거한 항이 반대쪽에 반영되지 않음
    if d_rhs == 0 and _is_removal_of(d_lhs, prev[1]):
        return True
    if d_lhs == 0 and _is_removal_of(d_rhs, prev[2]):
        return True
    return False


def _eq_signature(node, syms) -> list:
    """등식의 계수 서명: [변수별 계수(좌), 상수(좌), 변수별 계수(우), 상수(우)]."""
    lhs, rhs = node[1], node[2]
    zero = {s: 0 for s in syms}
    sig = []
    for s in syms:
        sig.append(lhs.coeff(s))
    sig.append(lhs.subs(zero))
    for s in syms:
        sig.append(rhs.coeff(s))
    sig.append(rhs.subs(zero))
    return sig


def _is_comprehension_error(problem, cur, error_index: int) -> bool:
    """첫 줄 전사/오독 — 문제식과 구조가 같고 상수 하나만 다르면 True."""
    if error_index != 0:
        return False
    if problem is None or cur is None or problem[0] != "eq" or cur[0] != "eq":
        return False
    try:
        syms = sorted(
            problem[1].free_symbols
            | problem[2].free_symbols
            | cur[1].free_symbols
            | cur[2].free_symbols,
            key=str,
        )
        if not syms:
            return False
        sp = _eq_signature(problem, syms)
        sc = _eq_signature(cur, syms)
        return sum(1 for a, b in zip(sp, sc) if simplify(a - b) != 0) == 1
    except Exception:
        return False


def classify(problem_latex: str, steps: list[str], error_index: int) -> ErrorInfo:
    """오류 단계를 오개념 트리에 연결한다 (자유 생성 금지 — ADR-02).

    판정 우선순위: 분배법칙 > 이항 부호 > 문제 오독 > 계산.
    기준선은 '바로 앞의 판정 가능 줄'이다 — 인식 실패 줄은 건너뛴다 (ADR-03).
    반환되는 모든 ID는 frozen taxonomy에 존재하며, mapping은
    _TAXONOMY_BINDING 하나를 통해서만 만들어진다.
    """
    problem = parse_line(problem_latex)
    cur = parse_line(steps[error_index])

    prev_raw, prev = problem_latex, problem
    for j in range(error_index - 1, -1, -1):
        p = parse_line(steps[j])
        if p is not None and p[0] == "eq":
            prev_raw, prev = steps[j], p
            break

    if _is_distribution_error(prev_raw, prev, cur):
        return _error_info("distribution")
    if _is_transposition_error(prev, cur):
        return _error_info("transposition")
    if _is_comprehension_error(problem, cur, error_index):
        return _error_info("comprehension")
    return _error_info("calculation")
