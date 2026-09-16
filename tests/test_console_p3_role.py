"""console-ia-consolidation P3 — role shapes the queue, not the menu (4.1/4.2).

4.1 Home's needs-you list is filtered by the acting hat; a solo operator sees
    the UNION; navigation is never role-gated.
4.2 The `role_scope` inversion is fixed: scoping is additive EMPHASIS, never
    subtractive hiding, and an unresolved identity never sees MORE than a
    resolved hat.

The inversion was mine, shipped in team-mode 2.4b part 3: `founder` got
everything, `cto` got a reduced "costing" view, and an unresolved identity got
everything — so a CTO saw LESS THAN A STRANGER.
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli.console import bus
from otaman_cli.console.home import QUEUE_ROWS, queue_label, queue_rows_for_hats
from otaman_cli.console.registry_detail import (
    acting_hats,
    outcome_detail_text,
    role_emphasis,
    solution_detail_text,
)


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "human-roster": [
                    {"name": "cto-person", "email": "c@x.io", "roles": ["cto"]},
                    {"name": "founder-person", "email": "f@x.io", "roles": ["founder"]},
                    {"name": "dev-person", "email": "d@x.io", "roles": ["developer"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    strat = tmp_path / "strategy"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    (strat / "outcomes.yaml").write_text(
        yaml.dump(
            {
                "outcomes": [
                    {
                        "id": "JTBD-1",
                        "status": "Approved",
                        "priority": "P1",
                        "category": "growth",
                        "persona": "operator",
                        "estimate-requested": True,
                        "cost-accepted": False,
                        "chosen-solution": "SOL-1",
                        "product-notes": "keep it simple",
                        "statement": {
                            "as-a": "u",
                            "i-want-to": "w",
                            "incremental-outcome": "o",
                            "so-i-can": "s",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (strat / "solutions.yaml").write_text(
        yaml.dump(
            {
                "solutions": [
                    {
                        "id": "SOL-1",
                        "outcome-id": "JTBD-1",
                        "status": "Considering",
                        "t-shirt": "M",
                        "effort-days": 4,
                        "description": "a way",
                        "pros": ["fast"],
                        "cons": ["rough"],
                        "cto-notes": "reuse the lib",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return bus.Program(name="demo", root=root)


# ---------------------------------------------------------------------------
# 4.2 — the inversion


def test_a_cto_no_longer_sees_less_than_a_stranger(program, monkeypatch):
    """THE BUG. Previously `cto` got a reduced costing view while an unresolved
    identity got everything."""
    monkeypatch.setenv("OTAMAN_HUMAN", "cto-person")
    cto_text = outcome_detail_text(program, "JTBD-1", emphasis=role_emphasis(program))
    monkeypatch.setenv("OTAMAN_HUMAN", "nobody-in-the-roster")
    stranger_text = outcome_detail_text(program, "JTBD-1", emphasis=role_emphasis(program))

    for field in ("Category:", "Persona:", "Chosen-solution:", "Cost-accepted:"):
        assert field in cto_text, field
        assert field in stranger_text, field
    # and the CTO gets MORE signal, not less: emphasis markers
    assert "»" in cto_text
    assert "»" not in stranger_text


def test_emphasis_never_hides_any_group(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "cto-person")
    text = outcome_detail_text(program, "JTBD-1", emphasis={"costing"})
    # value fields are present despite costing emphasis
    assert "Category:" in text and "Persona:" in text and "keep it simple" in text


def test_solution_emphasis_never_hides(program):
    text = solution_detail_text(program, "SOL-1", emphasis={"costing"})
    assert "T-shirt:" in text and "CTO notes" in text  # emphasised
    assert "Pros" in text and "Cons" in text  # NOT hidden


def test_no_emphasis_still_shows_everything(program):
    text = solution_detail_text(program, "SOL-1")
    for field in ("T-shirt:", "Effort-days:", "Pros", "Cons", "CTO notes"):
        assert field in text, field


def test_unresolved_gets_no_emphasis_which_is_less_not_more(program, monkeypatch):
    """The direction that matters: unresolved ≤ resolved, never >."""
    monkeypatch.setenv("OTAMAN_HUMAN", "")
    assert role_emphasis(program) == set()
    monkeypatch.setenv("OTAMAN_HUMAN", "cto-person")
    assert role_emphasis(program) == {"costing"}
    monkeypatch.setenv("OTAMAN_HUMAN", "founder-person")
    assert role_emphasis(program) == {"costing", "value"}


def test_emphasis_banner_says_nothing_is_hidden(program):
    text = outcome_detail_text(program, "JTBD-1", emphasis={"costing"})
    assert "your focus" in text
    assert "everything below is shown" in text  # the promise is explicit


def test_acting_hats_resolves_and_degrades(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "cto-person")
    assert acting_hats(program) == {"cto"}
    monkeypatch.setenv("OTAMAN_HUMAN", "ghost")
    assert acting_hats(program) == set()


def test_unknown_hat_contributes_no_emphasis(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "dev-person")  # 'developer' has no emphasis
    assert role_emphasis(program) == set()


# ---------------------------------------------------------------------------
# 4.1 — the queue is filtered; navigation is not


def test_a_cto_sees_their_own_rows():
    assert queue_rows_for_hats({"cto"}) == ("solution_choices", "spec_review")


def test_a_founder_sees_value_and_cost_rows():
    rows = queue_rows_for_hats({"founder"})
    assert "cost_acceptance" in rows and "value_decisions" in rows


def test_an_approver_sees_pending_scrs():
    assert queue_rows_for_hats({"approver"}) == ("scr",)


def test_an_assignee_sees_their_tasks():
    assert queue_rows_for_hats({"developer"}) == ("assigned_tasks",)


def test_multiple_hats_union_their_rows():
    rows = queue_rows_for_hats({"cto", "approver"})
    assert set(rows) == {"solution_choices", "spec_review", "scr"}


def test_a_solo_operator_sees_the_union(program):
    """An unresolved human must never get an EMPTY queue — that would hide their
    own work, the same inversion 4.2 fixes on the detail surface."""
    assert queue_rows_for_hats(set()) == QUEUE_ROWS


def test_an_unknown_hat_also_falls_back_to_the_union():
    assert queue_rows_for_hats({"some-new-role"}) == QUEUE_ROWS


def test_rows_keep_display_order():
    rows = queue_rows_for_hats({"developer", "approver"})
    assert list(rows) == [r for r in QUEUE_ROWS if r in rows]


def test_every_row_has_a_human_label():
    for row in QUEUE_ROWS:
        assert queue_label(row) and queue_label(row) != row


# ---------------------------------------------------------------------------
# 4.1 — the counts


def test_cost_acceptance_counts_estimate_requested_but_unaccepted(program):
    from otaman_cli.console.home import _registry_queue_counts

    cost, _value, _choices = _registry_queue_counts(program)
    assert cost == 1


def test_value_decisions_counts_approved_without_a_chosen_solution(program, monkeypatch):
    from otaman_cli.console.home import _registry_queue_counts

    strat = __import__("pathlib").Path(__import__("os").environ["OTAMAN_STRATEGY_DIR"])
    (strat / "outcomes.yaml").write_text(
        yaml.dump({"outcomes": [{"id": "X", "status": "Approved"}]}), encoding="utf-8"
    )
    _cost, value, _choices = _registry_queue_counts(program)
    assert value == 1


def test_solution_choices_counts_multi_candidate_undecided(program):
    from otaman_cli.console.home import _registry_queue_counts

    strat = __import__("pathlib").Path(__import__("os").environ["OTAMAN_STRATEGY_DIR"])
    (strat / "outcomes.yaml").write_text(
        yaml.dump({"outcomes": [{"id": "X", "status": "Approved"}]}), encoding="utf-8"
    )
    (strat / "solutions.yaml").write_text(
        yaml.dump(
            {
                "solutions": [
                    {"id": "a", "outcome-id": "X", "status": "Considering"},
                    {"id": "b", "outcome-id": "X", "status": "Considering"},
                    {"id": "c", "outcome-id": "X", "status": "Discarded"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _c, _v, choices = _registry_queue_counts(program)
    assert choices == 1  # two LIVE candidates, none chosen


def test_counts_degrade_to_zero_without_registries(tmp_path, monkeypatch):
    from otaman_cli.console.home import _registry_queue_counts

    monkeypatch.delenv("OTAMAN_STRATEGY_DIR", raising=False)
    root = tmp_path / "bare"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("project: p\nversion: '1.0'\n", encoding="utf-8")
    assert _registry_queue_counts(bus.Program(name="p", root=root)) == (0, 0, 0)


def test_summary_carries_hats_and_rows(program, monkeypatch):
    from otaman_cli.console.home import build_home_summary

    monkeypatch.setenv("OTAMAN_HUMAN", "cto-person")
    s = build_home_summary(program)
    assert s.hats == {"cto"}
    assert s.queue_rows == ("solution_choices", "spec_review")


def test_navigation_is_not_role_gated(program, monkeypatch):
    """D6: browsing stays open to every hat. The hat changes the QUEUE only."""
    from otaman_cli.console.app import HomeScreen

    monkeypatch.setenv("OTAMAN_HUMAN", "dev-person")  # the narrowest hat
    keys = {b.key for b in HomeScreen.BINDINGS}
    assert {"m", "t", "a", "s", "d", "b", "l"} <= keys  # every door still bound


def test_a_bare_summary_defaults_to_the_union_not_an_empty_queue():
    """Regression: `queue_rows` defaulted to `()`, so any HomeSummary built
    without hats rendered "nothing waiting on you" over real counts — the 4.2
    inversion reintroduced through a dataclass default."""
    from otaman_cli.console.app import HomeScreen
    from otaman_cli.console.home import HomeSummary

    s = HomeSummary(scr_count=3, spec_review=1, changes_total=48)
    assert s.queue_rows == QUEUE_ROWS
    body = HomeScreen._body_text(s)
    assert "3 spec-change requests" in body
    assert "nothing waiting on you" not in body
