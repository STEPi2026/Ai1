"""PHASE 9-8B — OCR → Attempt 분석 handoff golden fixture 회귀.

`tests/golden/ocr_handoff.json` 은 **OCR 응답 → Attempt 분석 요청** 변환 규칙과 그
결과(분석 응답)를 고정한다. 규칙(대조표 §14.18)이 바뀌면 fixture 와 함께 갱신한다.

왜 변환 로직을 **여기서 다시 구현**하는가
    `tests/test_ocr_analysis_handoff.py` 의 helper 와 **의존하지 않는다** — 두 구현이
    어긋나면 이 테스트가 즉시 실패한다. fixture 가 "helper 가 자기 자신과 일치한다"만
    검증하는 자기참조가 되지 않도록, 규칙을 Backend 관점에서 독립적으로 다시 적는다.

검증 범위
    - fixture 전 케이스: OCR 호출 → 변환 → 분석 호출 → 고정값 비교 (100% 일치)
    - 케이스 구성(상태 커버리지)이 계약에서 요구한 시나리오를 모두 담는지
    - fixture 와 무관한 독립 불변식 (대표 오류·코드·미인식 보존·멱등)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.integration.ocr import (
    OcrProvider,
    OcrProviderRequest,
    OcrResult,
    register_ocr_provider,
    reset_ocr_provider,
)
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository

client = TestClient(app)

GOLDEN_PATH = Path(__file__).parent / "golden" / "ocr_handoff.json"

#: 미인식 단계 표식 — 대조표 §14.18 규칙
UNREADABLE_MARKER = "???"

#: fixture 에서 고정하는 분석 응답 필드
PINNED_FIELDS = (
    "analysis_status",
    "correct",
    "error_step",
    "error_type",
    "error_subtype",
    "skill",
    "state",
    "misconception_id",
    "misconception_tags",
    "misconception_tag",
    "unrecognized",
    "valid",
    "confidence",
    "official_skill_codes",
    "official_misconception_codes",
    "provisional_skill_codes",
    "provisional_misconception_codes",
)


class FakeOcrProvider(OcrProvider):
    """fixture 의 ocr_result 를 그대로 돌려주는 결정적 provider."""

    def __init__(self, result: OcrResult):
        self.result = result

    def recognize(self, request: OcrProviderRequest) -> OcrResult:
        return self.result


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def ocr_to_analysis_request(ocr_body: dict, context: dict) -> dict | None:
    """대조표 §14.18 handoff 규칙 (Backend 관점 독립 구현).

    호출 금지 조건 3가지
      1. OCR 결과가 ``UNREADABLE``
      2. 검증용 problem LaTeX 없음
      3. **풀이 단계가 0개** — ``solution_text=""`` 요청을 만들지 않는다

    단계가 1개 이상이고 전부 ``"???"`` 면 호출한다 — AI¹ 이 422 UNKNOWN 을
    돌려주도록 맡긴다(요구사항 3).
    raw_text 표현은 problem/solution semantic 분리가 끝나지 않았으므로
    학생/Backend 보정 전에는 호출하지 않는다.
    """
    if ocr_body.get("representation") == "raw_text":
        return None
    if ocr_body["status"] == "UNREADABLE":
        return None
    problem_latex = ocr_body["problem"]["latex_text"]
    if not problem_latex:
        return None
    if len(ocr_body["solution_steps"]) == 0:
        return None

    lines: list[str] = []
    for step in ocr_body["solution_steps"]:
        if step["unreadable"]:
            lines.append(UNREADABLE_MARKER)
        else:
            lines.append(step["latex_text"] or step["text"])

    return {
        "problem_id": context["problem_id"],
        "problem_latex": problem_latex,
        "problem_skills": list(context["problem_skills"]),
        "student_id": context["student_id"],
        "solution_text": "\n".join(lines),
    }


def run_case(case: dict) -> dict:
    """한 case 를 2단계로 실행한다. (Knowledge State 격리는 호출부가 한다)"""
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()

    provider = FakeOcrProvider(OcrResult(**case["ocr_result"]))
    register_ocr_provider(provider)
    ocr_response = client.post(
        "/api/v1/attempts/OH1/ocr",
        json={"image_reference": "https://backend.example/attempts/OH/page1.jpg"},
    )
    assert ocr_response.status_code == 200, ocr_response.text
    ocr_body = ocr_response.json()

    request = ocr_to_analysis_request(ocr_body, case["context"])
    analysis_response = None
    if request is not None:
        analysis_response = client.post("/api/v1/attempts/OH1/analyze", json=request)

    return {
        "ocr_body": ocr_body,
        "request": request,
        "analysis_response": analysis_response,
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("AI1_OCR_PROVIDER", raising=False)
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()
    yield
    analyzer.reset_knowledge()
    reset_repository()
    reset_ocr_provider()


# ===========================================================================
# 1. fixture 자체의 무결성
# ===========================================================================

def test_golden_file_exists_and_has_meta_and_cases():
    data = load_golden()
    assert "meta" in data and "cases" in data
    assert data["meta"]["name"] == "ocr_handoff"
    assert len(data["cases"]) >= 7


def test_golden_case_ids_are_unique():
    ids = [case["id"] for case in load_golden()["cases"]]
    assert len(ids) == len(set(ids))


def test_golden_covers_required_scenarios():
    """계약에서 요구한 시나리오가 모두 fixture 에 담겨 있어야 한다."""
    categories = {case["category"] for case in load_golden()["cases"]}
    assert {
        "complete-correct",
        "complete-concept-error",
        "partial-unreadable-step",
        "unreadable-skip",
        "missing-problem-latex-skip",
        "complete-multi-error",
        "no-readable-step-unknown",
        "complete-text-vs-latex-priority",
        "empty-solution-steps-skip",
    } <= categories


def test_golden_covers_all_three_skip_conditions():
    """호출 금지 3종이 fixture 에 모두 담겨 있어야 한다."""
    categories = {case["category"] for case in load_golden()["cases"]}
    # UNREADABLE / 검증용 problem LaTeX 없음 / 풀이 단계 0개
    assert {
        "unreadable-skip",
        "missing-problem-latex-skip",
        "empty-solution-steps-skip",
    } <= categories
    # 단계 1개 이상이면서 전부 "???" → 호출해서 422 UNKNOWN 을 받는다
    assert "no-readable-step-unknown" in categories


def test_golden_ocr_status_values_are_closed():
    for case in load_golden()["cases"]:
        assert case["expected_request"] is None or case["expected_http_status"] in (
            200,
            422,
        )


# ===========================================================================
# 2. fixture 전체 회귀 (100% 일치)
# ===========================================================================

def test_golden_handoff_all_cases_match():
    """fixture 전 케이스가 고정값과 100% 일치한다."""
    data = load_golden()
    failures: list[str] = []

    for case in data["cases"]:
        outcome = run_case(case)
        label = case["id"]

        # 변환된 분석 요청
        if outcome["request"] != case["expected_request"]:
            failures.append(
                "{}: request 불일치\n  expected={}\n  got     ={}".format(
                    label, case["expected_request"], outcome["request"]
                )
            )
            continue

        # 분석 호출 여부
        if case["expected_request"] is None:
            if outcome["analysis_response"] is not None:
                failures.append("{}: 분석을 호출하지 않아야 한다".format(label))
            continue

        response = outcome["analysis_response"]
        if response.status_code != case["expected_http_status"]:
            failures.append(
                "{}: HTTP {} != {}".format(
                    label, response.status_code, case["expected_http_status"]
                )
            )
            continue

        if case["expected_analysis"] is None:
            continue

        body = response.json()
        expected = case["expected_analysis"]
        for field in PINNED_FIELDS:
            if body.get(field) != expected.get(field):
                failures.append(
                    "{}: {} 불일치 expected={!r} got={!r}".format(
                        label, field, expected.get(field), body.get(field)
                    )
                )
        if len(body["errors"]) != expected["error_count"]:
            failures.append(
                "{}: error_count {} != {}".format(
                    label, len(body["errors"]), expected["error_count"]
                )
            )
        if [e["step_no"] for e in body["errors"]] != expected["error_steps"]:
            failures.append(
                "{}: error_steps {} != {}".format(
                    label,
                    [e["step_no"] for e in body["errors"]],
                    expected["error_steps"],
                )
            )

    assert not failures, "\n".join(failures)


def test_golden_handoff_is_deterministic():
    """같은 fixture 를 두 번 실행해도 결과가 같다 (case 간 상태 누출 없음)."""
    data = load_golden()
    case = data["cases"][1]
    first = run_case(case)
    second = run_case(case)
    assert first["request"] == second["request"]
    assert (
        first["analysis_response"].json()["analysis_id"]
        is not None
        and first["analysis_response"].json()["analysis_status"]
        == second["analysis_response"].json()["analysis_status"]
    )
    assert (
        first["analysis_response"].json()["mastery_by_skill"]
        == second["analysis_response"].json()["mastery_by_skill"]
    )


# ===========================================================================
# 3. fixture 와 무관한 독립 불변식 (자기참조 방지)
# ===========================================================================

def _case(case_id: str) -> dict:
    for case in load_golden()["cases"]:
        if case["id"] == case_id:
            return case
    raise AssertionError("fixture 에 없는 case: " + case_id)


def test_concept_error_case_invariants():
    """분배 오류 케이스 — 대표 오류·Backend 코드·오개념이 일관된다."""
    body = run_case(_case("OH02"))["analysis_response"].json()
    assert body["error_step"] == 2
    assert body["error_type"] == "concept_error"
    assert body["error_subtype"] == "distribution_omit"
    assert body["skill"] == "distribution"
    assert body["state"] == "needs_practice"
    assert body["misconception_id"] == "2.1"
    assert body["misconception_tag"] == "2.1"
    assert body["official_skill_codes"] == ["SKL-1-004"]
    assert body["official_misconception_codes"] == ["MIS-1-004"]
    assert body["provisional_misconception_codes"] == []


def test_unreadable_marker_invariants():
    """??? 단계는 오류가 되지 않고 판정 보류로 남는다."""
    body = run_case(_case("OH03"))["analysis_response"].json()
    assert body["analysis_status"] == "REVIEW_REQUIRED"
    assert body["unrecognized"] == [3]
    assert body["valid"] == [True, False, None]
    # 오류는 판정 가능한 2단계 하나뿐 — ??? 가 만든 오류가 없다
    assert [e["step_no"] for e in body["errors"]] == [2]
    # ??? 때문에 오개념이 새로 붙지 않는다
    assert body["misconception_id"] == "2.1"
    assert body["misconception_tags"] == ["2.1"]


def test_multi_error_invariants():
    """독립 오류 3건 — 대표 오류는 가장 낮은 step_no."""
    body = run_case(_case("OH06"))["analysis_response"].json()
    assert [e["step_no"] for e in body["errors"]] == [2, 4, 6]
    assert body["error_step"] == 2
    assert body["error_type"] == "calculation"
    # 계산 오류에는 오개념이 붙지 않는다
    assert body["misconception_id"] is None
    assert body["misconception_tags"] == []
    assert body["official_misconception_codes"] == []


def test_text_vs_latex_priority_is_pinned():
    """읽힌 step 은 ``latex_text`` 우선, 없으면 ``text`` — fixture 가 이 규칙을 고정한다.

    OH08 은 단계마다 ``text``(평문 OCR) 와 ``latex_text``(정규화 LaTeX) 가 다르다.
    규칙이 뒤집히면 solution_text 가 평문으로 바뀌어 분석 결과까지 달라진다.
    """
    case = _case("OH08")
    steps = case["ocr_result"]["steps"]
    # fixture 자체가 규칙을 드러낼 수 있는 형태인지 확인
    assert steps[0]["text"] != steps[0]["latex_text"]
    assert steps[1]["text"] != steps[1]["latex_text"]
    assert steps[2]["latex_text"] is None and steps[2]["text"]

    outcome = run_case(case)
    request = outcome["request"]
    assert request["solution_text"] == "2(x-3)=6\n2x-6=6\nx=6"
    # LaTeX 우선이면 분석이 정상 통과한다 (평문이면 파싱 불가가 된다)
    assert outcome["analysis_response"].status_code == 200
    assert outcome["analysis_response"].json()["correct"] is True


def test_empty_solution_steps_skip_invariants():
    """풀이 단계 0개 — 분석 요청을 만들지 않고 아무것도 남기지 않는다.

    OCR status 는 PARTIAL 이고 검증용 problem LaTeX 도 있지만, 풀이 단계가 0개면
    ``solution_text=""`` 요청을 만들지 않는다.
    """
    outcome = run_case(_case("OH09"))
    assert outcome["ocr_body"]["status"] == "PARTIAL"
    assert outcome["ocr_body"]["problem"]["latex_text"] == "2(x-3)=6"
    assert outcome["ocr_body"]["solution_steps"] == []
    assert outcome["request"] is None
    assert outcome["analysis_response"] is None
    repo = get_repository()
    assert repo.list_attempts() == []
    assert repo.list_problems() == []
    assert repo.has_analysis("OH1") is False
    # Knowledge State 도 갱신되지 않는다
    rule = analyzer.get_engine("rule")
    assert rule.state("S-OH09", "linear_equation") == "learning"


def test_unreadable_step_still_calls_analyze():
    """단계 1개 이상이면서 전부 "???" — 기존 계약대로 호출해 422 UNKNOWN 을 받는다."""
    outcome = run_case(_case("OH07"))
    assert outcome["request"] is not None
    assert outcome["request"]["solution_text"] == "???"
    response = outcome["analysis_response"]
    assert response.status_code == 422
    assert response.json()["detail"]["analysis_status"] == "UNKNOWN"


def test_unreadable_ocr_still_skips():
    """UNREADABLE — 분석 요청을 만들지 않는다."""
    outcome = run_case(_case("OH04"))
    assert outcome["ocr_body"]["status"] == "UNREADABLE"
    assert outcome["request"] is None
    assert outcome["analysis_response"] is None


def test_skip_cases_store_nothing():
    """분석 호출 금지 케이스는 Attempt/분석을 남기지 않는다."""
    for case_id in ("OH04", "OH05", "OH09"):
        outcome = run_case(_case(case_id))
        assert outcome["request"] is None, case_id
        assert outcome["analysis_response"] is None, case_id
        repo = get_repository()
        assert repo.list_attempts() == [], case_id
        assert repo.has_analysis("OH1") is False, case_id


def test_unknown_case_stores_nothing():
    """읽힌 단계가 없으면 422 UNKNOWN 이고 아무것도 저장하지 않는다."""
    outcome = run_case(_case("OH07"))
    response = outcome["analysis_response"]
    assert response.status_code == 422
    assert response.json()["detail"]["analysis_status"] == "UNKNOWN"
    repo = get_repository()
    assert repo.has_analysis("OH1") is False
    assert repo.list_attempts() == []


def test_ocr_confidence_is_independent_of_analysis_confidence():
    """OCR 신뢰도와 분류 신뢰도는 섞이지 않는다."""
    outcome = run_case(_case("OH03"))
    ocr_body = outcome["ocr_body"]
    analysis_body = outcome["analysis_response"].json()
    assert "ocr_confidence" in ocr_body and "confidence" not in ocr_body
    assert "confidence" in analysis_body and "ocr_confidence" not in analysis_body
    assert ocr_body["ocr_confidence"] != analysis_body["confidence"]


def test_golden_does_not_modify_existing_golden_files():
    """신규 fixture 는 추가일 뿐 기존 golden 은 건드리지 않는다."""
    for name in ("golden_20.json", "golden_100.json"):
        path = Path(__file__).parent / "golden" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["cases"], name
        assert "ocr_result" not in data["cases"][0], name
