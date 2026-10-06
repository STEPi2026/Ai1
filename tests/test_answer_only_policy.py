"""S7 정책 고정 — 과정 없는 정답 단독 제출.

확정 정책(이번 단계에서 동작 변경 없음):
    correct=true는 '정답이 수학적으로 완전히 검증되었다'는 뜻이 아니라
    '현재 입력된 풀이에서 오류가 검출되지 않았다'는 뜻이다.
    따라서 최종 답만 적어도 correct=true를 허용한다.

이 정책은 향후 변경될 수 있으므로, 변경 시 아래 테스트가 즉시 실패해야 한다.
테스트 이름에 '정답만 제출' 정책 의도를 명시해 두었다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyzer import analyze, get_engine, reset_knowledge
from app.schemas import AnalyzeSolutionResponse

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


# ---------------------------------------------------------------------------
# 정책: 과정 없는 정답 단독 제출은 correct=true (설계 의도)
# ---------------------------------------------------------------------------

def test_answer_only_submission_is_correct_by_design_not_a_bug():
    """정책 고정: 정답만 제출(단계 생략)해도 correct=true — 오류 미검출 의미.

    problem = 2x=6, solution = x=3 (풀이 과정 없음)
    """
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": "2x=6",
            "solution_text": "x=3",
            "student_id": "s7-answer-only",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is True
    assert body["error_step"] is None
    assert body["error_type"] is None
    assert body["misconception_id"] is None
    assert body["misconception_ids"] == []
    # 판정은 수행되었고(문제식과 대조) 오류가 없었을 뿐이다
    assert body["steps_latex"] == ["x=3"]
    assert body["valid"] == [True]
    assert body["unrecognized"] == []


def test_answer_only_updates_knowledge_state_as_correct():
    """정책 고정: 단계 생략도 Knowledge State 갱신 입력은 정답으로 본다.

    이번 단계에서 상태 상승 동작은 변경하지 않는다.
    """
    analyze("2x=6", "x=3", student_id="s7-kb")
    assert ("s7-kb", "linear_equation") in get_engine("rule")._state


def test_answer_only_with_wrong_answer_is_still_detected_as_error():
    """정답 단독 제출이 허용되는 것은 '오류 미검출'일 때뿐이다.

    x=4는 문제식 2x=6과 동치가 아니므로 오류로 검출되어야 한다.
    """
    r = analyze("2x=6", "x=4", student_id="s7-wrong")
    assert r["correct"] is False
    assert r["error_step"] == 1
    assert r["error_type"] == "calculation"


def test_multi_step_correct_solution_also_correct():
    """과정 있는 풀이와 과정 없는 풀이의 correct 판정은 동일하다."""
    with_process = analyze("2x=6", "2x=6\nx=3", student_id="s7-a")
    without_process = analyze("2x=6", "x=3", student_id="s7-b")
    assert with_process["correct"] is without_process["correct"] is True


def test_skipped_step_without_error_is_not_flagged():
    """중간 단계를 건너뛰어도 오류가 없으면 correct=true (동치 판정 기준).

    '2x=6 → x=3' 처럼 3x=12 단계를 생략해도 판정에는 오류가 없다.
    """
    r = analyze("2x=6", "2x=6\nx=3", student_id="s7-skip")
    assert r["correct"] is True
    assert r["valid"] == [True, True]


# ---------------------------------------------------------------------------
# 문서화 고정: 정책이 schema/README에 명시되어 있는가
# ---------------------------------------------------------------------------

def test_correct_field_description_documents_the_policy():
    """스키마 description이 '오류 미검출 ≠ 완전 검증' 의미를 담고 있어야 한다."""
    desc = AnalyzeSolutionResponse.model_fields["correct"].description or ""
    assert "오류가 검출되지 않았" in desc
    assert "완전성" in desc
    assert "독립적인 증명" in desc


def test_policy_recorded_in_readme():
    """README에 정답만/과정없이 제출해도 허용되는 정책이 기록되어 있어야 한다."""
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    # 정책의 의미를 담는 앵커 (문구 선택이 아니라 정책 존재를 검증)
    assert "오류가 검출되지 않았" in readme, "correct 필드 의미가 README에 없음"
    assert "풀이 과정의 완전성" in readme, "완전성 아님이 README에 명시되지 않음"
    assert "correct=true`가 허용된다" in readme, "과정 없는 정답 허용이 README에 없음"
    assert "동작을 변경하지 않았다" in readme, "정책 변경 없음이 README에 없음"


def test_no_new_validation_state_was_added_for_answer_only():
    """S7 정책용 필드/상태를 추가하지 않았음을 고정한다 (Phase 8-2 analysis_status 제외).

    Phase 8-2 에서 앱 r49 대응으로 `analysis_status` 가 추가되었지만, 그것은
    '풀이 완전성'이 아니라 '분석 판정 상태'이므로 S7 정책 필드가 아니다.
    S7 이 금지한 '검증 불가/완전성' 표현 필드는 여전히 없다.
    """
    fields = set(AnalyzeSolutionResponse.model_fields)
    assert fields == {
        "correct", "error_step", "error_type", "skill", "state", "mastery",
        "misconception_id", "confidence", "steps_latex", "valid", "unrecognized",
        "skills", "skill_ids", "error_subtype", "misconception_ids",
        "analysis_status",
    }
    # '검증 불가'/'부분 검증'을 나타내는 별도 상태 필드는 없다
    assert "verification_state" not in fields
    assert "is_complete" not in fields
    assert "process_complete" not in fields
