"""PHASE 8-8: Problem 표시 메타데이터 → 학생 이력.

정책
  1. 명세 표시 텍스트(r25 question / r33 concepts.name)를 Problem 에 additive 저장
  2. 단원/개념 표시명은 명시 메타데이터이며 skill_id 에서 파생하지 않음
  3. 최초 등록 때만 저장, 이미 등록된 문제는 저장본이 source of truth
  4. 이력 응답은 nullable 필드, 없으면 null (추측하지 않음)
  5. 기존 문제식/Skill 동작, Attempt 멱등성, attempted_at 정렬·페이지네이션 유지
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import Problem

client = TestClient(app)

CORRECT = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"
WRONG = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"

SOLUTION = {
    "solution_id": "SOL-1",
    "steps": [
        {"latex_text": "2(x-3)=6"},
        {"latex_text": "2x-6=6"},
        {"latex_text": "2x=12"},
        {"latex_text": "x=6"},
    ],
    "answer_latex": "x=6",
}

BASE = {
    "problem_id": "P1",
    "problem_latex": "2(x-3)=6",
    "problem_skills": ["linear_equation", "distribution"],
    "correct_solution": SOLUTION,
}

HISTORY_URL = "/api/v1/students/{}/history"

DISPLAY_KEYS = {"problem_text", "concept_name", "unit_name"}
ITEM_FIELDS = DISPLAY_KEYS | {
    "attempt_id", "problem_id", "attempted_at", "latest_analysis_id", "detail_path",
    "analysis_count", "analysis_status", "correct", "error_step",
    "first_error_step_index", "error_type", "error_subtype", "skill", "state",
    "mastery", "misconception_id", "misconception_tags", "error_count",
    "observation_count", "step_count",
}


def post(attempt_id: str, student_id: str = "S1", solution_text: str = CORRECT, **kw):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "student_id": student_id, "solution_text": solution_text, **kw},
    )


def history(student_id: str = "S1", **params):
    return client.get(HISTORY_URL.format(student_id), params=params)


def item(attempt_id: str, student_id: str = "S1") -> dict:
    items = {i["attempt_id"]: i for i in history(student_id, page_size=100).json()["items"]}
    return items[attempt_id]


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 모델 — additive / optional
# ===========================================================================

def test_problem_display_fields_default_to_none():
    problem = Problem(problem_id="P1", problem_latex="2x=6", skills=["linear_equation"])
    assert problem.question_text is None
    assert problem.concept_name is None
    assert problem.unit_name is None


def test_problem_accepts_display_metadata():
    problem = Problem(
        problem_id="P1", problem_latex="2x=6", skills=["linear_equation"],
        question_text="2x + 3 = 7일 때 x의 값은?", concept_name="일차방정식",
        unit_name="중1 수학 · 방정식",
    )
    assert problem.question_text == "2x + 3 = 7일 때 x의 값은?"
    assert problem.concept_name == "일차방정식"
    assert problem.unit_name == "중1 수학 · 방정식"


def test_existing_problem_constructor_signature_still_works():
    """하위호환: 기존 인자만으로 생성된다(새 필드는 모두 선택)."""
    problem = Problem(problem_id="P1", problem_latex="2x=6", skills=["linear_equation"])
    assert problem.problem_id == "P1" and problem.skills == ["linear_equation"]


def test_problem_rejects_unknown_skill_unchanged():
    with pytest.raises(ValueError):
        Problem(problem_id="P1", problem_latex="2x=6", skills=["NOT_A_SKILL"])


# ===========================================================================
# 하위호환 — 메타데이터 없는 기존 요청
# ===========================================================================

def test_request_without_metadata_still_works():
    r = post("A1")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 26, "기존 POST 응답 필드 수가 바뀌면 안 된다"
    assert body["correct"] is True


def test_history_returns_null_when_no_metadata():
    post("A2")
    entry = item("A2")
    assert entry["problem_text"] is None
    assert entry["concept_name"] is None
    assert entry["unit_name"] is None


def test_history_item_field_set_includes_display_keys():
    post("A3")
    assert set(item("A3")) == ITEM_FIELDS
    assert DISPLAY_KEYS <= set(item("A3"))


def test_null_metadata_preserves_all_other_fields():
    post("A4", "S1", WRONG)
    entry = item("A4")
    assert entry["misconception_id"] == "2.1"
    assert entry["error_step"] == 2
    assert entry["analysis_status"] == "ANALYZED"
    assert entry["problem_id"] == "P1"


# ===========================================================================
# 신규 Problem 등록 시 저장·조회
# ===========================================================================

def test_new_problem_stores_display_metadata():
    post(
        "B1",
        problem_question_text="2x + 3 = 7일 때 x의 값은?",
        problem_concept_name="일차방정식",
        problem_unit_name="중1 수학 · 방정식",
    )
    stored = get_repository().get_problem("P1")
    assert stored.question_text == "2x + 3 = 7일 때 x의 값은?"
    assert stored.concept_name == "일차방정식"
    assert stored.unit_name == "중1 수학 · 방정식"


def test_history_exposes_stored_display_metadata():
    post(
        "B2",
        problem_question_text="2x + 3 = 7일 때 x의 값은?",
        problem_concept_name="일차방정식",
        problem_unit_name="중1 수학 · 방정식",
    )
    entry = item("B2")
    assert entry["problem_text"] == "2x + 3 = 7일 때 x의 값은?"
    assert entry["concept_name"] == "일차방정식"
    assert entry["unit_name"] == "중1 수학 · 방정식"


@pytest.mark.parametrize("field,request_key,model_key,value", [
    ("problem_text", "problem_question_text", "question_text", "2x+3=7일 때 x의 값은?"),
    ("concept_name", "problem_concept_name", "concept_name", "일차방정식"),
    ("unit_name", "problem_unit_name", "unit_name", "중1 수학 · 방정식"),
])
def test_each_display_field_stores_independently(field, request_key, model_key, value):
    post("B3", **{request_key: value})
    assert getattr(get_repository().get_problem("P1"), model_key) == value
    assert item("B3")[field] == value
    others = DISPLAY_KEYS - {field}
    assert all(item("B3")[other] is None for other in others)


def test_partial_metadata_leaves_others_null():
    post("B4", problem_concept_name="일차방정식")
    entry = item("B4")
    assert entry["concept_name"] == "일차방정식"
    assert entry["problem_text"] is None
    assert entry["unit_name"] is None


# ===========================================================================
# 정책 3 — 저장본 우선 / 덮어쓰지 않음
# ===========================================================================

def test_stored_problem_metadata_is_not_overwritten():
    post("C1", problem_question_text="원본 텍스트", problem_concept_name="원본 단원",
         problem_unit_name="원본 표시 단원")
    post("C2", problem_question_text="바꿔치기 텍스트", problem_concept_name="WRONG",
         problem_unit_name="WRONG")
    stored = get_repository().get_problem("P1")
    assert stored.question_text == "원본 텍스트"
    assert stored.concept_name == "원본 단원"
    assert stored.unit_name == "원본 표시 단원"
    assert item("C1")["problem_text"] == "원본 텍스트"
    assert item("C2")["problem_text"] == "원본 텍스트"


def test_stored_problem_latex_is_not_overwritten_either():
    post("C3")
    post("C4", problem_latex="999=999", problem_question_text="다른 문제")
    stored = get_repository().get_problem("P1")
    assert stored.problem_latex == "2(x-3)=6"
    assert stored.question_text is None


def test_metadata_added_later_does_not_appear_retroactively():
    post("C5")
    assert item("C5")["concept_name"] is None
    post("C6", problem_concept_name="일차방정식")
    assert item("C5")["concept_name"] is None, "나중에 등록된 값이 기존 항목에 붙었다"
    assert item("C6")["concept_name"] is None, "저장본 미반영 값이 노출됐다"


def test_missing_latex_still_rejected_even_with_metadata():
    r = client.post(
        "/api/v1/attempts/C7/analyze",
        json={**BASE, "problem_id": "NEW", "problem_latex": None, "student_id": "S1",
              "solution_text": CORRECT, "problem_concept_name": "일차방정식"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_REFERENCE_INTEGRITY"


# ===========================================================================
# 정책 2/5 — 파생하지 않음
# ===========================================================================

def test_concept_name_is_not_derived_from_skills():
    post("D1", problem_skills=["linear_equation", "distribution"])
    entry = item("D1")
    assert entry["concept_name"] is None
    assert entry["unit_name"] is None


def test_concept_name_differs_from_skill_id_verbatim():
    post("D2", problem_concept_name="linear_equation")
    assert item("D2")["concept_name"] == "linear_equation", "값은 보존하되 대응시키지 않는다"


def test_metadata_is_per_problem():
    """문제별로 별도의 정답 풀이를 함께 등록해야 concept 연결이 성립한다."""

    def solve_for(solution_id: str):
        return {**SOLUTION, "solution_id": solution_id}

    post("D3", problem_id="PA", problem_latex="2(x-3)=6", problem_concept_name="분배법칙",
         correct_solution=solve_for("SOL-PA"))
    post("D4", problem_id="PB", problem_latex="2x=6", problem_concept_name="일차방정식",
         correct_solution=solve_for("SOL-PB"), solution_text="2x=6\nx=3",
         problem_skills=["linear_equation"])
    repo = get_repository()
    assert repo.get_problem("PA").concept_name == "분배법칙"
    assert repo.get_problem("PB").concept_name == "일차방정식"
    assert item("D3")["concept_name"] == "분배법칙"
    entries = {i["attempt_id"]: i for i in history(page_size=100).json()["items"]}
    assert entries["D4"]["concept_name"] == "일차방정식"


def test_taxonomy_names_are_not_used_as_fallback():
    """skills.json 의 name 은 표시명의 fallback 으로 쓰지 않는다(추측 금지)."""
    from app.taxonomy import skill_by_id

    post("D6", problem_skills=["linear_equation"])
    entry = item("D6")
    taxonomy_name = skill_by_id("linear_equation").name
    assert entry["concept_name"] is None
    assert entry["problem_text"] is None and entry["unit_name"] is None
    assert all(entry[key] != taxonomy_name for key in DISPLAY_KEYS)


# ===========================================================================
# 정책 5 — 기존 동작 유지
# ===========================================================================

def test_skills_and_classification_unchanged_with_metadata():
    posted = post(
        "E1", "S1", WRONG,
        problem_question_text="2(x-3)=6 일 때 x의 값은?", problem_concept_name="분배법칙",
    ).json()
    assert posted["skill"] == "distribution"
    assert posted["misconception_id"] == "2.1"
    assert posted["error_step"] == 2
    assert get_repository().get_problem("P1").skills == ["linear_equation", "distribution"]


def test_untrackable_skill_error_unchanged():
    r = post("E2", problem_skills=["factoring"], problem_concept_name="인수분해")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


def test_attempt_idempotency_with_metadata():
    ids = {
        post("F1", problem_concept_name="일차방정식").json()["analysis_id"]
        for _ in range(4)
    }
    assert len(ids) == 1
    assert history().json()["items"][0]["analysis_count"] == 1


def test_conflict_still_409_with_metadata():
    post("F2", problem_concept_name="일차방정식")
    r = post("F2", "S1", "2(x-3)=6\nx=3", problem_concept_name="다른 단원")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E_ATTEMPT_CONFLICT"
    assert get_repository().get_problem("P1").concept_name == "일차방정식"


def test_attempt_conflict_check_ignores_metadata():
    """attempt 본문 비교는 metadata 를 포함하지 않는다 (문제 식·학생·풀이만)."""
    post("F3", problem_concept_name="일차방정식")
    assert post("F3", "S1", CORRECT, problem_concept_name="바뀐 값").status_code == 200


def test_attempted_at_sorting_unchanged():
    for name in ("G1", "G2", "G3"):
        post(name, problem_concept_name="일차방정식")
    assert [i["attempt_id"] for i in history().json()["items"]] == ["G3", "G2", "G1"]
    assert history().json()["order"] == "attempted_at_desc"


def test_pagination_unchanged():
    for i in range(7):
        post(f"H{i}", problem_concept_name="일차방정식")
    assert len(history(page=1, page_size=3).json()["items"]) == 3
    assert len(history(page=2, page_size=3).json()["items"]) == 3
    assert [i["attempt_id"] for i in history(page=3, page_size=3).json()["items"]] == ["H0"]
    assert history(page=4, page_size=3).json()["items"] == []
    assert history(page=99).json()["total"] == 7
    paged = [
        i["attempt_id"]
        for page in (1, 2, 3, 4)
        for i in history(page=page, page_size=2).json()["items"]
    ]
    assert paged == [f"H{i}" for i in (6, 5, 4, 3, 2, 1, 0)]


def test_display_metadata_survives_pagination():
    for i in range(5):
        post(f"I{i}", problem_concept_name="일차방정식")
    entries = [
        i for page in (1, 2, 3)
        for i in history(page=page, page_size=2).json()["items"]
    ]
    assert len(entries) == 5
    assert all(e["concept_name"] == "일차방정식" for e in entries)


# ===========================================================================
# 읽기 전용
# ===========================================================================

def test_history_get_does_not_change_problem_or_state():
    post("J1", problem_concept_name="일차방정식")
    before = get_repository().get_problem("P1")
    snapshot = (before.question_text, before.concept_name, before.unit_name)
    rule = analyzer.get_engine("rule")
    for _ in range(4):
        history()
    after = get_repository().get_problem("P1")
    assert (after.question_text, after.concept_name, after.unit_name) == snapshot
    assert rule._streak[("S1", "linear_equation")] == 1


def test_history_without_registered_problem_is_impossible():
    """attempt 는 problem 존재를 보장받아 저장된다(무결성)."""
    post("J2")
    repo = get_repository()
    assert {a.problem_id for a in repo.list_attempts()} == {"P1"}
    assert history().json()["total"] == 1


# ===========================================================================
# 회귀
# ===========================================================================

def test_repository_interface_unchanged():
    repo = get_repository()
    for name in (
        "save_problem", "get_problem", "list_problems", "save_correct_solution",
        "get_correct_solution", "list_correct_solutions", "save_attempt", "get_attempt",
        "list_attempts", "save_analysis", "get_analysis", "list_analyses",
        "get_latest_analysis", "has_analysis", "claim_analysis", "release_analysis", "reset",
    ):
        assert hasattr(repo, name), name


def test_repository_stores_display_metadata():
    post("K1", problem_question_text="표시 텍스트", problem_concept_name="일차방정식")
    assert get_repository().get_problem("P1").question_text == "표시 텍스트"
    assert [p.problem_id for p in get_repository().list_problems()] == ["P1"]


def test_repository_duplicate_problem_still_rejected():
    repo = get_repository()
    post("K2")
    with pytest.raises(Exception):
        repo.save_problem(Problem(problem_id="P1", problem_latex="other"))


def test_attempt_detail_api_unchanged():
    posted = post("L1", "S1", WRONG, problem_concept_name="분배법칙").json()
    assert len(posted) == 26
    listing = client.get("/api/v1/attempts/L1/analyses").json()
    assert listing["analysis_count"] == 1
    assert len(listing["analyses"][0]) == 16
    detail = client.get(
        f"/api/v1/attempts/L1/analyses/{posted['analysis_id'].replace('#', '%23')}"
    ).json()
    assert len(detail) == 27
    assert detail["raw_result"]["misconception_ids"] == ["2.1"]


def test_analyze_solution_contract_unchanged():
    r = client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    )
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 16
    assert "problem_text" not in body and "concept_name" not in body
    assert body["misconception_id"] == "2.1"


def test_analyze_solution_creates_no_problem_metadata():
    client.post(
        "/analyze-solution",
        json={"problem_latex": "2x=6", "solution_text": "2x=6\nx=3", "student_id": "S1"},
    )
    assert get_repository().list_problems() == []
    assert history().json()["total"] == 0


def test_legacy_client_request_with_unknown_field_still_422():
    r = client.post(
        "/api/v1/attempts/M1/analyze",
        json={**BASE, "student_id": "S1", "solution_text": CORRECT, "bogus": 1},
    )
    assert r.status_code == 422
