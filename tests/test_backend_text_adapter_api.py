"""PHASE 9-7 — Backend 전달 경로 API 테스트.

검증 범위
  - 정수 attempt_id/student_id 정규화 (십진 문자열, 접두사 없음)
  - 오류 없는 정답 / 대표 오류 / 다중 오류 / REVIEW_REQUIRED / UNKNOWN
  - POST · 멱등 재응답 · 분석 상세 GET 이 같은 코드 매핑을 재생성한다
  - approved / provisional / unmapped / 금지 코드가 API 응답에서 구분된다
  - REVIEW_REQUIRED 는 AI② 진행 불가 상태로 정책 고정
  - 기존 계약 호환: Legacy 16필드, Attempt 기존 필드, 멱등성, GET 읽기 전용,
    taxonomy/golden 불변
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import RepositoryError, get_repository, reset_repository
from app.schemas import AnalyzeAttemptResponse, AnalyzeSolutionResponse
from app.taxonomy import validate_all

client = TestClient(app)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: PHASE 9-7 additive 필드 (기존 26개 + 5개 = 31)
BACKEND_CODES_FIELDS = {
    "misconception_tag",
    "official_skill_codes",
    "official_misconception_codes",
    "provisional_skill_codes",
    "provisional_misconception_codes",
}

PROBLEM = "2(x-3)=6"
DISTRIBUTION_ERROR = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"
CORRECT = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"
TRANSPOSITION_ASYMMETRY = "2x+6=14\n2x=14"
TRANSPOSITION_SIGN = "3x+5=20\n3x=25"
REVIEW_REQUIRED = "2(x-3)=6\n2x-3=6\n???\nx=6.5"


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


def post(
    attempt_id,
    *,
    student_id="S1",
    problem_id="P1",
    problem_latex=PROBLEM,
    skills=("linear_equation", "distribution"),
    text=DISTRIBUTION_ERROR,
):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={
            "problem_id": problem_id,
            "problem_latex": problem_latex,
            "problem_skills": list(skills),
            "student_id": student_id,
            "solution_text": text,
        },
    )


def detail_of(attempt_id, analysis_id):
    return client.get(
        f"/api/v1/attempts/{attempt_id}/analyses/" + analysis_id.replace("#", "%23")
    )


# ===========================================================================
# 1. 정수 식별자 정규화
# ===========================================================================

def test_integer_student_id_normalized_to_decimal_string():
    r = post("A1", student_id=4821)
    body = r.json()
    assert r.status_code == 200
    assert body["student_id"] == "4821"
    assert isinstance(body["student_id"], str)


def test_string_student_id_unchanged():
    r = post("A2", student_id="S001")
    assert r.json()["student_id"] == "S001"


def test_integer_student_id_adds_no_prefix():
    r = post("A3", student_id=4821)
    assert not r.json()["student_id"].startswith(("t_", "s_", "T", "S"))


def test_integer_and_string_student_id_share_knowledge_state():
    """정수 4821 과 문자열 "4821" 은 같은 학생이다 (십진 문자열 정규화)."""
    first = post("A4", student_id=4821, text=CORRECT).json()
    second = post("A5", student_id="4821", text=CORRECT).json()
    assert second["mastery_by_skill"]["linear_equation"] > first["mastery_by_skill"][
        "linear_equation"
    ]
    assert second["state"] == "mastered"


def test_leading_zero_student_id_is_not_merged():
    """"004821" 과 4821 은 서로 다른 학생이다 — ID 를 합치지 않는다."""
    a = post("A6", student_id="004821", text=CORRECT).json()
    b = post("A7", student_id="4821", text=CORRECT).json()
    assert a["mastery_by_skill"]["linear_equation"] == b["mastery_by_skill"]["linear_equation"]


def test_integer_attempt_id_in_path_is_string():
    r = post(9001)
    body = r.json()
    assert r.status_code == 200
    assert body["attempt_id"] == "9001"


def test_bool_student_id_rejected_before_normalization():
    """bool 이 정수 1 로 조용히 정규화되면 학생 "1" 이 만들어진다 — 거절한다."""
    r = client.post(
        "/api/v1/attempts/B2/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": PROBLEM,
            "problem_skills": ["linear_equation"],
            "student_id": True,
            "solution_text": CORRECT,
        },
    )
    assert r.status_code == 422
    assert get_repository().has_analysis("B2") is False


def test_blank_student_id_rejected_with_backend_id_code():
    r = client.post(
        "/api/v1/attempts/B3/analyze",
        json={
            "problem_id": "P1",
            "problem_latex": PROBLEM,
            "problem_skills": ["linear_equation"],
            "student_id": "   ",
            "solution_text": CORRECT,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_BACKEND_ID"


# ===========================================================================
# 2. problem_id 보존 — 추측·병합 금지
# ===========================================================================

@pytest.mark.parametrize("problem_id", ["captured:42", "PRB-000123", "opaque-任意-id"])
def test_problem_id_preserved_verbatim(problem_id):
    r = post("C1", problem_id=problem_id)
    body = r.json()
    assert body["problem_id"] == problem_id
    stored = get_repository().get_problem(problem_id)
    assert stored.problem_id == problem_id


def test_captured_and_seed_problem_ids_are_not_merged():
    captured = post("C2", problem_id="captured:42", text=CORRECT).json()
    seed = post("C3", problem_id="PRB-42", text=CORRECT).json()
    assert captured["problem_id"] == "captured:42"
    assert seed["problem_id"] == "PRB-42"
    assert len(get_repository().list_problems()) == 2


# ===========================================================================
# 3. Backend Skill code 역변환
# ===========================================================================

def test_backend_skill_code_is_reverse_mapped():
    r = post("S1", skills=("SKL-1-004", "linear_equation"))
    assert r.status_code == 200
    assert r.json()["skill"] == "distribution"


def test_seed_outside_skill_code_is_rejected_not_reverse_mapped():
    """SKL-2-037 은 공식 Seed(SKL-2-035 까지) 밖 코드다.

    A2 로 역변환 대상에서 제외했다. 시드 밖 코드를 통과시키면 Backend 가
    FK 로 잡을 수 없는 값이 응답에 섞인다.
    """
    r = post(
        "S2",
        problem_latex="2x+6=14",
        skills=("linear_equation", "SKL-2-037"),
        text=TRANSPOSITION_ASYMMETRY,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL_CODE"
    assert get_repository().has_analysis("S2") is False


@pytest.mark.parametrize("bad_code", ["SKL-9-999", "MIS-2-001", "SKL-1-4", "SKL-2-037"])
def test_unknown_backend_skill_code_rejected_422(bad_code):
    r = post("S3", skills=(bad_code,))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL_CODE"


def test_unknown_skill_code_is_not_replaced_by_default_skill():
    """모르는 코드를 기본 Skill 로 치환하지 않는다 — 422 로 거절한다."""
    r = post("S4", skills=("SKL-9-999",))
    assert r.status_code == 422
    assert get_repository().has_analysis("S4") is False


def test_mixed_codes_and_internal_ids_resolve():
    """문제 선언분은 요청에서 역변환된다. 응답 코드는 '분석 결과의' skill 기준이다."""
    r = post("S5", skills=("SKL-1-004", "linear_equation"))
    assert r.status_code == 200
    body = r.json()
    # 요청은 SKL-1-004 + linear_equation 로 정상 해석됐다
    assert body["skill"] == "distribution"
    assert body["official_skill_codes"] == ["SKL-1-004", "SKL-1-005"]
    # 응답 코드는 관측 skill 기준이고, provisional 표는 비어 있다 (A2)
    assert body["provisional_skill_codes"] == []
    stored = get_repository().get_problem("P1")
    assert set(stored.skills) == {"distribution", "linear_equation"}


# ===========================================================================
# 4. 오류 없는 정답
# ===========================================================================

def test_correct_solution_has_no_backend_codes_and_no_tag():
    body = post("K1", text=CORRECT).json()
    assert body["correct"] is True
    assert body["analysis_status"] == "ANALYZED"
    assert body["misconception_tag"] is None
    assert body["misconception_tags"] == []
    assert body["official_misconception_codes"] == []
    assert body["provisional_misconception_codes"] == []


def test_correct_solution_on_unmapped_skill_does_not_invent_code():
    """linear_equation 에는 승인/후보 코드가 없다 → 지어내지 않는다."""
    body = post("K2", skills=("linear_equation",), text=CORRECT).json()
    assert body["official_skill_codes"] == []
    assert body["provisional_skill_codes"] == []


# ===========================================================================
# 5. 대표 오류 (approved) / 다중 오류
# ===========================================================================

def test_primary_approved_error_exposes_official_codes():
    body = post("E1").json()
    assert body["error_type"] == "concept_error"
    assert body["misconception_id"] == "2.1"
    assert body["misconception_tag"] == "2.1"
    assert body["official_skill_codes"] == ["SKL-1-004", "SKL-1-005"]
    assert body["official_misconception_codes"] == ["MIS-1-004"]
    assert body["provisional_skill_codes"] == []
    assert body["provisional_misconception_codes"] == []


def test_transposition_error_keeps_internal_ids_without_backend_codes():
    """내부 taxonomy(3.2)는 그대로 관측되지만 시드 밖 코드는 나가지 않는다 (A2)."""
    body = post(
        "E2",
        problem_latex="2x+6=14",
        skills=("linear_equation", "transposition"),
        text=TRANSPOSITION_ASYMMETRY,
    ).json()
    assert body["misconception_tag"] == "3.2"
    assert body["misconception_tags"] == ["3.2"]
    assert body["official_misconception_codes"] == []
    assert body["provisional_misconception_codes"] == []
    assert body["provisional_skill_codes"] == []


def test_3_1_sign_unchanged_keeps_internal_id_without_backend_code():
    body = post(
        "E3",
        problem_latex="3x+5=20",
        skills=("linear_equation", "transposition"),
        text=TRANSPOSITION_SIGN,
    ).json()
    assert body["misconception_tag"] == "3.1"
    assert body["provisional_misconception_codes"] == []


def test_multi_error_keeps_all_errors_and_picks_lowest_step_no():
    """독립 오류 3건이 모두 보존되고, 대표 오류는 가장 낮은 step_no 다.

    풀이 `2(x-3)=10 → 2x-6=11 → 2x=17 → x=8 → x+1=9 → x=7` 은 각 오류의 기준 줄이
    유효해서 독립 오류가 3건(2·4·6단계) 기록된다. 연쇄 오류로 접히지 않는다.
    """
    body = post(
        "E4",
        problem_latex="2(x-3)=10",
        problem_id="P10",
        skills=("linear_equation", "arithmetic"),
        text="2(x-3)=10\n2x-6=11\n2x=17\nx=8\nx+1=9\nx=7",
    ).json()
    # 실제 독립 오류의 기대 목록을 그대로 비교한다 (자기참조 비교 금지)
    assert [err["step_no"] for err in body["errors"]] == [2, 4, 6]
    assert [err["error_type"] for err in body["errors"]] == ["calculation"] * 3
    assert all(err["misconception_ids"] == [] for err in body["errors"])
    # 대표 오류 = 가장 낮은 step_no, 단계 번호는 1-based
    assert body["error_step"] == 2
    assert body["first_error_step_index"] == 2
    assert body["error_step"] == min(err["step_no"] for err in body["errors"])
    # confidence 는 대표 오류의 값 그대로 (오류 전체의 합·평균이 아니다)
    assert body["confidence"] == body["errors"][0]["confidence"] == 0.75
    # 계산 오류에는 오개념이 없으므로 단수 태그도 null
    assert body["misconception_tag"] is None
    assert body["official_misconception_codes"] == []
    assert body["provisional_misconception_codes"] == []


def test_confidence_is_primary_error_confidence_not_averaged():
    """API 응답 confidence 는 대표 오류의 분류 confidence 다.

    같은 시도 안의 독립 오류는 같은 유형이면 confidence 가 같으므로 평균과 수치상
    구분되지 않는다. 그래서 '대표 오류 값과 동일' 을 단언하고, 서로 다른 confidence 를
    가진 대표 선택은 adapter 단위 테스트가 담당한다.
    """
    body = post(
        "E5",
        problem_latex="2(x-3)=10",
        problem_id="P10",
        skills=("linear_equation", "arithmetic"),
        text="2(x-3)=10\n2x-6=11\n2x=17\nx=8\nx+1=9\nx=7",
    ).json()
    primary = min(body["errors"], key=lambda e: e["step_no"])
    assert body["confidence"] == primary["confidence"]
    assert body["confidence"] is not None


def test_multi_error_with_concept_keeps_misconception_of_primary_only():
    """대표 오류가 개념 오류면 그 오류의 오개념만 단수 태그가 된다."""
    body = post("E5b", text="3x+5=20\n3x=25\n3x+5=20\n3x=30",
                problem_latex="3x+5=20", problem_id="P11",
                skills=("linear_equation", "transposition")).json()
    assert [err["step_no"] for err in body["errors"]] == [2, 3]
    assert body["error_step"] == 2
    assert body["misconception_tag"] == body["errors"][0]["misconception_ids"][0] == "3.1"
    # 3.1 은 시드 밖 코드라 Backend 코드 배열에는 나가지 않는다 (A2)
    assert body["provisional_misconception_codes"] == []


def test_step_numbers_are_one_based():
    body = post("E6").json()
    assert body["error_step"] == 2
    assert body["first_error_step_index"] == 2
    assert body["errors"][0]["step_no"] == 2


def test_calculation_error_gets_no_misconception_code():
    body = post(
        "E7", problem_latex="2x-6=7", problem_id="P9", text="2x-6=7\n2x=14"
    ).json()
    assert body["error_type"] == "calculation"
    assert body["misconception_tag"] is None
    assert body["official_misconception_codes"] == []
    assert body["provisional_misconception_codes"] == []
    assert "MIS-2-003" not in json.dumps(body)


def test_division_by_zero_code_never_emitted():
    body = post("E8", problem_latex="2x=0", problem_id="P8", text="2x=0\nx=0/0").json()
    payload = json.dumps(body)
    assert "MIS-2-004" not in payload
    assert "DIVISION_BY_ZERO" not in payload


def test_no_stp_code_in_response():
    body = post("E9").json()
    assert "STP-" not in json.dumps(body)


# ===========================================================================
# 6. REVIEW_REQUIRED — AI② 진행 불가 상태
# ===========================================================================

def test_review_required_status_and_fields():
    body = post("R1", text=REVIEW_REQUIRED).json()
    assert body["analysis_status"] == "REVIEW_REQUIRED"
    assert body["unrecognized"] == [3]
    assert None in body["valid"]


def test_review_required_keeps_confirmed_error_evidence_codes():
    """REVIEW_REQUIRED 여도 확정된 오류 근거의 코드는 노출한다."""
    body = post("R2", text=REVIEW_REQUIRED).json()
    assert body["error_type"] == "concept_error"
    assert body["misconception_tag"] == "2.1"
    assert body["official_misconception_codes"] == ["MIS-1-004"]


def test_review_required_is_not_a_complete_correct_observation():
    """correct=true 여도 완전한 정답 관측이 아니라는 정책 고정.

    REVIEW_REQUIRED 응답에는 확정 오류 근거가 있으므로 Backend 는 오답/오개념
    기록 후보로만 다뤄야 하고 AI② 를 진행하지 않는다.
    """
    body = post("R3", text="2(x-3)=6\n???\n2x=12").json()
    assert body["analysis_status"] == "REVIEW_REQUIRED"
    assert body["correct"] is True  # 오류 검출 없음
    # 미인식 단계가 있으므로 확정 오류는 없다 → 오개념 코드도 없다
    assert body["errors"] == []
    assert body["misconception_tag"] is None
    assert body["official_misconception_codes"] == []
    assert body["provisional_misconception_codes"] == []


# ===========================================================================
# 7. UNKNOWN — 422 detail.analysis_status, 미저장
# ===========================================================================

def test_unknown_status_is_422_detail_and_persists_nothing():
    r = post("U1", text="???")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "E_UNRECOGNIZED"
    assert detail["analysis_status"] == "UNKNOWN"
    # 분석 이력이 저장되지 않았음을 저장소에서 직접 확인한다
    repo = get_repository()
    assert repo.has_analysis("U1") is False
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_attempt("U1")
    assert excinfo.value.code == "E_REFERENCE_INTEGRITY"
    with pytest.raises(RepositoryError):
        repo.list_analyses("U1")


def test_failed_analysis_does_not_lock_attempt_id():
    """UNKNOWN 분석이 attempt_id 를 잠가 버리면 재제분이 409 가 된다 (그래서는 안 된다)."""
    assert post("U1b", text="???").status_code == 422
    retry = post("U1b", text=CORRECT)
    assert retry.status_code == 200
    assert retry.json()["correct"] is True
    assert len(get_repository().list_analyses("U1b")) == 1


def test_problem_is_registered_even_when_analysis_fails():
    """문제는 최초 요청 때 등록된다 (문제 자체는 유효하므로). 분석/attempt 만 미저장."""
    post("U1c", text="???")
    assert get_repository().get_problem("P1").problem_id == "P1"
    assert get_repository().has_analysis("U1c") is False


def test_unknown_does_not_register_attempt():
    r = post("U2", text="???")
    assert r.status_code == 422
    assert client.get("/api/v1/students/S1/history").json()["total"] == 0


def test_error_detail_wrapper_shape_unchanged():
    r = post("U3", text="???")
    detail = r.json()["detail"]
    assert set(detail) == {"code", "message", "analysis_status"}
    assert isinstance(detail["message"], str)


def test_ok_status_never_reports_unknown():
    cases = [
        ("U4a", CORRECT),
        ("U4b", DISTRIBUTION_ERROR),
        ("U4c", REVIEW_REQUIRED),
    ]
    for attempt_id, text in cases:
        body = post(attempt_id, text=text).json()
        assert body["analysis_status"] in ("ANALYZED", "REVIEW_REQUIRED"), attempt_id
        assert body["analysis_status"] != "UNKNOWN", attempt_id


# ===========================================================================
# 8. POST · 멱등 재응답 · 상세 GET 동일성
# ===========================================================================

def test_post_idempotent_and_detail_share_identical_mapping():
    first = post("M1").json()
    second = post("M1").json()  # 멱등 재응답
    detail = detail_of("M1", first["analysis_id"]).json()
    for body in (first, second, detail):
        assert body["official_skill_codes"] == ["SKL-1-004", "SKL-1-005"]
        assert body["official_misconception_codes"] == ["MIS-1-004"]
        assert body["provisional_skill_codes"] == []
        assert body["provisional_misconception_codes"] == []
        assert body["misconception_tag"] == "2.1"


def test_idempotent_repost_does_not_add_history_or_reobserve():
    first = post("M2").json()
    mastery_after_first = first["mastery_by_skill"]
    second = post("M2").json()
    assert second["analysis_id"] == first["analysis_id"]
    assert second["analysis_count"] == 1
    assert len(get_repository().list_analyses("M2")) == 1
    assert mastery_after_first  # 값이 존재한다 (재관측이면 변했을 것)


def test_detail_is_read_only_for_knowledge_state():
    before = post("M3").json()
    states_before = dict(before["states"])
    mastery_before = dict(before["mastery_by_skill"])
    for _ in range(3):
        detail_of("M3", before["analysis_id"])
        client.get("/api/v1/attempts/M3/analyses")
        client.get("/api/v1/students/S1/history")
    after = detail_of("M3", before["analysis_id"]).json()
    assert after["states"] == states_before
    assert after["mastery_by_skill"] == mastery_before
    assert after["mastery"] == before["mastery"]


def test_detail_carries_raw_result_and_same_mapping():
    posted = post("M4").json()
    detail = detail_of("M4", posted["analysis_id"]).json()
    assert "raw_result" in detail
    assert detail["raw_result"]["misconception_id"] == posted["misconception_id"]
    assert detail["misconception_tag"] == posted["misconception_tag"]
    assert detail["official_misconception_codes"] == posted["official_misconception_codes"]


def test_repeat_post_for_transposition_keeps_empty_backend_codes():
    first = post(
        "M5",
        problem_latex="2x+6=14",
        skills=("linear_equation", "transposition"),
        text=TRANSPOSITION_ASYMMETRY,
    ).json()
    second = post(
        "M5",
        problem_latex="2x+6=14",
        skills=("linear_equation", "transposition"),
        text=TRANSPOSITION_ASYMMETRY,
    ).json()
    detail = detail_of("M5", first["analysis_id"]).json()
    for body in (first, second, detail):
        assert body["provisional_skill_codes"] == []
        assert body["provisional_misconception_codes"] == []
        assert body["official_misconception_codes"] == []
        # 내부 관측은 멱등 재응답·상세 조회에서도 동일하다
        assert body["misconception_tag"] == "3.2"


def test_different_attempt_ids_keep_separate_histories():
    post("M6", text=CORRECT)
    post("M7", text=DISTRIBUTION_ERROR)
    assert len(get_repository().list_analyses("M6")) == 1
    assert len(get_repository().list_analyses("M7")) == 1
    assert client.get("/api/v1/students/S1/history").json()["total"] == 2


# ===========================================================================
# 9. 기존 계약 호환
# ===========================================================================

def test_attempt_response_field_set_is_stable_plus_backend_codes():
    body = post("F1").json()
    legacy_fields = {
        "analysis_id", "attempt_id", "problem_id", "student_id", "analysis_status",
        "analysis_count", "attempted_at", "correct", "error_step",
        "first_error_step_index", "error_type", "error_subtype", "skill", "state",
        "mastery", "misconception_id", "confidence", "steps_latex", "valid",
        "unrecognized", "step_list", "errors", "observations", "misconception_tags",
        "states", "mastery_by_skill",
    }
    assert set(body) == (
        legacy_fields | BACKEND_CODES_FIELDS
        | {"final_answer_present", "review_reasons"}
    )
    assert len(body) == 33


def test_schema_model_field_count_matches_response():
    assert len(AnalyzeAttemptResponse.model_fields) == 33


def test_legacy_analyze_solution_still_16_fields_without_codes():
    r = client.post(
        "/analyze-solution",
        json={
            "problem_latex": PROBLEM,
            "solution_text": DISTRIBUTION_ERROR,
            "student_id": "legacy",
            "problem_skill": "linear_equation",
        },
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert len(AnalyzeSolutionResponse.model_fields) == 16
    assert body["skill"] == "distribution"
    assert body["misconception_id"] == "2.1"
    payload = json.dumps(body)
    for forbidden in BACKEND_CODES_FIELDS:
        assert forbidden not in body, forbidden
    assert "SKL-" not in payload
    assert "MIS-" not in payload


def test_legacy_taxonomy_ids_are_not_overwritten_by_codes():
    body = post("F2").json()
    assert body["skill"] == "distribution"
    assert body["misconception_id"] == "2.1"
    assert body["misconception_tags"] == ["2.1"]
    # Backend 코드는 별도 필드로만 coexisting
    assert body["official_skill_codes"] == ["SKL-1-004", "SKL-1-005"]


def test_correct_field_unchanged():
    assert post("F3", text=CORRECT).json()["correct"] is True
    assert post("F4", text=DISTRIBUTION_ERROR).json()["correct"] is False


def test_attempted_at_is_first_request_utc():
    body = post("F5").json()
    assert body["attempted_at"].endswith("Z")
    repeat = post("F5").json()
    assert repeat["attempted_at"] == body["attempted_at"]


def test_conflict_still_409_on_different_body_same_attempt():
    post("F6", text=CORRECT)
    r = post("F6", text=DISTRIBUTION_ERROR)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"


def test_route_count_unchanged():
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "")
        not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
    }
    assert paths == {
        "/health",
        "/analyze-solution",
        "/api/v1/attempts/{attempt_id}/analyze",
        "/api/v1/attempts/{attempt_id}/ocr",
        "/api/v1/attempts/{attempt_id}/analyses",
        "/api/v1/attempts/{attempt_id}/analyses/{analysis_id}",
        "/api/v1/students/{student_id}/history",
        "/api/v1/students/{student_id}/misconceptions/top",
        "/api/v1/students/{student_id}/skills",
    }


def test_ocr_route_present_but_no_multipart_or_backend_analysis_path():
    """PHASE 9-8A 로 OCR 경로가 추가됐다. 단, multipart 업로드 경로와
    Backend 119 API 의 `/api/analysis` 는 계속 없다."""
    paths = {route.path for route in app.routes if getattr(route, "path", "")}
    assert "/api/v1/attempts/{attempt_id}/ocr" in paths
    assert not any("multipart" in p.lower() for p in paths), paths
    assert "/api/analysis" not in paths


def test_taxonomy_and_golden_unchanged():
    assert validate_all() == []
    for name in ("golden_20.json", "golden_100.json"):
        payload = json.loads((PROJECT_ROOT / "tests" / "golden" / name).read_text(encoding="utf-8"))
        assert payload["cases"], name


def test_student_history_item_shape_unchanged():
    post("F7")
    item = client.get("/api/v1/students/S1/history").json()["items"][0]
    assert len(item) == 23
    for forbidden in BACKEND_CODES_FIELDS:
        assert forbidden not in item, forbidden


def test_top_misconceptions_uses_taxonomy_ids_only():
    post("F8")
    top = client.get("/api/v1/students/S1/misconceptions/top").json()
    assert top["items"][0]["misconception_id"] == "2.1"
    assert "MIS-" not in json.dumps(top)
