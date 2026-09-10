"""SLE 2.1 — the shared, stage-aware lifecycle derivation (otaman_cli.lifecycle).

One derivation, two renderers (console view + `otaman spec status`). Covers the
fields SLE adds over IHC 1.3: stage read from `.openspec.yaml` (repo is truth),
the WARN-day1 / ERROR-day3 severity buckets for the stalled states, and numeric
age_days. Archive-awareness itself is covered by the console suite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from otaman_cli.lifecycle import (
    APPROVED_UNAUTHORED,
    COMPLETE_UNARCHIVED,
    IN_FLIGHT,
    SEV_ERROR,
    SEV_OK,
    SEV_WARN,
    derive_lifecycle,
)

_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _bus(tmp_path):
    d = tmp_path / "bus"
    d.mkdir()
    return d


def _approved(bus_dir, title, *, ts):
    stem = f"{ts.replace('-', '').replace(':', '')}-human-to-all-spec-change-approved"
    (bus_dir / f"{stem}.md").write_text(
        f"---\nfrom: human\nto: all\ntype: spec-change-approved\ntimestamp: {ts}\n"
        f"status: pending\n---\n\n## Subject: Approved: {title}\n\nApproved.\n",
        encoding="utf-8",
    )


def _changes(tmp_path):
    d = tmp_path / "changes"
    d.mkdir()
    return d


def _change(changes_dir, name, *, ticks, stage=None):
    folder = changes_dir / name
    folder.mkdir()
    body = "## Tasks\n\n" + "\n".join(
        f"- [{'x' if t else ' '}] task {i} @otaman-cli" for i, t in enumerate(ticks)
    )
    (folder / "tasks.md").write_text(body + "\n", encoding="utf-8")
    if stage:
        (folder / ".openspec.yaml").write_text(f"stage: {stage}\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# severity buckets (D3): WARN at day 1, ERROR at day 3


def test_approved_unauthored_severity_ok_under_a_day(tmp_path):
    bus = _bus(tmp_path)
    _approved(bus, "fresh", ts="2026-09-09T18:00:00Z")  # ~6h before _NOW
    (row,) = derive_lifecycle(changes_dir=None, bus_active_dir=bus, now=_NOW)
    assert row.state == APPROVED_UNAUTHORED and row.severity == SEV_OK and row.age_days == 0


def test_approved_unauthored_severity_warn_at_day_1(tmp_path):
    bus = _bus(tmp_path)
    _approved(bus, "aging", ts="2026-09-08T18:00:00Z")  # ~1.25d
    (row,) = derive_lifecycle(changes_dir=None, bus_active_dir=bus, now=_NOW)
    assert row.severity == SEV_WARN and row.age_days == 1


def test_approved_unauthored_severity_error_at_day_3(tmp_path):
    bus = _bus(tmp_path)
    _approved(bus, "stale", ts="2026-09-05T00:00:00Z")  # 5d
    (row,) = derive_lifecycle(changes_dir=None, bus_active_dir=bus, now=_NOW)
    assert row.severity == SEV_ERROR and row.age_days == 5


# ---------------------------------------------------------------------------
# approved-unauthored is archive- + disposition-aware (doctor lint defect fix):
# an approval that authored a change is not "unauthored" even when the change's
# folder slug differs from the broadcast title (matched via approved_by), and an
# approval closed in the disposition ledger (absorbed/withdrawn) is not flagged.


def _archived_change(changes_dir, dated_name, *, approved_by):
    folder = changes_dir / "archive" / dated_name
    folder.mkdir(parents=True)
    (folder / ".openspec.yaml").write_text(
        f"stage: spec-approved\napproved_by: {approved_by}\n", encoding="utf-8"
    )
    return folder


def test_approved_authored_via_approved_by_note_not_flagged(tmp_path):
    # Archived change whose folder slug does NOT resemble the broadcast title, but
    # whose approved_by cites the broadcast's compact timestamp → resolved.
    bus = _bus(tmp_path)
    _approved(bus, "CLI guidance when not in a project", ts="2026-09-09T10:00:00Z")
    changes = _changes(tmp_path)
    _archived_change(
        changes,
        "2026-09-09-cli-not-in-project-guidance",
        approved_by="roman (broadcast 20260909T100000)",
    )
    assert derive_lifecycle(changes_dir=changes, bus_active_dir=bus, now=_NOW) == []


def test_approval_absorbed_in_disposition_ledger_not_flagged(tmp_path):
    # Folderless approval recorded as absorbed → closed, not unauthored (by token).
    bus = _bus(tmp_path)
    _approved(bus, "otaman-method-process-levels", ts="2026-09-07T11:35:46Z")
    changes = _changes(tmp_path)
    (changes.parent / "dispositions.yaml").write_text(
        "- approval: 20260907T113546-human-to-all-spec-change-approved\n"
        "  title: otaman-method-process-levels\n"
        "  disposition: absorbed\n",
        encoding="utf-8",
    )
    assert derive_lifecycle(changes_dir=changes, bus_active_dir=bus, now=_NOW) == []


def test_approval_withdrawn_by_title_not_flagged(tmp_path):
    # Withdrawn approval with a synthetic ledger id → matched by title slug.
    bus = _bus(tmp_path)
    _approved(bus, "'--help' footgun artifact", ts="2026-08-24T09:59:11Z")
    changes = _changes(tmp_path)
    (changes.parent / "dispositions.yaml").write_text(
        "- approval: 20260824T-help-scr\n"
        "  title: \"'--help' footgun artifact\"\n"
        "  disposition: withdrawn\n",
        encoding="utf-8",
    )
    assert derive_lifecycle(changes_dir=changes, bus_active_dir=bus, now=_NOW) == []


def test_genuinely_unauthored_approval_still_flags(tmp_path):
    # Negative case: no folder, no approved_by, no disposition → still flagged.
    bus = _bus(tmp_path)
    _approved(bus, "orphan approval nobody authored", ts="2026-09-05T00:00:00Z")
    changes = _changes(tmp_path)
    _archived_change(
        changes, "2026-09-01-unrelated", approved_by="roman (broadcast 20260101T000000)"
    )
    (changes.parent / "dispositions.yaml").write_text(
        "- approval: 20260101T000000-x\n  title: something else\n  disposition: withdrawn\n",
        encoding="utf-8",
    )
    (row,) = derive_lifecycle(changes_dir=changes, bus_active_dir=bus, now=_NOW)
    assert row.state == APPROVED_UNAUTHORED


# ---------------------------------------------------------------------------
# stage from .openspec.yaml (repo is truth, D1)


def test_in_flight_reads_stage_from_openspec_yaml(tmp_path):
    changes = _changes(tmp_path)
    _change(changes, "wip", ticks=[False], stage="authored")
    (row,) = derive_lifecycle(changes_dir=changes, bus_active_dir=None, now=_NOW)
    assert row.state == IN_FLIGHT and row.stage == "authored"
    assert row.severity == SEV_OK  # active work is never an alarm
    assert "cli-agent" in row.next_actor


def test_stage_none_when_no_openspec_yaml(tmp_path):
    changes = _changes(tmp_path)
    _change(changes, "legacy", ticks=[False])  # not yet backfilled
    (row,) = derive_lifecycle(changes_dir=changes, bus_active_dir=None, now=_NOW)
    assert row.stage is None


# ---------------------------------------------------------------------------
# complete-unarchived escalates with age; owner/next is spec-agent


def test_complete_unarchived_is_a_stalled_bucket(tmp_path):
    changes = _changes(tmp_path)
    folder = _change(changes, "done", ticks=[True, True], stage="implemented")
    # backdate the folder mtime to 4 days → ERROR
    import os

    four_days_ago = _NOW.timestamp() - 4 * 86400
    os.utime(folder, (four_days_ago, four_days_ago))
    (row,) = derive_lifecycle(changes_dir=changes, bus_active_dir=None, now=_NOW)
    assert row.state == COMPLETE_UNARCHIVED and row.severity == SEV_ERROR
    # unapproved delta change → ratify-blocked → next actor is the human (gate 3.1)
    assert "human" in row.next_actor and "ratify" in row.next_actor


def test_empty_inputs_yield_nothing(tmp_path):
    assert derive_lifecycle(changes_dir=None, bus_active_dir=None, now=_NOW) == []
