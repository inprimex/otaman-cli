"""The promote guard names the condition that actually fired.

cofounder-agent 20260920T111813, trying to promote JTBD-62 under a strategy
ruling:

    [!] Validation failed; refusing to write outcomes.yaml:
    transition[0] action=promote requires from+to

`from` and `to` were both present and correct. The trip was the THIRD condition
collapsed into that message — `current is None`, which happens when the log has
no `create` entry to establish a prior status. The 2026-09-10 CANON migration
rewrote legacy transitions as `update-field` audit entries and synthesised no
`create`, so this is true of 74 of 74 pre-existing outcomes: promote, demote
and retire have never once succeeded against real data.

The error sent them looking at the two fields that were fine. Fail-closed
worked — nothing was written — but a guard that misnames its own cause costs an
investigation every time it fires.

This file fixes the DIAGNOSIS only. Whether a missing `create` should be
tolerated (derive the start from the entry's `status`), backfilled, or keep
failing is a canon question in outcome-and-solution-registries Appendix A.4,
raised with spec-agent rather than decided here.
"""

from __future__ import annotations

import pytest

from otaman_cli.registries.outcomes import OutcomeRegistry

BASE = {
    "id": "JTBD-62-hosted-teams-tier",
    "status": "Backlog",
    "created": "2026-09-01",
    "updated": "2026-09-20",
    "statement": {
        "as-a": "an operator",
        "i-want-to": "a hosted tier",
        "incremental-outcome": "revenue",
        "so-i-can": "grow",
    },
}

MIGRATED = {
    "at": "2026-09-10T00:00:00Z",
    "by": "migration",
    "action": "update-field",
    "field": "status",
    "old": "Drafting",
    "new": "Backlog",
}
CREATE = {"at": "2026-09-01T00:00:00Z", "by": "x", "action": "create", "to": "Backlog"}


def _validate(transitions, *, status: str = "Backlog"):
    """`status` must match the final state the transitions arrive at — the
    registry cross-checks them, so a happy-path fixture has to agree."""
    return OutcomeRegistry.model_validate(
        {"outcomes": [{**BASE, "status": status, "transitions": transitions}]}
    )


def _error(transitions) -> str:
    with pytest.raises(Exception) as exc:
        _validate(transitions)
    return str(exc.value)


def test_a_migrated_outcome_says_it_has_no_create_entry():
    """THE DEFECT: this reported "requires from+to" while both were present."""
    promote = {
        "action": "promote",
        "from": "Backlog",
        "to": "Approved",
        "at": "2026-09-20T00:00:00Z",
        "by": "roman",
    }
    message = _error([MIGRATED, promote])
    assert "no established prior status" in message
    assert "`create`" in message


def test_the_misleading_message_is_gone_for_that_case():
    promote = {
        "action": "promote",
        "from": "Backlog",
        "to": "Approved",
        "at": "2026-09-20T00:00:00Z",
        "by": "roman",
    }
    assert "requires from+to" not in _error([MIGRATED, promote])


def test_a_genuinely_missing_field_still_says_from_to():
    """The original message is still right when it IS the cause."""
    promote = {"action": "promote", "from": "Backlog", "at": "2026-09-20T00:00:00Z", "by": "roman"}
    message = _error([CREATE, promote])
    assert "requires from+to" in message
    assert "no established prior status" not in message


def test_a_promote_after_create_still_validates():
    """The fix is diagnostic only — behaviour is unchanged."""
    promote = {
        "action": "promote",
        "from": "Backlog",
        "to": "Approved",
        "at": "2026-09-20T00:00:00Z",
        "by": "roman",
    }
    assert _validate([CREATE, promote], status="Approved") is not None


def test_an_illegal_promote_target_still_reports_as_illegal():
    promote = {
        "action": "promote",
        "from": "Backlog",
        "to": "Retired",
        "at": "2026-09-20T00:00:00Z",
        "by": "roman",
    }
    assert "illegal promote" in _error([CREATE, promote])


def test_the_migrated_shape_is_the_registry_majority():
    """Pins WHY this matters: an outcome whose log is update-field entries with
    no `create` is the normal case on real data (74 of 74), not an edge case."""
    assert _validate([MIGRATED]) is not None  # valid until a forward verb is used
