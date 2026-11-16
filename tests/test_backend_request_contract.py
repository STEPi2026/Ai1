"""PHASE 9-7.1 — Backend 요청 계약(tracking Skill) 및 검증 테스트 보완.

검증 범위
  - tracking Skill 이 최소 1개여야 한다는 Backend 요청 계약
  - 누락 / 빈 목록 / group node 만 / 미등록 ID → 명확한 422
  - group node 는 함께 선언할 수 있지만 관측 대상이 아니다
  - 승인·provisional SKL code 는 기존 adapter 표에 있는 값만 역변환한다
  - `linear_equation` 은 내부 ID 로 전달 가능, 공식 Backend code 는 미매핑
  - tracking Skill 이 있을 때 정답·오답 분석 / code 출력 / 멱등 재요청이 유지된다

기존 테스트는 삭제하거나 완화하지 않는다 — 여기서는 **추가**만 한다.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.integration import backend_adapter as ba
from app.main import app
from app.pipeline import analyzer
from app.pipeline.repository import RepositoryError, get_repository, reset_repository
from app.taxonomy import is_known_skill, is_tracking_skill

client = TestClient(app)

PROBLEM = "2(x-3)=6"
DISTRIBUTION_ERROR = "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"
CORRECT = "2(x-3)=6\n2x-6=6\n2x=12\nx=6"


@pytest.fixture(autouse=True)
def _isolate():
    analyzer.reset_knowledge()
    reset_repository()
    yield
    analyzer.reset_knowledge()
    reset_repository()


def post(attempt_id, *, student_id="S1", problem_id="P1", problem_latex=PROBLEM,
         skills=("linear_equation", "distribution"), text=DISTRIBUTION_ERROR,
         omit_skills=False):
    body = {
        "problem_id": problem_id,
        "problem_latex": problem_latex,
        "student_id": student_id,
        "solution_text": text,
    }
    if not omit_skills:
        body["problem_skills"] = list(skills)
    return client.post(f"/api/v1/attempts/{attempt_id}/analyze", json=body)


# ===========================================================================
# 1. tracking Skill 계약 — 거절
# ===========================================================================

def test_missing_problem_skills_rejected_422():
    r = post("C1", omit_skills=True)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


def test_empty_problem_skills_rejected_422():
    r = post("C2", skills=())
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


def test_group_node_only_rejected_422():
    """group node 는 관측 대상이 아니므로 단독으로는 부족하다."""
    r = post("C3", skills=("factoring",))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"


def test_group_node_with_tracking_skill_accepted():
    r = post("C4", skills=("factoring", "linear_equation"))
    assert r.status_code == 200
    assert r.json()["skill"] in ("linear_equation", "distribution")


def test_unknown_skill_id_rejected_422_not_server_error():
    """taxonomy 에 없는 ID 는 500 이 아니라 명확한 422 로 거절한다."""
    r = post("C5", skills=("NOT_A_SKILL",))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "E_UNKNOWN_SKILL"


def test_unknown_skill_id_stored_nothing():
    post("C6", skills=("NOT_A_SKILL",))
    assert get_repository().has_analysis("C6") is False
    with pytest.raises(RepositoryError):
        get_repository().get_problem("P1")


def test_rejection_does_not_guess_a_default_skill():
    """기본 Skill 로 치환하지 않는다 — 응답에도 분석 결과가 없다."""
    for attempt_id, kwargs in [
        ("C7", {"omit_skills": True}),
        ("C8", {"skills": ()}),
        ("C9", {"skills": ("factoring",)}),
    ]:
        r = post(attempt_id, **kwargs)
        assert r.status_code == 422, attempt_id
        assert "skill" not in r.json(), attempt_id
        assert get_repository().has_analysis(attempt_id) is False


def test_rejection_message_names_the_unusable_skill_declaration():
    """거절 메시지는 관측 불가 사유를 밝힌다 (문제 식별자나 추측값을 넣지 않는다)."""
    group_only = post("C10", skills=("factoring",))
    assert "Knowledge State" in group_only.json()["detail"]["message"]
    assert "factoring" in group_only.json()["detail"]["message"]

    empty = post("C11", skills=())
    assert empty.json()["detail"]["code"] == "E_SKILL_NOT_TRACKABLE"
    assert "Knowledge State" in empty.json()["detail"]["message"]


# ===========================================================================
# 2. adapter 계약 함수 (단위)
# ===========================================================================

def test_require_problem_skills_passes_through_valid_set():
    assert ba.require_problem_skills(["linear_equation", "factoring"]) == [
        "linear_equation",
        "factoring",
    ]


@pytest.mark.parametrize("empty", [None, [], ()])
def test_require_problem_skills_rejects_empty(empty):
    with pytest.raises(ba.NoTrackingSkillError):
        ba.require_problem_skills(empty)


def test_require_problem_skills_rejects_group_node_only():
    with pytest.raises(ba.NoTrackingSkillError):
        ba.require_problem_skills(["factoring", "polynomial_arithmetic"])


def test_require_problem_skills_rejects_unknown_id():
    with pytest.raises(ba.UnknownSkillIdError):
        ba.require_problem_skills(["linear_equation", "NOT_A_SKILL"])


def test_no_tracking_skill_error_code_matches_pipeline_contract():
    """같은 사실(추적 불가)에 하나의 코드만 존재해야 한다."""
    from app.pipeline.analyzer import UntrackableSkillError

    assert ba.NoTrackingSkillError.code == UntrackableSkillError("x").code
    assert ba.UnknownSkillIdError.code == "E_UNKNOWN_SKILL"


def test_require_problem_skills_accepts_every_tracking_skill():
    from app.taxonomy import all_skill_ids, load_skill_catalog

    tracking = [s for s in all_skill_ids(load_skill_catalog()) if is_tracking_skill(s)]
    assert tracking, "tracking skill 이 하나도 없다"
    for skill_id in tracking:
        assert ba.require_problem_skills([skill_id]) == [skill_id]


# ===========================================================================
# 3. SKL code 역변환 — adapter 표에 있는 값만
# ===========================================================================

def test_approved_codes_resolve_to_tracking_skill():
    skill_id = ba.skill_id_for_code("SKL-1-004")
    assert is_known_skill(skill_id)
    assert is_tracking_skill(skill_id)


def test_seed_outside_code_is_not_resolved():
    """SKL-2-037 은 공식 Seed 밖 — A2 로 역변환 대상에서 제외됐다."""
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.skill_id_for_code("SKL-2-037")


def test_unknown_code_rejected_before_touching_taxonomy():
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.skill_id_for_code("SKL-9-999")


def test_linear_equation_is_accepted_as_internal_id():
    """`linear_equation` 은 내부 ID 로 전달할 수 있다."""
    r = post("S1", skills=("linear_equation",), text=CORRECT)
    assert r.status_code == 200
    assert r.json()["skill"] == "linear_equation"


def test_linear_equation_backend_code_stays_unmapped():
    """공식 Backend code 는 미매핑 — 지어내지 않는다."""
    assert ba.skill_code("linear_equation") is None
    assert ba.official_skill_codes(["linear_equation"]) == []
    assert ba.provisional_skill_codes(["linear_equation"]) == []
    with pytest.raises(ba.UnknownSkillCodeError):
        ba.skill_id_for_code("SKL-1-001")  # 어떤 코드도 매핑되어 있지 않다


def test_code_resolved_request_keeps_observation_target():
    r = post("S2", skills=("SKL-1-004", "linear_equation"))
    assert r.status_code == 200
    assert "distribution" in get_repository().get_problem("P1").skills


# ===========================================================================
# 4. tracking Skill 이 있을 때 기존 동작 유지
# ===========================================================================

def test_correct_analysis_with_tracking_skill():
    r = post("A1", skills=("linear_equation",), text=CORRECT)
    body = r.json()
    assert r.status_code == 200
    assert body["correct"] is True
    assert body["analysis_status"] == "ANALYZED"
    assert body["misconception_tag"] is None
    assert body["states"] == {"linear_equation": "mastered"} or (
        body["states"] == {"linear_equation": "learning"}
    )
    assert "linear_equation" in body["mastery_by_skill"]


def test_incorrect_analysis_with_tracking_skill_exposes_approved_code():
    r = post("A2", skills=("linear_equation", "distribution"))
    body = r.json()
    assert r.status_code == 200
    assert body["correct"] is False
    assert body["misconception_tag"] == "2.1"
    assert body["official_skill_codes"] == ["SKL-1-004"]
    assert body["official_misconception_codes"] == ["MIS-1-004"]
    assert body["states"]["distribution"] == "needs_practice"
    assert body["mastery_by_skill"]["distribution"] == 0.3


def test_incorrect_analysis_keeps_internal_id_without_seed_outside_code():
    """내부 3.2 는 관측되지만 시드 밖 Backend 코드는 나가지 않는다 (A2).

    내부 taxonomy 관측(state 갱신)은 그대로 유지된다 — 제거한 것은 Backend 코드뿐이다.
    """
    r = post(
        "A3",
        problem_latex="2x+6=14",
        problem_id="P2",
        skills=("linear_equation", "transposition"),
        text="2x+6=14\n2x=14",
    )
    body = r.json()
    assert r.status_code == 200
    assert body["misconception_tag"] == "3.2"
    assert body["provisional_skill_codes"] == []
    assert body["provisional_misconception_codes"] == []
    assert body["states"]["transposition"] == "needs_practice"


def test_idempotent_repost_with_tracking_skill():
    first = post("A4", skills=("linear_equation",), text=CORRECT).json()
    second = post("A4", skills=("linear_equation",), text=CORRECT).json()
    assert second["analysis_id"] == first["analysis_id"]
    assert second["analysis_count"] == 1
    assert second["mastery_by_skill"] == first["mastery_by_skill"]
    assert len(get_repository().list_analyses("A4")) == 1


def test_idempotent_repost_may_omit_problem_skills():
    """저장된 문제가 source of truth — 재요청에서 problem_skills 를 생략해도 된다."""
    first = post("A5", skills=("linear_equation",), text=CORRECT).json()
    repeat = post("A5", omit_skills=True, text=CORRECT).json()
    assert repeat["analysis_id"] == first["analysis_id"]
    assert repeat["correct"] is True


def test_repeat_does_not_double_observe():
    """멱등 재요청이 Knowledge State 를 두 번 갱신하지 않는다."""
    body = post("A6", skills=("linear_equation",), text=CORRECT).json()
    after_first = body["mastery_by_skill"]["linear_equation"]
    for _ in range(3):
        post("A6", skills=("linear_equation",), text=CORRECT)
    assert get_engine_mastery("A6") == after_first


def get_engine_mastery(attempt_id):
    """저장된 최신 분석 스냅샷의 mastery (읽기 전용 확인용)."""
    latest = get_repository().get_latest_analysis(attempt_id)
    return dict(latest.analysis.mastery)["linear_equation"]


def test_detail_get_is_read_only_with_tracking_skill():
    posted = post("A7", skills=("linear_equation",), text=CORRECT).json()
    detail = client.get(
        "/api/v1/attempts/A7/analyses/"
        + posted["analysis_id"].replace("#", "%23")
    ).json()
    assert detail["mastery_by_skill"] == posted["mastery_by_skill"]
    assert detail["misconception_tag"] is None
    assert detail["official_skill_codes"] == posted["official_skill_codes"]


def test_review_required_with_tracking_skill():
    r = post("A8", skills=("linear_equation", "distribution"),
             text="2(x-3)=6\n2x-3=6\n???\nx=6.5")
    body = r.json()
    assert r.status_code == 200
    assert body["analysis_status"] == "REVIEW_REQUIRED"
    assert body["official_misconception_codes"] == ["MIS-1-004"]


def test_unknown_with_tracking_skill_persists_nothing():
    r = post("A9", skills=("linear_equation",), text="???")
    assert r.status_code == 422
    assert r.json()["detail"]["analysis_status"] == "UNKNOWN"
    assert get_repository().has_analysis("A9") is False
    with pytest.raises(RepositoryError):
        get_repository().get_attempt("A9")


# ===========================================================================
# 5. 오류 응답 봉투 불변
# ===========================================================================

@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"omit_skills": True}, "E_SKILL_NOT_TRACKABLE"),
        ({"skills": ()}, "E_SKILL_NOT_TRACKABLE"),
        ({"skills": ("factoring",)}, "E_SKILL_NOT_TRACKABLE"),
        ({"skills": ("NOT_A_SKILL",)}, "E_UNKNOWN_SKILL"),
        ({"skills": ("SKL-9-999",)}, "E_UNKNOWN_SKILL_CODE"),
    ],
)
def test_all_rejections_use_detail_wrapper(kwargs, code):
    r = post("D1", **kwargs)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == code
    assert set(detail) == {"code", "message"}, detail
    assert isinstance(detail["message"], str) and detail["message"]


def test_backend_code_never_overwrites_taxonomy_field():
    """Backend 코드는 전용 배열에만 나오고 taxonomy 필드를 덮어쓰지 않는다."""
    r = post("D2", skills=("SKL-1-004",), text=CORRECT)
    body = r.json()
    assert r.status_code == 200
    assert body["skill"] == "distribution"          # taxonomy ID 유지
    assert body["misconception_id"] is None
    assert body["official_skill_codes"] == ["SKL-1-004"]  # 코드 전용 배열
    code_fields = {
        "official_skill_codes",
        "official_misconception_codes",
        "provisional_skill_codes",
        "provisional_misconception_codes",
    }
    legacy = {k: v for k, v in body.items() if k not in code_fields}
    legacy_payload = json.dumps(legacy)
    assert "SKL-" not in legacy_payload
    assert "MIS-" not in legacy_payload