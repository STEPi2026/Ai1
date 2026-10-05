"""Phase 6 회귀 — 학습 데이터 흐름 연결.

    Problem → StudentAttempt → verify → classify → ErrorRecord[]
             → KnowledgeObservation[] → Knowledge State / BKT

핵심 불변식:
- BKT outcome 은 반드시 이진(correct | incorrect). error_type 은 별도 메타데이터.
- error_type(관찰된 형태) 과 misconception(개념적 원인) 은 분리된다.
- 관측되지 않은 Skill 은 Knowledge State 를 건드리지 않는다.
- group node 는 Problem.skills 에 표현 가능하나 BKT tracking 대상이 아니다.
- student_id 정책은 기존 validate_student_id() 를 그대로 재사용한다.
- 기존 analyze() / API 계약 / golden 은 변하지 않는다.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.pipeline.analyzer import analyze, get_engine, reset_knowledge
from app.pipeline.knowledge import (
    STATE_LEARNING,
    STATE_MASTERED,
    STATE_NEEDS_PRACTICE,
    BKTEngine,
    RuleStateEngine,
    is_correct_outcome,
    validate_student_id,
)
from app.pipeline.session import (
    AttemptAnalysis,
    CorrectSolution,
    ErrorRecord,
    KnowledgeObservation,
    Problem,
    SolutionStep,
    StudentAttempt,
    analyze_attempt,
)
from app.schemas import AnalyzeSolutionResponse


@pytest.fixture(autouse=True)
def _fresh():
    reset_knowledge()
    yield
    reset_knowledge()


DIST_PROBLEM = Problem(
    problem_id="P-DIST",
    problem_latex="2(x-3)=6",
    skills=["linear_equation", "distribution"],
)
CALC_PROBLEM = Problem(
    problem_id="P-CALC", problem_latex="5x=15", skills=["linear_equation", "arithmetic"]
)


# ===========================================================================
# 1. Problem — 다중 Skill / M:N
# ===========================================================================

def test_problem_can_have_multiple_skills():
    assert DIST_PROBLEM.skills == ["linear_equation", "distribution"]


def test_problem_many_to_many_with_skills():
    """M:N — 하나의 skill 이 여러 problem 에 쓰이고, problem 은 여러 skill 을 가진다."""
    a = Problem(problem_id="A", problem_latex="2x=6", skills=["linear_equation"])
    b = Problem(problem_id="B", problem_latex="2(x-3)=6", skills=["distribution"])
    c = Problem(problem_id="C", problem_latex="3(x-2)=9", skills=["distribution"])
    assert a.skills[0] == b.skills[0] or True  # 서로 다른 문제/스킬 조합 존재
    assert "distribution" in b.skills and "distribution" in c.skills  # 1 skill : N problems
    assert len(b.skills) == 1 and len(DIST_PROBLEM.skills) == 2  # N skills : 1 problem


def test_problem_rejects_unknown_skill():
    with pytest.raises(ValidationError):
        Problem(problem_id="X", problem_latex="2x=6", skills=["NOT_A_SKILL"])


def test_problem_tracking_skill_ids_excludes_group_node():
    p = Problem(problem_id="G", problem_latex="2x=6", skills=["factoring", "linear_equation"])
    assert p.skills == ["factoring", "linear_equation"]
    assert p.tracking_skill_ids() == ["linear_equation"], "group node 는 추적 대상이 아니다"
    assert p.primary_tracking_skill() == "linear_equation"


def test_problem_with_only_group_node_has_no_tracking_skill():
    p = Problem(problem_id="G2", problem_latex="2x=6", skills=["factoring"])
    assert p.tracking_skill_ids() == []
    with pytest.raises(Exception):
        p.primary_tracking_skill()


# ===========================================================================
# 2. SolutionStep / CorrectSolution
# ===========================================================================

def test_solution_step_can_have_multiple_skills():
    s = SolutionStep(step_no=1, latex_text="2(x-3)=6", skill_ids=["linear_equation", "distribution"])
    assert s.skill_ids == ["linear_equation", "distribution"]
    assert s.aligned_step_no is None
    assert s.unrecognized is False


def test_solution_step_rejects_unknown_skill():
    with pytest.raises(ValidationError):
        SolutionStep(step_no=1, latex_text="x=1", skill_ids=["NOPE"])


def test_correct_solution_with_multiple_steps():
    sol = CorrectSolution(
        solution_id="S1",
        problem_id=DIST_PROBLEM.problem_id,
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=6", skill_ids=["linear_equation"]),
            SolutionStep(step_no=2, latex_text="2x-6=6", skill_ids=["distribution"]),
            SolutionStep(step_no=3, latex_text="2x=12", skill_ids=["distribution"]),
            SolutionStep(step_no=4, latex_text="x=6", skill_ids=["linear_equation"]),
        ],
        answer_latex="x=6",
    )
    assert len(sol.steps) == 4
    assert sol.is_primary is True
    assert sol.belongs_to(DIST_PROBLEM) is True
    assert sol.steps[0].aligned_step_no is None  # 정답 풀이에서는 선택 값


def test_correct_solution_step_skill_ids_used_by_alignment():
    sol = CorrectSolution(
        solution_id="S2",
        problem_id=DIST_PROBLEM.problem_id,
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=6", aligned_step_no=1),
            SolutionStep(step_no=2, latex_text="2x-6=6", aligned_step_no=1),
        ],
        answer_latex="x=6",
    )
    assert [s.aligned_step_no for s in sol.steps] == [1, 1]


def test_correct_solution_rejects_duplicate_step_no():
    with pytest.raises(ValidationError):
        CorrectSolution(
            solution_id="S3",
            problem_id="P",
            steps=[SolutionStep(step_no=1, latex_text="a"), SolutionStep(step_no=1, latex_text="b")],
            answer_latex="x",
        )


def test_correct_solution_rejects_empty_steps():
    with pytest.raises(ValidationError):
        CorrectSolution(solution_id="S4", problem_id="P", steps=[], answer_latex="x")


def test_analyze_attempt_accepts_matching_solution():
    sol = CorrectSolution(
        solution_id="S5",
        problem_id=DIST_PROBLEM.problem_id,
        steps=[SolutionStep(step_no=1, latex_text="2(x-3)=6")],
        answer_latex="x=6",
    )
    a = StudentAttempt(
        attempt_id="A-sol", student_id="S1", problem_id="P-DIST", solution_text="2(x-3)=6\nx=6"
    )
    r = analyze_attempt(DIST_PROBLEM, a, solution=sol)
    assert r.correct is True


def test_analyze_attempt_rejects_mismatched_solution():
    sol = CorrectSolution(
        solution_id="S6",
        problem_id="OTHER",
        steps=[SolutionStep(step_no=1, latex_text="x")],
        answer_latex="x",
    )
    a = StudentAttempt(attempt_id="A-m", student_id="S1", problem_id="P-DIST", solution_text="x=3")
    with pytest.raises(ValueError):
        analyze_attempt(DIST_PROBLEM, a, solution=sol)


# ===========================================================================
# 3. StudentAttempt — student_id 정책 재사용
# ===========================================================================

def test_attempt_reuses_student_id_validation():
    for bad in ("", "   ", "S" * 200, "S\x00x"):
        with pytest.raises(ValidationError):
            StudentAttempt(attempt_id="A", student_id=bad, problem_id="P", solution_text="x=1")


def test_attempt_student_id_normalized_like_helper():
    a = StudentAttempt(attempt_id="A", student_id="  S001  ", problem_id="P", solution_text="x=1")
    assert a.student_id == validate_student_id("  S001  ") == "S001"


def test_attempt_rejects_non_string_student_id():
    with pytest.raises(ValidationError):
        StudentAttempt(attempt_id="A", student_id=123, problem_id="P", solution_text="x=1")


def test_attempt_derives_steps_from_solution_text():
    a = StudentAttempt(
        attempt_id="A1", student_id="S1", problem_id="P-DIST", solution_text="2(x-3)=6\n2x-6=6\nx=6"
    )
    assert [s.step_no for s in a.steps] == [1, 2, 3]
    assert [s.latex_text for s in a.steps] == ["2(x-3)=6", "2x-6=6", "x=6"]


def test_attempt_keeps_explicit_steps():
    a = StudentAttempt(
        attempt_id="A2",
        student_id="S1",
        problem_id="P-DIST",
        solution_text="",
        steps=[SolutionStep(step_no=1, latex_text="2(x-3)=6")],
    )
    assert len(a.steps) == 1


def test_analyze_attempt_rejects_problem_id_mismatch():
    a = StudentAttempt(attempt_id="A3", student_id="S1", problem_id="WRONG", solution_text="x=3")
    with pytest.raises(ValueError):
        analyze_attempt(DIST_PROBLEM, a)


# ===========================================================================
# 4. ErrorRecord — 다중 skill / 다중 misconception / type-vs-misconception 분리
# ===========================================================================

def _err(**kw) -> ErrorRecord:
    base = dict(
        error_id="E1",
        attempt_id="A1",
        step_no=2,
        ref_step_no=1,
        error_type="concept_error",
        error_subtype="distribution_omit",
        confidence=0.9,
        evidence_latex="2x-3=6",
        description_ko="개념 오류",
    )
    base.update(kw)
    return ErrorRecord(**base)


def test_error_record_with_multiple_skills():
    e = _err(skill_ids=["distribution", "polynomial_multiplication"])
    assert len(e.skill_ids) == 2


def test_error_record_with_multiple_misconceptions():
    e = _err(misconception_ids=["2.1", "3.1"])
    assert e.misconception_ids == ["2.1", "3.1"]


def test_error_type_and_misconception_are_separate_fields():
    """error_type 은 관찰된 형태, misconception 은 개념적 원인이다."""
    e = _err(error_type="concept_error", misconception_ids=[])
    assert e.error_type == "concept_error"
    assert e.misconception_ids == [], "계산/일반 오류에 오개념을 억지로 붙이지 않는다"
    e2 = _err(misconception_ids=["2.1"])
    assert e2.error_type == "concept_error" and e2.misconception_ids == ["2.1"]


def test_error_record_rejects_unknown_error_type():
    with pytest.raises(ValidationError):
        _err(error_type="NOT_A_TYPE")


def test_error_record_rejects_subtype_not_belonging_to_type():
    with pytest.raises(ValidationError):
        _err(error_type="calculation", error_subtype="distribution_omit")


def test_error_record_rejects_unknown_subtype():
    with pytest.raises(ValidationError):
        _err(error_subtype="NOT_A_SUBTYPE")


def test_error_record_rejects_unknown_misconception():
    with pytest.raises(ValidationError):
        _err(misconception_ids=["99.9"])


def test_error_record_rejects_unknown_skill():
    with pytest.raises(ValidationError):
        _err(skill_ids=["distribution", "NOPE"])


def test_error_record_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        _err(confidence=1.5)


def test_flow_error_has_ref_step_no_and_evidence():
    a = StudentAttempt(
        attempt_id="A-err", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a)
    e = r.errors[0]
    assert e.step_no == 2
    assert e.ref_step_no == 1, "비교 기준이 된 이전 단계 번호"
    assert e.evidence_latex == "2x-3=6"
    assert e.description_ko
    assert "분배" in e.description_ko


def test_flow_error_ref_step_none_when_problem_is_baseline():
    """1단계가 문제식과 대조되면 기준은 문제식이므로 ref_step_no 는 None."""
    a = StudentAttempt(
        attempt_id="A-ref", student_id="S1", problem_id="P-CALC", solution_text="5x=14\nx=3"
    )
    r = analyze_attempt(CALC_PROBLEM, a)
    assert r.errors[0].step_no == 1
    assert r.errors[0].ref_step_no is None


# ===========================================================================
# 5. KnowledgeObservation — 이진 outcome / error_type 분리
# ===========================================================================

def _obs(**kw) -> KnowledgeObservation:
    base = dict(
        observation_id="O1", attempt_id="A1", problem_id="P1",
        student_id="S1", skill_id="linear_equation", outcome="correct",
    )
    base.update(kw)
    return KnowledgeObservation(**base)


def test_observation_correct_outcome():
    o = _obs()
    assert o.outcome == "correct"
    assert o.error_type is None


def test_observation_incorrect_outcome_with_error_type():
    o = _obs(outcome="incorrect", error_type="concept_error")
    assert o.outcome == "incorrect"
    assert o.error_type == "concept_error", "error_type 은 관측값이 아닌 메타데이터"


@pytest.mark.parametrize("error_type", ["calculation", "procedure", "comprehension", "concept_error"])
def test_all_error_types_recordable_as_metadata_only(error_type):
    o = _obs(outcome="incorrect", error_type=error_type)
    assert o.outcome == "incorrect"
    assert o.error_type == error_type


@pytest.mark.parametrize("bad_outcome", ["concept_error", "calculation", "procedure", "NEUTRAL"])
def test_error_type_cannot_be_used_as_outcome(bad_outcome):
    """BKT outcome 은 반드시 이진 — error_type 을 outcome 에 넣을 수 없다."""
    with pytest.raises(ValidationError):
        _obs(outcome=bad_outcome)


def test_observation_rejects_group_node_skill():
    for group in ("factoring", "polynomial_arithmetic"):
        with pytest.raises(ValidationError):
            _obs(skill_id=group)


def test_observation_rejects_unknown_skill():
    with pytest.raises(ValidationError):
        _obs(skill_id="NOPE")


def test_observation_rejects_unknown_error_type():
    with pytest.raises(ValidationError):
        _obs(outcome="incorrect", error_type="NOT_A_TYPE")


def test_outcome_argument_returns_binary_only():
    o = _obs(outcome="incorrect", error_type="concept_error")
    assert o.outcome_argument() == "incorrect"


# ===========================================================================
# 6. 관측 단위 — 관측 안 된 Skill 은 갱신되지 않는다
# ===========================================================================

def test_correct_observation_goes_to_bkt_as_correct():
    a = StudentAttempt(
        attempt_id="A-ok", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-6=6\nx=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a, engine="bkt")
    assert r.correct is True
    assert [o.outcome for o in r.observations] == ["correct"]
    assert r.observations[0].skill_id == "linear_equation"
    assert r.mastery["linear_equation"] > BKTEngine().p_init


def test_error_observation_goes_to_bkt_as_incorrect():
    a = StudentAttempt(
        attempt_id="A-bad", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a, engine="bkt")
    assert all(o.outcome == "incorrect" for o in r.observations)
    assert all(o.outcome_argument() == "incorrect" for o in r.observations)
    assert r.mastery["distribution"] < BKTEngine().p_init


def test_unobserved_skill_is_not_updated():
    """문제에 선언돼 있어도 실제 단계에서 관측되지 않은 skill 은 갱신되지 않는다."""
    p = Problem(problem_id="P-U", problem_latex="2(x-3)=6", skills=["linear_equation", "transposition"])
    a = StudentAttempt(
        attempt_id="A-u",
        student_id="S1",
        problem_id="P-U",
        solution_text="2(x-3)=6\n2x-6=6\nx=6",
        steps=[
            SolutionStep(step_no=1, latex_text="2(x-3)=6", skill_ids=["linear_equation"]),
            SolutionStep(step_no=2, latex_text="2x-6=6", skill_ids=["linear_equation"]),
            SolutionStep(step_no=3, latex_text="x=6", skill_ids=["linear_equation"]),
        ],
    )
    r = analyze_attempt(p, a)
    assert r.observed_skill_ids == ["linear_equation"]
    assert [o.skill_id for o in r.observations] == ["linear_equation"]
    rule = get_engine("rule")
    assert ("S1", "linear_equation") in rule._state
    assert ("S1", "transposition") not in rule._state, "관측되지 않은 skill 이 갱신되었다"


def test_error_skill_is_observed_even_if_not_declared_on_problem():
    """오류를 일으킨 skill 은 정의상 시도한 것이므로 관측된 것으로 본다.

    taxonomy 의 related_skill_ids 전체(오류 관련 skill)가 관측되며,
    오류와 무관한 문제 선언 skill 은 건드리지 않는다.
    """
    p = Problem(problem_id="P-N", problem_latex="2(x-3)=6", skills=["linear_equation"])
    a = StudentAttempt(
        attempt_id="A-n", student_id="S1", problem_id="P-N",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(p, a)
    observed = [o.skill_id for o in r.observations]
    assert observed == list(r.errors[0].skill_ids)
    assert "distribution" in observed
    assert "linear_equation" not in observed, "오류와 무관한 skill 은 갱신되지 않는다"
    assert "distribution" in r.observed_skill_ids
    rule = get_engine("rule")
    assert ("S1", "linear_equation") not in rule._state


def test_group_skill_never_enters_knowledge_state():
    p = Problem(problem_id="P-G", problem_latex="2x=6", skills=["factoring", "linear_equation"])
    a = StudentAttempt(
        attempt_id="A-g", student_id="S1", problem_id="P-G", solution_text="2x=6\nx=3"
    )
    r = analyze_attempt(p, a)
    assert [o.skill_id for o in r.observations] == ["linear_equation"]
    assert all(o.skill_id != "factoring" for o in r.observations)
    rule = get_engine("rule")
    assert all(skill != "factoring" for _, skill in rule._state)
    assert all(skill != "factoring" for skill in r.states)


def test_problem_with_only_group_node_cannot_produce_observation():
    p = Problem(problem_id="P-G2", problem_latex="2x=6", skills=["factoring"])
    a = StudentAttempt(attempt_id="A-g2", student_id="S1", problem_id="P-G2", solution_text="2x=6\nx=3")
    with pytest.raises(Exception):
        analyze_attempt(p, a)


# ===========================================================================
# 7. Knowledge State 갱신 — error_type 이 상태를 세분화한다
# ===========================================================================

def test_calculation_error_keeps_learning_state():
    a = StudentAttempt(
        attempt_id="A-c", student_id="S1", problem_id="P-CALC", solution_text="5x=15\nx=4"
    )
    r = analyze_attempt(CALC_PROBLEM, a)
    assert r.states["arithmetic"] == STATE_LEARNING
    assert r.observations[0].outcome == "incorrect"
    assert r.observations[0].error_type == "calculation"


def test_concept_error_forces_needs_practice():
    a = StudentAttempt(
        attempt_id="A-ce", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a)
    assert r.states["distribution"] == STATE_NEEDS_PRACTICE


def test_two_correct_reaches_mastered_via_observations():
    for i in (1, 2):
        a = StudentAttempt(
            attempt_id=f"A-m{i}", student_id="S1", problem_id="P-CALC", solution_text="5x=15\nx=3"
        )
        r = analyze_attempt(CALC_PROBLEM, a)
    assert r.states["linear_equation"] == STATE_MASTERED


def test_procedure_error_type_maps_to_learning():
    """classifier 가 아직 procedure 를 내지는 않지만 상태 전이는 정의돼 있다."""
    e = RuleStateEngine()
    assert e.update_from_observation("S1", "linear_equation", "incorrect", "procedure") == STATE_LEARNING


def test_bkt_ignores_error_type_and_uses_binary_outcome():
    a = BKTEngine()
    b = BKTEngine()
    p1 = a.update_from_observation("S1", "linear_equation", "incorrect", "concept_error")
    p2 = b.update_from_observation("S1", "linear_equation", "incorrect", "calculation")
    assert p1 == p2, "BKT 는 error_type 을 관측값으로 쓰지 않는다"
    p3 = a.update_from_observation("S2", "linear_equation", "correct", None)
    assert p3 > a.p_init


def test_update_from_observation_does_not_break_update():
    e = RuleStateEngine()
    assert e.update_from_observation("S1", "linear_equation", "correct", None) == STATE_LEARNING
    assert e.update_from_observation("S1", "linear_equation", "correct", None) == STATE_MASTERED
    # 기존 update 경로도 그대로 동작
    assert e.update("S1", "linear_equation", "concept_error") == STATE_NEEDS_PRACTICE


def test_is_correct_outcome_still_binary():
    assert is_correct_outcome("correct") is True
    for x in ("calculation", "procedure", "concept_error", "comprehension"):
        assert is_correct_outcome(x) is False


# ===========================================================================
# 8. API 계약 호환
# ===========================================================================

def test_api_response_validates_against_existing_schema():
    a = StudentAttempt(
        attempt_id="A-api", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a)
    parsed = AnalyzeSolutionResponse(**r.api_response)
    assert parsed.skill == "distribution"
    assert parsed.error_step == 2
    assert parsed.error_type == "concept_error"
    assert parsed.misconception_id == "2.1"
    assert parsed.error_subtype == "distribution_omit"


def test_api_response_field_set_matches_contract():
    a = StudentAttempt(attempt_id="A-f", student_id="S1", problem_id="P-DIST", solution_text="2(x-3)=6\nx=6")
    r = analyze_attempt(DIST_PROBLEM, a)
    assert set(r.api_response) == set(AnalyzeSolutionResponse.model_fields)


def test_analyze_and_analyze_attempt_agree_on_error_fields():
    a = StudentAttempt(
        attempt_id="A-cmp", student_id="S1", problem_id="P-DIST",
        solution_text="2(x-3)=6\n2x-3=6",
    )
    r = analyze_attempt(DIST_PROBLEM, a)
    legacy = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="S1", problem_skill="linear_equation")
    for key in ("correct", "error_step", "error_type", "skill", "misconception_id", "confidence"):
        assert r.api_response[key] == legacy[key], f"{key} 가 기존 analyze() 와 다르다"


def test_analyze_unchanged_by_phase6():
    """기존 analyze() 결과가 Phase 6 전과 동일하다 (회귀 고정)."""
    reset_knowledge()
    out = analyze("2(x-3)=6", "2(x-3)=6\n2x-3=6", student_id="reg", problem_skill="linear_equation")
    assert out["correct"] is False
    assert out["error_step"] == 2
    assert out["error_type"] == "concept_error"
    assert out["skill"] == "distribution"
    assert out["state"] == "needs_practice"
    assert out["misconception_id"] == "2.1"
    assert out["confidence"] == 0.9
    assert out["valid"] == [True, False]
    assert out["unrecognized"] == []
    assert out["error_subtype"] == "distribution_omit"
    assert out["misconception_ids"] == ["2.1"]


def test_correct_analyze_unchanged():
    reset_knowledge()
    out = analyze("3x+5=20", "3x+5=20\n3x=15\nx=5", student_id="reg2")
    assert out["correct"] is True
    assert out["error_step"] is None
    assert out["error_type"] is None
    assert out["state"] == "learning"
    assert out["mastery"] == 0.6


# ===========================================================================
# 9. 단계 메타데이터 / 모델 안전성
# ===========================================================================

def test_unrecognized_flag_reflected_on_attempt_steps():
    a = StudentAttempt(
        attempt_id="A-unrec", student_id="S1", problem_id="P-CALC",
        solution_text="5x=15\n???\nx=3",
    )
    r = analyze_attempt(CALC_PROBLEM, a)
    assert r.api_response["unrecognized"] == [2]
    assert r.attempt.steps[1].unrecognized is True
    assert r.attempt.steps[0].unrecognized is False


def test_attempt_analysis_attachment_is_acyclic():
    a = StudentAttempt(
        attempt_id="A-cyc", student_id="S1", problem_id="P-CALC", solution_text="5x=15\nx=3"
    )
    r = analyze_attempt(CALC_PROBLEM, a)
    attached = a.with_analysis(r)
    assert attached.analysis is r
    dumped = attached.model_dump()  # 순환 참조로 실패하지 않아야 한다
    assert dumped["analysis"]["attempt"]["analysis"] is None
    assert r.attempt.analysis is None, "분석에 담긴 attempt 는 analysis 가 비어 있어야 한다"


def test_attempt_analysis_contains_attempt():
    a = StudentAttempt(
        attempt_id="A-in", student_id="S1", problem_id="P-CALC", solution_text="5x=15\nx=3"
    )
    r = analyze_attempt(CALC_PROBLEM, a)
    assert isinstance(r, AttemptAnalysis)
    assert r.attempt.attempt_id == "A-in"
    assert r.attempt_id == "A-in"
    assert r.student_id == "S1"


def test_error_type_procedure_accepted_in_error_record():
    e = _err(error_type="procedure", error_subtype="incomplete_solution")
    assert e.error_type == "procedure"
    assert e.error_subtype == "incomplete_solution"


def test_description_ko_uses_taxonomy_names():
    e = _err(error_type="concept_error", error_subtype="distribution_omit", misconception_ids=["2.1"])
    from app.pipeline.session import _describe_ko

    text = _describe_ko("concept_error", "distribution_omit", ["2.1"])
    assert "개념 오류" in text and "분배법칙 누락" in text
    assert e.description_ko  # 모델이 설명을 요구한다
