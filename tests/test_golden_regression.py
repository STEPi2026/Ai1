"""Golden Dataset 회귀 테스트 — 테스트 데이터가 개발의 기준점 (Step 5).

게이트: error_step 정확도 ≥ 0.85, error_type 정확도 ≥ 0.85 (M2 목표)

S8 수정: 이전 구조는 (1) 정확도 집계와 (2) 실패 목록 수집을 서로 다른 루프에서
돌려 case를 사실상 2회 실행했고, 그 사이 같은 student_id로 Knowledge State가
누적됐다. 그 결과 state 비교에서 44건의 허위 mismatch가 보고되었다.
지금은 case마다 상태를 초기화하고 analyze를 1회만 실행한 결과를 재사용한다.
- Knowledge State는 reset_knowledge()로 case 단위 격리한다 (production 구조는 변경 없음)
- golden expected 값은 수정하지 않았다
- 실행 순서·반복 실행에 대해 결과가 동일함을 별도 테스트로 고정한다
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from app.pipeline.analyzer import analyze, reset_knowledge

GOLDEN_PATH = Path(__file__).parent / "golden" / "golden_100.json"

FIELDS = ("correct", "error_step", "error_type", "skill", "state")
GATE_FIELDS = ("error_step", "error_type")
NON_GATE_FIELDS = ("correct", "skill", "state")


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _run_case(case: dict) -> dict:
    """case 하나를 Knowledge State 격리 상태에서 정확히 1회 실행한다."""
    reset_knowledge()
    return analyze(
        problem_latex=case["problem_latex"],
        solution_text=case["solution_text"],
        student_id=f"golden-{case['id']}",
        engine="rule",
    )


def _run_all(cases: list[dict]) -> dict[str, dict]:
    """{case_id: 실행결과} — 순서에 관계없이 동일한 결과를 낸다."""
    return {case["id"]: _run_case(case) for case in cases}


def _compare(cases: list[dict], results: dict[str, dict]) -> tuple[dict, list]:
    """(필드별 hit 수, 실패 목록) — 실행 결과를 재사용하므로 추가 실행이 없다."""
    hits = {f: 0 for f in FIELDS}
    failures: list = []
    for case in cases:
        got = results[case["id"]]
        diff = {f: (case["expected"][f], got[f]) for f in FIELDS if got[f] != case["expected"][f]}
        if diff:
            failures.append((case["id"], diff))
        else:
            for f in FIELDS:
                hits[f] += 1
    return hits, failures


@pytest.fixture(scope="module")
def cases_and_results():
    """module 범위에서 1회만 실행하고 결과를 재사용한다."""
    data = load_golden()
    cases = data["cases"]
    results = _run_all(cases)
    yield cases, results
    reset_knowledge()


def test_golden_gate(cases_and_results):
    cases, results = cases_and_results
    data = load_golden()
    total = len(cases)
    assert total >= 100, "Golden 100건 미만"

    hits, failures = _compare(cases, results)
    acc = {f: h / total for f, h in hits.items()}
    gate = data["meta"]["gate"]

    for f in GATE_FIELDS:
        assert acc[f] >= gate[f"{'step' if f == 'error_step' else 'error_type'}_accuracy"], (
            f"{f} accuracy 미달: {acc}\n실패: {failures}"
        )
    for f in NON_GATE_FIELDS:
        assert acc[f] >= 0.85, f"{f} accuracy 미달: {acc}\n실패: {failures}"


def test_golden_fields_are_perfect(cases_and_results):
    """현재 golden은 5필드 모두 정확도 1.0이어야 한다 (S8로 정확도가 떨어지지 않았음)."""
    cases, results = cases_and_results
    hits, failures = _compare(cases, results)
    total = len(cases)
    acc = {f: h / total for f, h in hits.items()}
    assert all(v == 1.0 for v in acc.values()), f"정확도 하락: {acc}\n실패: {failures}"


def test_no_false_state_mismatch(cases_and_results):
    """S8 핵심: state 허위 mismatch가 0건이어야 한다.

    이전 구현은 같은 student_id를 재사용해 2회 실행하면서 44건이 보고됐다.
    """
    cases, results = cases_and_results
    _, failures = _compare(cases, results)
    state_failures = [(cid, d) for cid, d in failures if "state" in d]
    assert state_failures == [], f"state 허위 mismatch {len(state_failures)}건: {state_failures}"


def test_each_case_runs_from_isolated_state(cases_and_results):
    """case 단위 격리 확인: 단독 실행과 전체 실행의 결과가 동일하다."""
    cases, results = cases_and_results
    for case in cases[:10]:
        solo = _run_case(case)
        for f in FIELDS:
            assert solo[f] == results[case["id"]][f], (
                f"{case['id']} 의 {f} 가 전체 실행과 solo 실행에서 다르다"
            )


def test_results_are_order_independent():
    """case 순서를 바꿔도 결과가 동일해야 한다."""
    cases = load_golden()["cases"]
    baseline = _run_all(cases)
    shuffled = list(cases)
    random.Random(20260928).shuffle(shuffled)
    assert _run_all(shuffled) == baseline, "실행 순서가 결과에 영향을 준다"
    reset_knowledge()


def test_results_are_repeatable():
    """동일 입력을 반복 실행해도 결과가 동일해야 한다."""
    cases = load_golden()["cases"]
    first = _run_all(cases)
    second = _run_all(cases)
    assert first == second, "반복 실행에서 결과가 달라졌다"
    reset_knowledge()


def test_golden_categories_covered():
    data = load_golden()
    cats = {c["category"] for c in data["cases"]}
    assert {"correct", "calculation", "distribution", "transposition", "comprehension"} <= cats
