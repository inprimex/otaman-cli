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
