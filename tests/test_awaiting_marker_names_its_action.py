"""The "◀ you" marker names WHICH act clears it, and a mis-linked outcome names the id.

Roman hit both on the live console (spec-agent 20261004T123258):

1. `otaman-init-dev-scaffold` rendered complete-unarchived with the awaiting-you marker
   on the VALUE lens, and `v` refused it — because it is ratify-blocked, not authored.
   A marker that says you-act-here with no action is the disabled-with-reason rule
   inverted: the row claimed an obligation and named no way to discharge it.

2. `(outcome id does not resolve — 5)` gave a count and no strings, so finding WHICH
   five and what they said meant a repo diff. All five were authoring typos
   (hand-retyped slugs, a range in the outcome field, prose where the id goes) — the
   kind of thing visible at a glance IF the offending string is on the row.

The cause of (1) is that `_awaiting_ids` is a UNION of three sources with three
different actions — lifecycle `next_actor=human` (`otaman ratify`),
`list_authored_changes` (`v`), and `blocked_by_decision` (answer it) — and the marker
named none of them. It now carries the action per id.
"""

from __future__ import annotations

from otaman_cli.console.tree import TreeNode


def _row(**kw) -> str:
    return TreeNode(kind="change", id=kw.pop("id", "c"), title="", **kw).display_label()


# ---------------------------------------------------------------------------
# the marker names the act


def test_the_ratify_blocked_row_names_ratify_not_a_bare_marker():
    """Roman's exact row."""
    row = _row(id="otaman-init-dev-scaffold", awaiting=True, awaiting_action="otaman ratify")

    assert "◀ you" in row
    assert "otaman ratify" in row, f"the marker still names no act: {row!r}"


def test_the_authored_row_names_the_console_key():
    row = _row(id="csp", awaiting=True, awaiting_action="v")

    assert "◀ you (v)" in row


def test_a_marker_with_no_known_action_still_renders():
    """Absence of a hint must not break the row — the marker is the thing a reader
    scans for, and losing it to a missing lookup would be worse than a vague one."""
    row = _row(awaiting=True)

    assert "◀ you" in row
    assert "(" not in row.split("◀ you")[1], "an empty hint rendered as empty parens"


# ---------------------------------------------------------------------------
# the mis-linked outcome names the offending string


def test_the_offending_outcome_id_is_on_the_row():
    row = _row(id="llm-router-backend", dangling_outcome="JTBD-58-llm-router-multi-backend")

    assert "JTBD-58-llm-router-multi-backend" in row
    assert "no such id" in row, "the string without the verdict reads like a valid link"


def test_a_resolved_row_carries_no_such_marker():
    assert "no such id" not in _row(id="csp")


# ---------------------------------------------------------------------------
# the mapping keeps every membership consumer working


def test_the_awaiting_mapping_still_answers_membership():
    """`_awaiting_ids` became id -> action. The filter's `:a` and the header count both
    ask only `id in awaiting`, so a dict must drop in without touching them — this is
    the assertion that says so rather than trusting that it compiles."""
    from otaman_cli.console.filter_grammar import Query, Term, matches

    awaiting = {"csp": "v", "omo": "otaman ratify"}

    class _Row:
        id = "csp"

    assert matches(Query(terms=[Term("awaiting")]), _Row(), awaiting=awaiting)

    class _Other:
        id = "not-awaiting"

    assert not matches(Query(terms=[Term("awaiting")]), _Other(), awaiting=awaiting)


def test_a_decision_required_block_WINS_an_overlap():
    """Order matters in `_awaiting_ids`: an unanswered decision-required is a
    PREREQUISITE, so a change that is both authored and decision-blocked must send the
    operator to the decision, not to `v`."""
    import inspect

    from otaman_cli.console.app import TreeScreen

    src = inspect.getsource(TreeScreen._awaiting_ids)
    v_at = src.index('"v"')
    decision_at = src.index("blocked_by_decision")

    assert v_at < decision_at, (
        "blocked_by_decision must be applied AFTER the authored set so it wins"
    )
