"""PHASE 8-10: 학생별 반복 오개념 TOP 5 (웹 r30, 앱 r64).

  GET /api/v1/students/{student_id}/misconceptions/top?limit=5

집계 정책
  1. AI① frozen taxonomy 의 misconception_id 만 사용 (이름·설명은 taxonomy 조회)
  2. 한 Attempt 안의 중복 오개념은 attempt_count 1회
  3. 서로 다른 Attempt 반복 횟수로 순위
  4. 같은 Attempt 의 분석 이력이 여러 건이면 최신 AnalysisRecord 만 사용
  5. REVIEW_REQUIRED 도 확정 ErrorRecord 에 taxonomy ID 가 있으면 집계
     (UNKNOWN 은 저장되지 않음, 근거 없는 계산 오류는 ID 가 없음)
  6. attempt_count 내림차순, 동률은 misconception_id 오름차순
  7. student_id 검증은 이력 API 와 동일, 기록 없으면 빈 목록
  8. 최소 misconception_id / name / description / attempt_count / 관련 skill ID
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import get_repository, reset_repository
from app.pipeline.session import ErrorRecord, StudentAttempt, analyze_attempt
from app.taxonomy import MISCONCEPTION_CATALOG, misconception_by_id

client = TestClient(app)

TOP_URL = "/api/v1/students/{}/misconceptions/top"

# 실제 파이프라인이 만드는 페이로드 (problem_latex = 2(x-3)=6 고정)
M21 = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"          # 오류 1건 → 2.1
M31_DUP = "2(x-3)=6\n2x-3=3\n2x=3"              # 오류 2건 → 둘 다 3.1
M31 = "2(x-3)=6\n2x-3=3\n2x-3=3\n2x-3=3"       # 오류 1건 → 3.1
M21_REVIEW = "2(x-3)=6\n2x-3=6\n???\nx=6.5"      # REVIEW_REQUIRED + 확정 2.1
CALC_ONLY = "2(x-3)=6\n2x-6=7\n2x=13\nx=6.5"    # 계산 오류 → 오개념 근거 없음
CORRECT = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"        # 오류 없음

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


def post(attempt_id: str, solution_text: str, student_id: str = "S1"):
    return client.post(
        f"/api/v1/attempts/{attempt_id}/analyze",
        json={**BASE, "student_id": student_id, "solution_text": solution_text},
    )


def top(student_id: str = "S1", **params):
    return client.get(TOP_URL.format(student_id), params=params)


def items(student_id: str = "S1", **params) -> list[dict]:
    return top(student_id, **params).json()["items"]


def append_analysis(attempt_id: str, solution_text: str) -> str:
    """같은 attempt 에 분석 이력을 추가한다 (POST 멱등이라 저장소 직접 사용)."""
    repo = get_repository()
    stored = repo.get_attempt(attempt_id)
    analysis = analyze_attempt(
        repo.get_problem("P1"),
        StudentAttempt(
            attempt_id=attempt_id,
            student_id=stored.student_id,
            problem_id=stored.problem_id,
            solution_text=solution_text,
        ),
        solution=repo.get_correct_solution("SOL-1"),
    )
    return repo.save_analysis(analysis).analysis_id


def seed_misconceptions(attempt_id: str, misconception_ids: list[str], student_id="S1") -> str:
    """임의의 유효 taxonomy ID 를 담은 분석 record 를 만든다 (순위/절단 테스트용)."""
    repo = get_repository()
    stored = repo.get_attempt(attempt_id)
    analysis = analyze_attempt(
        repo.get_problem("P1"),
        StudentAttempt(
            attempt_id=attempt_id,
            student_id=student_id or stored.student_id,
            problem_id=stored.problem_id,
            solution_text=CORRECT,
        ),
        solution=repo.get_correct_solution("SOL-1"),
    )
    errors = [
        ErrorRecord(
            error_id=f"E-{attempt_id}-{index}",
            attempt_id=attempt_id,
            step_no=index,
            error_type="concept_error",
            error_subtype="distribution_omit",
            skill_ids=["distribution"],
            misconception_ids=[mid],
            confidence=0.9,
            evidence_latex="2x-3=6",
            description_ko="fixture",
        )
        for index, mid in enumerate(misconception_ids, start=1)
    ]
    patched = analysis.model_copy(update={"errors": errors})
    return repo.save_analysis(patched).analysis_id


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


# ===========================================================================
# 기본 집계
# ===========================================================================

def test_single_misconception_ranked():
    post("A1", M21)
    body = top().json()
    assert body["student_id"] == "S1"
    assert body["limit"] == 5
    assert body["order"] == "attempt_count_desc"
    assert body["distinct_misconception_count"] == 1
    assert [i["misconception_id"] for i in body["items"]] == ["2.1"]
    assert body["items"][0]["attempt_count"] == 1
    assert body["items"][0]["rank"] == 1


def test_item_carries_taxonomy_name_and_description():
    post("A1", M21)
    entry = items()[0]
    taxonomy = misconception_by_id("2.1")
    assert entry["name"] == taxonomy.name
    assert entry["description"] == taxonomy.description
    assert entry["related_skill_ids"] == taxonomy.related_skill_ids
    assert entry["name"] != "" and entry["description"] != ""


def test_item_field_set():
    post("A1", M21)
    assert set(items()[0]) == {
        "rank", "misconception_id", "name", "description", "attempt_count",
        "related_skill_ids",
    }


def test_repeated_attempts_increase_count():
    for name in ("A1", "A2", "A3"):
        post(name, M21)
    entry = items()[0]
    assert entry["misconception_id"] == "2.1"
    assert entry["attempt_count"] == 3


def test_distinct_attempts_rank_by_count():
    for name in ("A1", "A2", "A3", "A4"):
        post(name, M21)
    for name in ("B1", "B2"):
        post(name, M31)
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["2.1", "3.1"]
    assert [i["attempt_count"] for i in ranked] == [4, 2]
    assert [i["rank"] for i in ranked] == [1, 2]


def test_top5_truncates_but_reports_total():
    ids = ["1.1", "1.2", "2.1", "3.1", "4.1", "4.2", "5.1"]
    for index, mid in enumerate(ids):
        post(f"C{index}", CORRECT)
        seed_misconceptions(f"C{index}", [mid])
    body = top().json()
    assert body["distinct_misconception_count"] == 7
    assert len(body["items"]) == 5
    assert [i["misconception_id"] for i in body["items"]] == ids[:5]
    assert [i["rank"] for i in body["items"]] == [1, 2, 3, 4, 5]


def test_limit_controls_item_count():
    ids = ["1.1", "1.2", "2.1", "3.1", "4.1", "4.2"]
    for index, mid in enumerate(ids):
        post(f"D{index}", CORRECT)
        seed_misconceptions(f"D{index}", [mid])
    assert len(items(limit=1)) == 1
    assert len(items(limit=3)) == 3
    assert len(items(limit=6)) == 6
    assert len(items(limit=20)) == 6
    assert top(limit=3).json()["distinct_misconception_count"] == 6


# ===========================================================================
# 정책 2 — 같은 Attempt 안의 중복은 1회
# ===========================================================================

def test_duplicate_misconception_within_one_attempt_counted_once():
    post("E1", M31_DUP)  # 오류 2건, 둘 다 3.1
    errors = get_repository().get_latest_analysis("E1").analysis.errors
    assert len(errors) == 2, "픽스처가 두 오류를 만들어야 한다"
    assert {m for e in errors for m in e.misconception_ids} == {"3.1"}
    assert items()[0]["attempt_count"] == 1


def test_duplicate_does_not_inflate_alongside_other_attempts():
    post("E2", M31_DUP)
    post("E3", M31_DUP)
    post("E4", M31)
    assert items()[0]["misconception_id"] == "3.1"
    assert items()[0]["attempt_count"] == 3


def test_two_distinct_misconceptions_in_one_attempt_each_counted():
    post("E5", CORRECT)
    seed_misconceptions("E5", ["2.1", "3.1"])
    ranked = items()
    assert [(i["misconception_id"], i["attempt_count"]) for i in ranked] == [
        ("2.1", 1), ("3.1", 1)
    ]


def test_repeated_same_misconception_in_one_record_counted_once():
    post("E6", CORRECT)
    seed_misconceptions("E6", ["1.1", "1.1", "1.1"])
    assert [(i["misconception_id"], i["attempt_count"]) for i in items()] == [("1.1", 1)]


# ===========================================================================
# 정책 4 — 같은 Attempt 의 과거 분석 이력은 중복 집계되지 않음
# ===========================================================================

def test_latest_analysis_only_is_used():
    post("F1", M21)
    assert items()[0]["misconception_id"] == "2.1"
    # 같은 attempt 에 다른 오개념의 분석을 추가
    append_analysis("F1", M31)
    latest_ids = get_repository().get_latest_analysis("F1").analysis_id
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["3.1"], "과거 분석까지 합산됐다"
    assert latest_ids.endswith(latest_ids.split("#")[-1])
    assert len(get_repository().list_analyses("F1")) == 2


def test_multiple_history_entries_do_not_double_count():
    post("F2", M21)
    append_analysis("F2", M21)
    append_analysis("F2", M21)
    assert items()[0]["attempt_count"] == 1
    assert get_repository().list_analyses("F2").__len__() == 3


def test_latest_analysis_without_misconception_replaces_earlier_one():
    post("F3", M21)
    append_analysis("F3", CORRECT)  # 최신 분석은 오류 없음
    assert items() == []
    assert top().json()["distinct_misconception_count"] == 0


# ===========================================================================
# 정책 5 — REVIEW_REQUIRED / 계산 오류
# ===========================================================================

def test_review_required_with_confirmed_error_is_counted():
    r = post("G1", M21_REVIEW)
    assert r.json()["analysis_status"] == "REVIEW_REQUIRED"
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["2.1"]
    assert ranked[0]["attempt_count"] == 1


def test_calculation_error_without_misconception_is_excluded():
    r = post("G2", CALC_ONLY)
    assert r.json()["error_type"] == "calculation"
    assert r.json()["misconception_id"] is None
    assert items() == []


def test_correct_attempt_is_excluded():
    post("G3", CORRECT)
    assert items() == []


def test_unrecognized_submission_is_never_stored():
    r = post("G4", "보통은 이렇게 푼다고 합니다")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNRECOGNIZED"
    assert r.json()["detail"]["analysis_status"] == "UNKNOWN"
    assert items() == []


def test_mixed_excluded_and_included():
    post("H1", M21)
    post("H2", CALC_ONLY)
    post("H3", CORRECT)
    post("H4", M21_REVIEW)
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["2.1"]
    assert ranked[0]["attempt_count"] == 2


# ===========================================================================
# 정책 6 — 동률 정렬
# ===========================================================================

def test_tie_break_is_misconception_id_ascending():
    post("I1", CORRECT)
    seed_misconceptions("I1", ["4.2", "1.1", "3.1", "2.1"])
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["1.1", "2.1", "3.1", "4.2"]
    assert all(i["attempt_count"] == 1 for i in ranked)


def test_tie_break_order_stable_across_repeated_calls():
    for index, mid in enumerate(["5.1", "1.1", "3.1"]):
        post(f"J{index}", CORRECT)
        seed_misconceptions(f"J{index}", [mid])
    first = [i["misconception_id"] for i in items()]
    for _ in range(4):
        assert [i["misconception_id"] for i in items()] == first == ["1.1", "3.1", "5.1"]


def test_count_wins_over_id_order():
    post("K1", M21)
    post("K2", M21)
    post("K3", CORRECT)
    seed_misconceptions("K3", ["1.1"])
    ranked = items()
    assert [i["misconception_id"] for i in ranked] == ["2.1", "1.1"]
    assert [i["attempt_count"] for i in ranked] == [2, 1]


def test_rank_is_dense_and_one_based():
    for index, mid in enumerate(["1.1", "2.1"]):
        post(f"L{index}", CORRECT)
        seed_misconceptions(f"L{index}", [mid])
    assert [i["rank"] for i in items()] == [1, 2]


# ===========================================================================
# 정책 7 — 학생 격리 / 빈 결과 / 검증
# ===========================================================================

def test_history_is_isolated_per_student():
    post("M1", M21, "S1")
    post("M2", M21, "S1")
    post("M3", M21, "S2")
    post("M4", M31, "S3")
    assert items("S1")[0]["attempt_count"] == 2
    assert items("S2")[0]["attempt_count"] == 1
    assert [i["misconception_id"] for i in items("S3")] == ["3.1"]


def test_empty_for_unknown_student():
    body = top("NOBODY").json()
    assert body["student_id"] == "NOBODY"
    assert body["items"] == []
    assert body["distinct_misconception_count"] == 0


def test_empty_when_only_correct_or_calculation_attempts():
    post("N1", CORRECT)
    post("N2", CALC_ONLY)
    body = top().json()
    assert body["items"] == [] and body["distinct_misconception_count"] == 0


def test_student_id_is_trimmed_like_history_api():
    post("O1", M21)
    assert top(" S1 ").json()["student_id"] == "S1"


@pytest.mark.parametrize("student_id", ["S1%00", "S1%01x", " "])
def test_invalid_student_id_is_422(student_id):
    r = top(student_id)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_INVALID_STUDENT_ID"


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": 21}, {"limit": "many"}, {"limit": 2.5},
])
def test_invalid_limit_is_422(params):
    post("P1", M21)
    assert top(**params).status_code == 422


def test_limit_boundaries_accepted():
    post("P2", M21)
    assert top(limit=1).status_code == 200
    assert top(limit=20).status_code == 200


def test_attempt_without_analysis_is_skipped():
    post("Q1", M21)
    repo = get_repository()
    repo.save_attempt(repo.get_attempt("Q1").model_copy(update={"attempt_id": "Q1-bare"}))
    assert top().json()["distinct_misconception_count"] == 1


# ===========================================================================
# 읽기 전용
# ===========================================================================

def _snapshot() -> dict:
    rule = analyzer.get_engine("rule")
    bkt = analyzer.get_engine("bkt")
    repo = get_repository()
    return {
        "streak": dict(rule._streak),
        "state": dict(rule._state),
        "p": dict(bkt._p),
        "analyses": sorted(repo._analyses),
        "attempts": sorted(repo._attempts),
        "seq": repo._seq,
    }


def test_top_does_not_change_state_or_storage():
    for name, text in (("R1", M21), ("R2", M21), ("R3", M31), ("R4", CALC_ONLY)):
        post(name, text)
    before = _snapshot()
    for _ in range(5):
        assert top().status_code == 200
        assert top(limit=2).status_code == 200
        assert top("NOBODY").status_code == 200
    assert _snapshot() == before


def test_top_does_not_call_analyze_attempt(monkeypatch):
    from app import main as main_module

    post("S1x", M21)

    def boom(*args, **kwargs):
        raise AssertionError("조회 중 분석이 실행되었다")

    monkeypatch.setattr(main_module, "analyze_attempt", boom)
    assert top().status_code == 200
    assert top(limit=1).status_code == 200


def test_top_does_not_take_the_analysis_gate():
    post("T1", M21)
    repo = get_repository()
    repo.claim_analysis("T1")
    try:
        assert top().status_code == 200
    finally:
        repo.release_analysis("T1")


def test_top_does_not_advance_knowledge_state():
    post("U1", M21)
    rule = analyzer.get_engine("rule")
    for _ in range(3):
        top()
    assert rule._streak[("S1", "distribution")] == 0
    second = post("U2", M31).json()
    assert second["state"] == "needs_practice"


# ===========================================================================
# 제약 — taxonomy / concept 매핑 미사용
# ===========================================================================

def test_only_frozen_taxonomy_ids_are_returned():
    post("V1", M21)
    post("V2", M31)
    known = {m.misconception_id for m in MISCONCEPTION_CATALOG.misconceptions}
    for entry in items(limit=20):
        assert entry["misconception_id"] in known
        assert misconception_by_id(entry["misconception_id"]).name == entry["name"]


def test_response_has_no_concept_id_field():
    """Phase 8-9 보류 — concept_id / Skill↔Concept 매핑을 쓰지 않는다."""
    import json as _json

    post("V2", M21)
    body = _json.dumps(top().json(), ensure_ascii=False)
    # 'misconception' 이 'concept' 을 포함하므로 키/필드명으로 검사한다
    assert "concept_id" not in body
    assert '"concept"' not in body
    assert "concept_id" not in {k for e in items() for k in e}


def test_ai2_alias_is_not_stored_as_taxonomy_id():
    """SIGN_ERROR 류 AI② 전용 별칭은 taxonomy ID 로 쓰이지 않는다."""
    from app.taxonomy import is_valid_misconception_id

    for alias in ("SIGN_ERROR", "sign_error", "MISCONCEPTION_2_1", "2.1.0"):
        assert is_valid_misconception_id(alias) is False


def test_json_shape_is_flat():
    import json as _json

    post("X1", M21)
    body = top().json()
    assert set(body) == {
        "student_id", "limit", "order", "distinct_misconception_count", "items"
    }
    _json.dumps(body)  # 직렬화 가능


# ===========================================================================
# 회귀
# ===========================================================================

def test_route_registered():
    assert "/api/v1/students/{student_id}/misconceptions/top" in {r.path for r in app.routes}
    assert client.get("/health").json() == {"status": "ok"}


def test_history_api_unchanged():
    post("Y1", M21)
    body = client.get("/api/v1/students/S1/history").json()
    assert body["total"] == 1
    assert len(body["items"][0]) == 23


def test_post_contracts_unchanged():
    assert len(post("Z1", M21).json()) == 26
    assert len(client.post(
        "/analyze-solution",
        json={"problem_latex": "2(x-3)=6", "solution_text": "2(x-3)=6\n2x-3=6"},
    ).json()) == 16
