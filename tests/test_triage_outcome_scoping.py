"""`rank_solutions` ranks ONE outcome's candidates (cofounder-agent 20260921T115837).

cpo-agent noticed every outcome being recommended the same solution; cofounder
traced it to one unfiltered argument. `rank_solutions` excluded only Discarded
solutions and ones without effort-days — never the ones belonging to a DIFFERENT
outcome — so the whole register was scored against whichever outcome was being
evaluated and the global winner was returned every time.

Appendix G is explicit, so this is conformance, not a canon gap:

    "The triage score ranks solutions for a single outcome. It does NOT compare
     solutions across different outcomes."

Measured on the live registry before the fix: one Tiny 1-day solution won for
all 85 outcomes, against 48 candidates each, and belonged to none of them.
After: 18 outcomes get a recommendation, 18 distinct winners, each its own.

The consequence was not noisy output — a bad recommendation can ride all the
way to a CEO-hat accept-cost.
"""

from __future__ import annotations

import pytest

from otaman_cli.registries import triage

OUTCOME = {"id": "JTBD-1-alpha", "impact": "XL", "priority": "P0"}
OTHER = {"id": "JTBD-2-beta", "impact": "M", "priority": "P1"}

MINE_BIG = {"id": "SOL-11-mine", "outcome-id": "JTBD-1-alpha", "effort-days": 10}
MINE_SMALL = {"id": "SOL-12-mine", "outcome-id": "JTBD-1-alpha", "effort-days": 5}
#: the shape that won everything: tiny effort, so the best value-rate anywhere
FOREIGN_TINY = {"id": "SOL-113-foreign", "outcome-id": "JTBD-2-beta", "effort-days": 1}

ALL = [MINE_BIG, MINE_SMALL, FOREIGN_TINY]


def _ids(results):
    return [r.solution_id for r in results]


def test_a_foreign_solution_is_never_ranked():
    """THE DEFECT: the tiny foreign solution outscored every real candidate."""
    assert "SOL-113-foreign" not in _ids(triage.rank_solutions(OUTCOME, ALL))


def test_only_this_outcomes_candidates_are_returned():
    assert set(_ids(triage.rank_solutions(OUTCOME, ALL))) == {"SOL-11-mine", "SOL-12-mine"}


def test_the_winner_belongs_to_the_outcome():
    ranked = triage.rank_solutions(OUTCOME, ALL)
    assert ranked[0].solution_id.endswith("-mine")


def test_each_outcome_gets_its_own_winner():
    """Before, both outcomes were recommended the same global winner."""
    a = triage.rank_solutions(OUTCOME, ALL)[0].solution_id
    b = triage.rank_solutions(OTHER, ALL)[0].solution_id
    assert a != b
    assert b == "SOL-113-foreign"  # it IS the right answer for its own outcome


def test_an_outcome_with_no_candidates_emits_nothing():
    """cpo-agent asked for exactly this: an outcome with no solutions of its own
    must emit nothing rather than borrow someone else's. `if not ranked` at the
    call site then already does the right thing."""
    lonely = {"id": "JTBD-9-none", "impact": "L", "priority": "P1"}
    assert triage.rank_solutions(lonely, ALL) == []
    assert triage.recommend(lonely, ALL) is None


def test_recommend_is_scoped_too():
    """Scoped inside `rank_solutions`, not at the caller, so `recommend` and
    every future caller inherit it rather than repeating the filter."""
    assert triage.recommend(OUTCOME, ALL).solution_id.endswith("-mine")


def test_discarded_and_effortless_exclusions_still_apply():
    rows = ALL + [
        {
            "id": "SOL-13-mine",
            "outcome-id": "JTBD-1-alpha",
            "effort-days": 1,
            "status": "Discarded",
        },
        {"id": "SOL-14-mine", "outcome-id": "JTBD-1-alpha"},  # no effort-days
    ]
    assert set(_ids(triage.rank_solutions(OUTCOME, rows))) == {"SOL-11-mine", "SOL-12-mine"}


def test_an_outcome_without_an_id_still_ranks():
    """Defensive: a malformed outcome must not silently rank nothing — that
    would trade a wrong recommendation for a missing one."""
    anon = {"impact": "XL", "priority": "P0"}
    assert len(triage.rank_solutions(anon, ALL)) == 3


@pytest.mark.parametrize("key", ["outcome-id", "outcome_id"])
def test_only_the_canonical_key_is_read(key):
    """The registry writes `outcome-id`; a solution using the underscore form is
    not this outcome's, and guessing would resurrect the bug in miniature."""
    row = {"id": "SOL-99-x", key: "JTBD-1-alpha", "effort-days": 2}
    ranked = _ids(triage.rank_solutions(OUTCOME, [row]))
    assert ranked == (["SOL-99-x"] if key == "outcome-id" else [])


def test_appendix_g_is_quoted_where_the_rule_lives():
    """The comment must say WHY, so the filter is not 'simplified' away."""
    import inspect

    src = inspect.getsource(triage.rank_solutions)
    assert "Appendix G" in src
    assert "across different outcomes" in src
