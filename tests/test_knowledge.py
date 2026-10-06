"""Knowledge State — Stage 0 규칙 기반 + Stage 1 BKT."""
from app.pipeline.knowledge import BKTEngine, RuleStateEngine


def test_rule_consecutive_correct_reaches_mastered():
    e = RuleStateEngine()
    assert e.update("s1", "distribution", "correct") == "learning"
    assert e.update("s1", "distribution", "correct") == "mastered"


def test_rule_concept_error_forces_needs_practice():
    e = RuleStateEngine()
    e.update("s1", "distribution", "correct")
    e.update("s1", "distribution", "correct")
    assert e.update("s1", "distribution", "concept_error") == "needs_practice"


def test_rule_calculation_error_stays_learning():
    e = RuleStateEngine()
    assert e.update("s1", "arithmetic", "calculation") == "learning"


def test_rule_skill_isolated():
    e = RuleStateEngine()
    e.update("s1", "distribution", "concept_error")
    assert e.state("s1", "transposition") == "learning"  # 기본값
    assert e.state("s1", "distribution") == "needs_practice"


def test_bkt_correct_increases_mastery():
    e = BKTEngine()
    before = e.mastery("s1", "linear_equation")
    after = e.update("s1", "linear_equation", "correct")
    assert after > before


def test_bkt_wrong_decreases_mastery():
    e = BKTEngine()
    p = e.p_init
    for _ in range(3):
        p = e.update("s1", "linear_equation", "concept_error")
    assert p < e.p_init


def test_bkt_state_thresholds():
    e = BKTEngine()
    e._p[("s1", "x")] = 0.85
    assert e.state("s1", "x") == "mastered"
    e._p[("s1", "x")] = 0.5
    assert e.state("s1", "x") == "learning"
    e._p[("s1", "x")] = 0.2
    assert e.state("s1", "x") == "needs_practice"


def test_bkt_bounds():
    e = BKTEngine()
    p = e.p_init
    for _ in range(50):
        p = e.update("s1", "x", "correct")
    assert 0.0 <= p <= 1.0
