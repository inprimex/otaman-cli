"""Forward verbs start from the declared status (outcome-transition-start-rule 1.1).

`promote`, `demote` and `retire` had never once succeeded against real data.
The status-machine walk only ever established a starting point from a `create`
transition, and the 2026-09-10 CANON migration rewrote legacy transitions as
`update-field` audit entries and synthesised none — so 74 of 85 outcomes had no
starting point and every forward verb refused.

Ruled option 1: the declared `status` is the source of truth and the transition
log is an AUDIT TRAIL. The walk derives its start from the recorded `from`
rather than refusing, and NO synthetic `create` is ever written for an event
that did not happen.

Measured after the fix: all 74 previously-dead outcomes validate a promote.
"""

from __future__ import annotations

import pytest

from otaman_cli.registries.outcomes import OutcomeRegistry

BASE = {
    "id": "JTBD-1-signin",
    "created": "2026-09-01",
    "updated": "2026-09-20",
    "statement": {
        "as-a": "an operator",
        "i-want-to": "sign in",
        "incremental-outcome": "fewer lockouts",
        "so-i-can": "work",
    },
}

#: what the 2026-09-10 migration actually wrote — an audit entry, not a create
MIGRATED = {
    "at": "2026-09-10T00:00:00Z",
    "by": "migration",
    "action": "update-field",
    "field": "status",
    "old": "Drafting",
    "new": "Backlog",
}
CREATE = {"at": "2026-09-01T00:00:00Z", "by": "x", "action": "create", "to": "Backlog"}


def _promote(frm, to):
    return {"at": "2026-09-20T00:00:00Z", "by": "roman", "action": "promote", "from": frm, "to": to}


def _demote(frm, to):
    return {"at": "2026-09-20T00:00:00Z", "by": "roman", "action": "demote", "from": frm, "to": to}


def _validate(status, transitions):
    return OutcomeRegistry.model_validate(
        {"outcomes": [{**BASE, "status": status, "transitions": transitions}]}
    )


def _refused(status, transitions) -> str:
    with pytest.raises(Exception) as exc:
        _validate(status, transitions)
    return str(exc.value)


# ---------------------------------------------------------------------------
# the rule


def test_a_migrated_outcome_promotes():
    """The spec's first scenario, and the shape of 74 of 85 real outcomes."""
    reg = _validate("Approved", [MIGRATED, _promote("Backlog", "Approved")])
    assert reg.outcomes[0].status.value == "Approved"


def test_a_zero_transition_outcome_is_not_dead():
    """41 outcomes had an EMPTY log. Promote must work from the declared status
    alone, and the promote entry becomes the log's first."""
    reg = _validate("Backlog", [_promote("Drafting", "Backlog")])
    assert [t.action for t in reg.outcomes[0].transitions] == ["promote"]


def test_an_outcome_with_no_transitions_at_all_validates():
    assert _validate("Drafting", []) is not None


def test_demote_derives_its_start_too():
    """Same rule for demote, per the task."""
    assert _validate("Backlog", [MIGRATED, _demote("Approved", "Backlog")]) is not None


def test_the_audit_entry_records_the_derived_from():
    reg = _validate("Approved", [MIGRATED, _promote("Backlog", "Approved")])
    promote = reg.outcomes[0].transitions[-1]
    assert promote.from_ == "Backlog"
    assert promote.to == "Approved"


# ---------------------------------------------------------------------------
# what must NOT happen


def test_no_synthetic_create_is_written():
    """The audit trail never lies: validation must not manufacture an entry for
    an event that did not occur."""
    reg = _validate("Approved", [MIGRATED, _promote("Backlog", "Approved")])
    assert [t.action for t in reg.outcomes[0].transitions] == ["update-field", "promote"]


def test_an_illegal_move_is_still_refused():
    """Deriving a start point must not launder an invalid transition."""
    assert "illegal promote" in _refused("Retired", [MIGRATED, _promote("Backlog", "Retired")])


def test_a_chain_that_contradicts_the_status_is_still_refused():
    """The end-of-walk cross-check still guards: adopting a start cannot hide a
    log whose final state disagrees with the declared status."""
    assert "final state" in _refused("Drafting", [MIGRATED, _promote("Backlog", "Approved")])


def test_a_create_still_establishes_the_start_when_present():
    """Outcomes created through the CLI keep their `create` semantics."""
    assert _validate("Approved", [CREATE, _promote("Backlog", "Approved")]) is not None


def test_a_promote_missing_from_or_to_is_still_refused():
    partial = {"at": "2026-09-20T00:00:00Z", "by": "roman", "action": "promote", "from": "Backlog"}
    assert "requires from+to" in _refused("Approved", [MIGRATED, partial])


def test_the_comment_records_why_the_walk_adopts_a_start():
    """So the guard is not 'restored' by someone reading it as a missing check."""
    import inspect

    from otaman_cli.registries import outcomes

    src = inspect.getsource(outcomes.Outcome._transitions_match_status_machine)
    assert "audit" in src.lower()
    assert "synthetic" in src.lower()
