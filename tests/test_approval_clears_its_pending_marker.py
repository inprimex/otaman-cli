"""Approving (or rejecting) an SCR resolves the `spec-approval-pending` it enqueued.

plugin-agent measured the defect on the live bus (20261004T121731): `otaman propose`
writes a `spec-approval-pending` item to the human alongside every SCR, and nothing ever
cleared it.

    spec-approval-pending still status: pending        24
    ...of those with an approval already on the bus    21
    oldest                                             2026-09-11

Unlike the queue's bulk producers this is a state machine that never completes — every
future SCR added one permanently — and the failure mode is worse than noise: a surface
that says something awaits the human when it does not trains people to ignore
approval-pending.

Its companion already worked: `_terminate_blocked_entries` cleared plugin's blocked entry
the moment the approval landed. One half of the pair resolved itself and the other did
not. This is the other half, in the same place.

On the human floor: plugin deliberately did NOT clear the 21 they found, because acking
as the human is never pre-authorizable. This clearing lives inside `otaman approve` —
PRIVILEGED, gated on `confirm_human_decision`'s TTY prompt — so the human is present and
it is the approval retracting its own notice, not an agent sweeping an inbox.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.approve import _resolve_approval_pending

SCR = "20261004T120000-cli-agent-to-human-spec-change-request"


@pytest.fixture
def bus(tmp_path):
    active = tmp_path / "active"
    acks = active / "acks"
    acks.mkdir(parents=True)
    return active, acks


def _marker(active, stem: str, scr: str = SCR, kind: str = "spec-approval-pending"):
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: cli-agent\nto: human\npriority: normal\n"
        f"type: {kind}\ntimestamp: 2026-10-04T12:00:00+00:00\nstatus: pending\n---\n\n"
        f"## Subject: Approval pending: something\n\n"
        f"Awaiting human approval/ratification of SCR `{scr}` (from cli-agent).\n",
        encoding="utf-8",
    )
    return stem


def test_the_matching_marker_is_resolved(bus):
    active, acks = bus
    stem = _marker(active, "20261004T120001-cli-agent-to-human-spec-approval-pending")

    resolved = _resolve_approval_pending(active, acks, SCR, "approved")

    assert resolved == [stem]
    assert (acks / f"{stem}.human.ack").read_text() == "approved\n"


def test_a_marker_for_a_DIFFERENT_scr_is_untouched(bus):
    """The link is the SCR stem the producer recorded, not "any pending marker" — a
    sweep is exactly what must not happen here."""
    active, acks = bus
    mine = _marker(active, "20261004T120001-cli-agent-to-human-spec-approval-pending")
    other = _marker(
        active,
        "20261004T120002-plugin-agent-to-human-spec-approval-pending",
        scr="20261001T090000-plugin-agent-to-human-spec-change-request",
    )

    resolved = _resolve_approval_pending(active, acks, SCR, "approved")

    assert resolved == [mine]
    assert not (acks / f"{other}.human.ack").exists(), "resolved someone else's marker"


def test_a_rejection_also_supersedes_the_notice(bus):
    """ "Approval pending" on a REJECTED scr is not merely stale, it is wrong."""
    active, acks = bus
    stem = _marker(active, "20261004T120001-cli-agent-to-human-spec-approval-pending")

    _resolve_approval_pending(active, acks, SCR, "rejected")

    assert (acks / f"{stem}.human.ack").read_text() == "rejected\n"


def test_an_already_resolved_marker_is_not_rewritten(bus):
    """Idempotent and non-destructive: a human may have acked it themselves, and
    overwriting their verdict with ours would be the sweep this avoids."""
    active, acks = bus
    stem = _marker(active, "20261004T120001-cli-agent-to-human-spec-approval-pending")
    (acks / f"{stem}.human.ack").write_text("read\n", encoding="utf-8")

    resolved = _resolve_approval_pending(active, acks, SCR, "approved")

    assert resolved == []
    assert (acks / f"{stem}.human.ack").read_text() == "read\n", "overwrote a human's ack"


def test_a_non_pending_type_is_never_touched(bus):
    """Filename-shaped matches are not enough — the type is checked."""
    active, acks = bus
    stem = _marker(
        active,
        "20261004T120003-cli-agent-to-human-spec-approval-pending-digest",
        kind="info",
    )

    assert _resolve_approval_pending(active, acks, SCR, "approved") == []
    assert not (acks / f"{stem}.human.ack").exists()


def test_no_marker_means_no_claim(bus):
    """Nothing to resolve returns nothing, so the caller reports nothing — a verb
    announcing work it did not do is the shape this platform forbids."""
    active, acks = bus

    assert _resolve_approval_pending(active, acks, SCR, "approved") == []


def test_a_missing_active_dir_is_not_a_failed_approval(bus):
    active, acks = bus

    assert _resolve_approval_pending(active.parent / "nope", acks, SCR, "approved") == []


# ---------------------------------------------------------------------------
# wiring: both verdict paths, and the human gate


def test_both_the_approve_and_reject_paths_resolve_it():
    import inspect

    from otaman_cli.commands import approve

    src = inspect.getsource(approve)
    approved = src.count(
        '_resolve_approval_pending(active_dir, acks_dir, target["stem"], "approved")'
    )
    rejected = src.count(
        '_resolve_approval_pending(active_dir, acks_dir, target["stem"], "rejected")'
    )

    assert approved == 1, "the approval path does not resolve its own notice"
    assert rejected == 1, "the rejection path does not"


def test_the_clearing_sits_behind_the_human_tty_gate():
    """The reason this is not an agent acking as the human. If the gate ever leaves
    this module, the clearing must be re-argued rather than inherited."""
    import inspect

    from otaman_cli.commands import approve

    src = inspect.getsource(approve)

    assert "confirm_human_decision" in src
