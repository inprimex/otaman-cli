"""interactive-human-console 1.3 — lifecycle-states view (D7).

Beyond the pending HITL queue, derive the catchable states where work stalls —
approved-but-unauthored, in-flight, complete-but-unarchived — runner-free from
bus + specs repo. The 2026-09-03 staleness incident (four SCRs approved, none
authored, invisible for days) is the four-approval fixture below. The Textual
LifecycleScreen pilot skips cleanly without the `console` extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone

import pytest

from otaman_cli.console import bus
from otaman_cli.console.lifecycle import (
    APPROVED_UNAUTHORED,
    COMPLETE_UNARCHIVED,
    IN_FLIGHT,
    list_lifecycle_states,
)

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")

_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _program(tmp_path, *, specs="specs"):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    lines = ["project: demo"]
    if specs is not None:
        lines.append("specs:")
        lines.append(f"  path: {specs}")
    root.joinpath("platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bus.Program(name="demo", root=root)


def _approved(program, title, *, ts="2026-08-25T00:00:00Z", stem_id="20260825T000000"):
    stem = f"{stem_id}-human-to-all-spec-change-approved"
    (program.root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nfrom: human\nto: all\ntype: spec-change-approved\ntimestamp: {ts}\n"
        f"status: pending\n---\n\n## Subject: Approved: {title}\n\nApproved.\n",
        encoding="utf-8",
    )


def _change(program, name, *, ticks, archived=False):
    """A change folder with a tasks.md; `ticks` is a list of bools (True == done)."""
    changes = program.root / "specs" / "openspec" / "changes"
    folder = (changes / "archive" / f"2026-08-01-{name}") if archived else (changes / name)
    folder.mkdir(parents=True, exist_ok=True)
    body = "## 1. Tasks\n\n" + "\n".join(
        f"- [{'x' if done else ' '}] task {i} @otaman-cli" for i, done in enumerate(ticks)
    )
    (folder / "tasks.md").write_text(body + "\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# approved-but-unauthored


def test_approved_with_no_change_folder_is_unauthored(tmp_path):
    program = _program(tmp_path)
    _approved(program, "Add widget engine")
    rows = list_lifecycle_states(program, now=_NOW)
    assert len(rows) == 1
    (row,) = rows
    assert row.state == APPROVED_UNAUTHORED
    assert row.change == "Add widget engine"
    assert row.age == "10d"  # approved 2026-08-25, now 2026-09-04
    assert "spec-agent" in row.next_action


def test_corrected_incident_only_genuinely_unauthored_flagged(tmp_path):
    # D8 (spec.md "pending-only blindness is gone, without archive false-positives"):
    # the corrected 2026-09-03 fixture — one SCR approved AND genuinely unauthored,
    # three approved SCRs whose changes were delivered and ARCHIVED in August. The
    # derivation must flag ONLY the one; the three archived ones are not stale.
    program = _program(tmp_path)
    _approved(program, "agent-credential-access", stem_id="20260825T000000")
    for i, name in enumerate(
        ["openspec-cli-adoption", "hitl-confirmation-adapters", "repo-registration-materialization"]
    ):
        _approved(program, name, stem_id=f"2026082{i}T000001", ts=f"2026-08-2{i}T00:00:00Z")
        _change(program, name, ticks=[True], archived=True)  # authored + delivered + archived
    rows = list_lifecycle_states(program, now=_NOW)
    unauthored = [r for r in rows if r.state == APPROVED_UNAUTHORED]
    assert [r.change for r in unauthored] == ["agent-credential-access"]  # exactly the real one


def test_approved_with_matching_change_folder_is_not_unauthored(tmp_path):
    program = _program(tmp_path)
    _approved(program, "add-widget")
    _change(program, "add-widget", ticks=[True])  # authored + complete
    rows = list_lifecycle_states(program, now=_NOW)
    assert not any(r.state == APPROVED_UNAUTHORED for r in rows)


def test_approved_matching_archived_folder_is_not_unauthored(tmp_path):
    program = _program(tmp_path)
    _approved(program, "add-widget")
    _change(program, "add-widget", ticks=[True], archived=True)
    rows = list_lifecycle_states(program, now=_NOW)
    assert not any(r.state == APPROVED_UNAUTHORED for r in rows)


# ---------------------------------------------------------------------------
# in-flight / complete-unarchived


def test_in_flight_change_names_unticked_owner(tmp_path):
    program = _program(tmp_path)
    _change(program, "wip-change", ticks=[True, False])
    rows = list_lifecycle_states(program, now=_NOW)
    (row,) = [r for r in rows if r.change == "wip-change"]
    assert row.state == IN_FLIGHT
    assert "cli-agent" in row.next_action


def test_complete_unarchived_change(tmp_path):
    program = _program(tmp_path)
    _change(program, "done-change", ticks=[True, True])
    rows = list_lifecycle_states(program, now=_NOW)
    (row,) = [r for r in rows if r.change == "done-change"]
    assert row.state == COMPLETE_UNARCHIVED
    assert "spec-agent" in row.next_action


def test_archived_change_is_not_listed(tmp_path):
    program = _program(tmp_path)
    _change(program, "old-change", ticks=[True, True], archived=True)
    rows = list_lifecycle_states(program, now=_NOW)
    assert not any(r.change == "old-change" for r in rows)


# ---------------------------------------------------------------------------
# graceful degradation


def test_no_specs_path_still_derives_from_bus(tmp_path):
    program = _program(tmp_path, specs=None)
    _approved(program, "Bus only change")
    rows = list_lifecycle_states(program, now=_NOW)
    assert [r.state for r in rows] == [APPROVED_UNAUTHORED]


def test_empty_program_yields_nothing(tmp_path):
    program = _program(tmp_path)
    assert list_lifecycle_states(program, now=_NOW) == []


# ---------------------------------------------------------------------------
# LifecycleScreen pilot


@_textual
def test_lifecycle_screen_lists_rows(tmp_path):
    program = _program(tmp_path)
    _approved(program, "Stalled change")
    _change(program, "wip-change", ticks=[False])
    from textual.widgets import ListView

    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            assert isinstance(app.screen, LifecycleScreen)
            lv = app.screen.query_one("#lifecycle-list", ListView)
            assert len(lv.children) == 2  # one approved-unauthored + one in-flight
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_pending_list_opens_lifecycle_via_binding(tmp_path):
    program = _program(tmp_path)
    _change(program, "wip-change", ticks=[False])
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole, PendingListScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            await app.screen.run_action("lifecycle")
            await pilot.pause()
            assert isinstance(app.screen, LifecycleScreen)
            await app.action_quit()

    asyncio.run(go())
