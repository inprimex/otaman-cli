"""console-lens-navigation-and-filtering 1.3 — the `:` grammar (D2).

The grammar is a module rather than a method on the screen precisely so it can
be tested like this: text in, predicate out, no terminal. These cover the parts
where a filter language usually goes wrong — ambiguity, conjunction, and the
difference between a filter and a view command — plus the tree-specific rule
that a filter must not hide the path to its own matches.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from otaman_cli.console.filter_grammar import (
    VIEW_COLLAPSE_ALL,
    VIEW_EXPAND_ALL,
    matches,
    matches_tree,
    parse,
)

STATUSES = ("approved", "backlog", "complete", "considering", "discarded", "done", "drafting")


def _row(**kw):
    kw.setdefault("id", "")
    kw.setdefault("title", "")
    kw.setdefault("status", "")
    kw.setdefault("priority", None)
    return SimpleNamespace(**kw)


# ---------------------------------------------------------------------------
# parsing


def test_priority_term():
    q = parse("p1", statuses=STATUSES)
    assert q.error is None
    assert q.describe() == "P1"


def test_status_prefix_resolves():
    q = parse("sappr", statuses=STATUSES)
    assert q.error is None
    assert q.describe() == "approved"


def test_ambiguous_status_prefix_lists_candidates_instead_of_guessing():
    """The important one: silently picking `done` over `drafting` would filter
    the view to something the reader never asked for and cannot see."""
    q = parse("sd", statuses=STATUSES)
    assert q.error is not None
    assert "ambiguous" in q.error
    assert "discarded" in q.error and "done" in q.error and "drafting" in q.error


def test_an_exact_status_is_not_ambiguous_with_its_own_prefixes():
    """`done` is a prefix of nothing else here, but `complete` would be
    ambiguous with a hypothetical `complete-unarchived` — an exact hit wins."""
    q = parse("scomplete", statuses=("complete", "complete-unarchived"))
    assert q.error is None
    assert q.describe() == "complete"


def test_unknown_status_prefix_says_what_is_available():
    q = parse("szzz", statuses=STATUSES)
    assert q.error is not None and "no status starts with" in q.error
    assert "approved" in q.error


def test_text_term_takes_the_rest_of_the_line():
    q = parse("t login flow", statuses=STATUSES)
    assert q.error is None
    assert q.describe() == '"login flow"'


def test_bare_t_is_an_error_not_an_empty_match():
    q = parse("t", statuses=STATUSES)
    assert q.error is not None and "needs a term" in q.error


def test_conjunction_by_juxtaposition():
    q = parse("p1 sappr a", statuses=STATUSES)
    assert q.error is None
    assert q.describe() == "P1 · approved · awaiting you"


def test_unknown_token_is_reported_rather_than_ignored():
    q = parse("p1 wat", statuses=STATUSES)
    assert q.error is not None and "wat" in q.error


def test_view_commands_are_not_filters():
    for text, expected in (("e+", VIEW_EXPAND_ALL), ("e-", VIEW_COLLAPSE_ALL)):
        q = parse(text, statuses=STATUSES)
        assert q.view == expected
        assert q.terms == ()
        assert q.describe() == ""


def test_a_view_command_composes_with_a_filter():
    """Why `e+` lives in the grammar instead of on a bare key (D2)."""
    q = parse("p1 e+", statuses=STATUSES)
    assert q.view == VIEW_EXPAND_ALL
    assert q.describe() == "P1"


def test_empty_input_is_empty_not_an_error():
    q = parse("", statuses=STATUSES)
    assert q.error is None and q.is_empty


# ---------------------------------------------------------------------------
# matching


def test_priority_matching_uses_the_shared_shortener():
    q = parse("p1", statuses=STATUSES)
    assert matches(q, _row(priority="P1"))
    assert not matches(q, _row(priority="P2"))


def test_status_matching_is_on_the_displayed_word():
    q = parse("sappr", statuses=STATUSES)
    assert matches(q, _row(status="Approved"))
    assert not matches(q, _row(status="Discarded"))


def test_text_matches_id_title_and_description():
    q = parse("t login", statuses=STATUSES)
    assert matches(q, _row(id="add-login-flow"))
    assert matches(q, _row(title="Rework the LOGIN screen"))
    assert matches(
        q, SimpleNamespace(id="x", title="", status="", priority=None, description="about login")
    )
    assert not matches(q, _row(id="unrelated"))


def test_awaiting_uses_the_set_it_is_handed():
    q = parse("a", statuses=STATUSES)
    assert matches(q, _row(id="mine"), awaiting={"mine"})
    assert not matches(q, _row(id="theirs"), awaiting={"mine"})
    # No set supplied → nothing is awaiting, rather than everything.
    assert not matches(q, _row(id="mine"))


def test_terms_are_anded():
    q = parse("p1 sappr", statuses=STATUSES)
    assert matches(q, _row(priority="P1", status="Approved"))
    assert not matches(q, _row(priority="P1", status="Backlog"))
    assert not matches(q, _row(priority="P2", status="Approved"))


def test_a_broken_query_filters_nothing():
    """The error is the feedback; emptying the screen on a typo is not."""
    q = parse("sd", statuses=STATUSES)
    assert q.error
    assert matches(q, _row(id="anything"))


# ---------------------------------------------------------------------------
# tree semantics


def _node(id_, children=(), **kw):
    return SimpleNamespace(
        id=id_,
        title=kw.get("title", ""),
        status=kw.get("status", ""),
        priority=kw.get("priority"),
        children=list(children),
    )


def test_a_branch_survives_when_a_descendant_matches():
    """Otherwise `:p1` on an outcome-first tree empties the screen even though
    the P1 changes are one level down."""
    q = parse("p1", statuses=STATUSES)
    tree = _node("JTBD-1", [_node("SOL-1", [_node("a-change", priority="P1")])])
    assert matches_tree(q, tree)


def test_a_branch_with_no_matching_descendant_does_not_survive():
    q = parse("p1", statuses=STATUSES)
    tree = _node("JTBD-2", [_node("SOL-9", [_node("b-change", priority="P3")])])
    assert not matches_tree(q, tree)


def test_a_leaf_matches_on_its_own():
    q = parse("p0", statuses=STATUSES)
    assert matches_tree(q, _node("x", priority="P0"))


def test_an_empty_query_keeps_everything():
    q = parse("", statuses=STATUSES)
    assert matches_tree(q, _node("anything"))


def test_known_statuses_come_from_the_palette_not_a_second_list():
    """A hand-kept vocabulary would drift from what the rows actually show."""
    from otaman_cli.console.filter_grammar import known_statuses
    from otaman_cli.console.palette import STATUS_STYLE

    assert set(known_statuses()) == {s.lower() for s in STATUS_STYLE}


@pytest.mark.parametrize("text", ["P1", "SAPPR", "E+", "A"])
def test_the_grammar_is_case_insensitive(text):
    assert parse(text, statuses=STATUSES).error is None
