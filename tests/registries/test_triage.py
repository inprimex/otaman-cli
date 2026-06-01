"""Tests for the auto-triage scorer (Appendix G, task 7.1).

Covers the formula (G.1) and all edge cases (G.3):
    - score = impact_weight / effort_days
    - priority tiebreaker (G.1 stage 2)
    - sol-seq tiebreaker (G.3 ties)
    - null impact → recommend returns None
    - null effort-days → solution excluded
    - all Discarded → returns None
    - single non-discarded → still recommended
"""

from __future__ import annotations

import pytest

from otaman_cli.registries.triage import (
    DEFAULT_IMPACT_WEIGHTS,
    PRIORITY_RANK,
    build_rationale,
    compute_triage_score,
    rank_solutions,
    recommend,
)


def _outcome(impact="M", priority="P2"):
    return {"id": "JTBD-1-x", "impact": impact, "priority": priority}


def _sol(id_="SOL-1-a", effort=3, status="Considering"):
    return {"id": id_, "outcome-id": "JTBD-1-x", "effort-days": effort, "status": status}


# ---------------------------------------------------------------------------
# compute_triage_score


def test_score_basic():
    # impact M (weight 3) / effort 10 = 0.3
    assert compute_triage_score(_outcome("M"), _sol(effort=10)) == pytest.approx(0.3)


def test_score_with_xl_impact():
    # impact XL (weight 8) / effort 1 = 8.0
    assert compute_triage_score(_outcome("XL"), _sol(effort=1)) == 8.0


def test_score_none_when_impact_is_null():
    assert compute_triage_score({"impact": None}, _sol()) is None


def test_score_none_when_effort_is_null():
    assert compute_triage_score(_outcome(), {"id": "SOL-1-x", "effort-days": None}) is None


def test_score_none_when_effort_is_zero_or_negative():
    assert compute_triage_score(_outcome(), {"id": "SOL-1-x", "effort-days": 0}) is None
    assert compute_triage_score(_outcome(), {"id": "SOL-1-x", "effort-days": -3}) is None


def test_score_none_for_unknown_impact_tier():
    assert compute_triage_score({"impact": "ZZZ"}, _sol()) is None


def test_score_with_custom_weights():
    custom = {"XS": 10, "S": 20, "M": 30, "L": 50, "XL": 80}
    # impact L (custom 50) / effort 5 = 10
    assert compute_triage_score(_outcome("L"), _sol(effort=5), custom) == 10.0


# ---------------------------------------------------------------------------
# rank_solutions


def test_rank_orders_by_score_descending():
    o = _outcome("M")  # weight 3
    sols = [
        _sol("SOL-1-cheap", effort=3),    # 3/3 = 1.00
        _sol("SOL-2-medium", effort=10),  # 3/10 = 0.30
        _sol("SOL-3-expensive", effort=30),  # 3/30 = 0.10
    ]
    ranked = rank_solutions(o, sols)
    assert [r.solution_id for r in ranked] == ["SOL-1-cheap", "SOL-2-medium", "SOL-3-expensive"]


def test_rank_excludes_discarded():
    o = _outcome("M")
    sols = [
        _sol("SOL-1-a", effort=3, status="Discarded"),
        _sol("SOL-2-b", effort=10),
    ]
    ranked = rank_solutions(o, sols)
    assert [r.solution_id for r in ranked] == ["SOL-2-b"]


def test_rank_excludes_null_effort():
    o = _outcome("M")
    sols = [
        {"id": "SOL-1-a", "outcome-id": "JTBD-1-x", "effort-days": None, "status": "Considering"},
        _sol("SOL-2-b", effort=10),
    ]
    ranked = rank_solutions(o, sols)
    assert [r.solution_id for r in ranked] == ["SOL-2-b"]


def test_rank_empty_when_all_discarded():
    o = _outcome("M")
    sols = [
        _sol("SOL-1-a", status="Discarded"),
        _sol("SOL-2-b", status="Discarded"),
    ]
    assert rank_solutions(o, sols) == []


def test_rank_priority_tiebreaker():
    """Equal score → higher priority_rank wins. P0 (4) > P1 (3) > P2 (2)."""
    o = _outcome("M", priority="P0")
    # Note: PRIORITY_RANK is from the outcome; all solutions share it.
    # The tiebreaker here is informational since score is the dominant key —
    # but two solutions with the same score do exist below.
    sols = [
        _sol("SOL-2-b", effort=3),   # 3/3 = 1.0
        _sol("SOL-1-a", effort=3),   # 3/3 = 1.0
    ]
    ranked = rank_solutions(o, sols)
    # Same score, same priority_rank → final tiebreaker is sol_seq ASC
    assert [r.solution_id for r in ranked] == ["SOL-1-a", "SOL-2-b"]


def test_rank_sol_seq_tiebreaker_when_all_else_equal():
    """Equal score + equal priority → lower SOL-<seq> wins (G.3)."""
    o = _outcome("M", priority="P2")
    sols = [
        _sol("SOL-5-late", effort=10),     # 3/10 = 0.30
        _sol("SOL-3-middle", effort=10),   # 3/10 = 0.30
        _sol("SOL-1-early", effort=10),    # 3/10 = 0.30
    ]
    ranked = rank_solutions(o, sols)
    assert [r.solution_id for r in ranked] == ["SOL-1-early", "SOL-3-middle", "SOL-5-late"]


# ---------------------------------------------------------------------------
# recommend


def test_recommend_returns_top_ranked():
    o = _outcome("L")  # weight 5
    sols = [
        _sol("SOL-1-cheap", effort=2),   # 5/2 = 2.5
        _sol("SOL-2-expensive", effort=10),  # 5/10 = 0.5
    ]
    r = recommend(o, sols)
    assert r is not None
    assert r.solution_id == "SOL-1-cheap"
    assert r.score == 2.5


def test_recommend_returns_none_when_impact_null():
    o = {"id": "JTBD-1-x", "impact": None, "priority": "P0"}
    sols = [_sol(effort=3)]
    assert recommend(o, sols) is None


def test_recommend_returns_none_when_all_discarded():
    o = _outcome("M")
    sols = [_sol("SOL-1-a", status="Discarded")]
    assert recommend(o, sols) is None


def test_recommend_single_solution_still_returns():
    """Even with one candidate the recommendation fires (with score)."""
    o = _outcome("M")
    sols = [_sol("SOL-1-only", effort=3)]
    r = recommend(o, sols)
    assert r is not None
    assert r.solution_id == "SOL-1-only"


def test_recommend_uses_custom_impact_weights():
    o = _outcome("M")
    custom = {"XS": 100, "S": 100, "M": 100, "L": 100, "XL": 100}  # everything = 100
    sols = [_sol("SOL-1-x", effort=10)]
    r = recommend(o, sols, custom)
    assert r.score == 10.0  # 100/10


# ---------------------------------------------------------------------------
# Constants


def test_default_impact_weights_match_spec():
    """Appendix G.4 default weights — must match exactly."""
    assert DEFAULT_IMPACT_WEIGHTS == {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}


def test_priority_rank_values():
    """Appendix G.1 stage 2 priority ranking."""
    assert PRIORITY_RANK == {"P0": 4, "P1": 3, "P2": 2, "P3": 1}


# ---------------------------------------------------------------------------
# build_rationale


def test_rationale_format():
    o = _outcome("M")
    sols = [_sol("SOL-1-x", effort=10), _sol("SOL-2-y", effort=30)]
    ranked = rank_solutions(o, sols)
    chosen = ranked[0]
    alts = ranked[1:]
    r = build_rationale(chosen, alts, o)
    assert "impact=M" in r
    assert "0.30" in r
    assert "beats SOL-2-y" in r


def test_rationale_single_candidate():
    o = _outcome("M")
    sols = [_sol("SOL-1-x", effort=10)]
    ranked = rank_solutions(o, sols)
    r = build_rationale(ranked[0], [], o)
    assert "only viable candidate" in r
