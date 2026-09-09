"""IHC 1.5 / D10 — lifecycle TABLE derivation + one-key nudge.

One row per active change (all columns from the spec), triage class read from
each change's .openspec.yaml, tasks x/y, last-real-touch (chore-excluded, git),
grouped/sorted by triage. Plus the nudge: a bus ping to the row's next actor,
with last-nudged derived back from the bus. The Textual table skips cleanly
without the console extra.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest
import yaml

from otaman_cli.console import bus
from otaman_cli.lifecycle import (
    TRIAGE_ORDER,
    derive_change_table,
    nudge_target,
    send_nudge,
    triage_rank,
)

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "specs" / "openspec" / "changes").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nspecs:\n  path: specs\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _changes_dir(program):
    return program.root / "specs" / "openspec" / "changes"


def _bus_dir(program):
    return program.root / ".agents" / "bus" / "active"


def _change(
    program, name, *, stage=None, triage=None, note="", ticks=(), owner="cli", approved_by=None
):
    d = _changes_dir(program) / name
    d.mkdir(parents=True)
    oy = {}
    if stage:
        oy["stage"] = stage
    if triage:
        oy["triage"] = triage
    if note:
        oy["triage_note"] = note
    if approved_by:
        oy["approved_by"] = approved_by
    if oy:
        (d / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    if ticks:
        body = "\n".join(
            f"- [{'x' if t else ' '}] t{i} @otaman-{owner}" for i, t in enumerate(ticks)
        )
        (d / "tasks.md").write_text(body + "\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# derive_change_table


def test_row_columns_populated(program):
    _change(
        program,
        "feature-x",
        stage="dispatched",
        triage="active",
        note="moving",
        ticks=[True, False],
    )
    (row,) = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert row.name == "feature-x" and row.stage == "dispatched"
    assert row.triage == "active" and row.triage_note == "moving"
    assert row.tasks_done == 1 and row.tasks_total == 2
    assert "cli-agent" in row.next_actor and row.state == "in-flight"


def test_sorted_by_triage_then_name(program):
    _change(program, "z-active", triage="active")
    _change(program, "a-dormant", triage="dormant")
    _change(program, "m-untriaged")  # no triage → sorts last
    _change(program, "b-active", triage="active")
    rows = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    names = [r.name for r in rows]
    # both active first (alpha), then dormant, then untriaged
    assert names == ["b-active", "z-active", "a-dormant", "m-untriaged"]


def test_triage_rank_order():
    assert triage_rank("active") < triage_rank("dormant")
    assert triage_rank(None) == len(TRIAGE_ORDER)  # unknown sorts last


def test_tasks_counts_and_states(program):
    _change(program, "in-flight-one", triage="active", ticks=[True, False, False])
    _change(program, "complete-one", triage="archive-candidate", ticks=[True, True])
    _change(program, "no-tasks", triage="paused-decision")
    rows = {
        r.name: r
        for r in derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    }
    assert rows["in-flight-one"].tasks_done == 1 and rows["in-flight-one"].tasks_total == 3
    assert rows["complete-one"].state == "complete-unarchived"
    assert rows["no-tasks"].tasks_total == 0 and rows["no-tasks"].state == "—"


# ---------------------------------------------------------------------------
# 1.1 next-actor fix: ratify-blocked -> human (Roman's misrouted-nudge incident)


def test_ratify_blocked_change_next_actor_is_human(program):
    # complete-unarchived delta change lacking approval → archive gate blocks on
    # approved_by; the next actor is the human with `otaman ratify`, not spec-agent.
    _change(
        program,
        "auto-clear-blocked-entries",
        stage="dispatched",
        triage="dormant",
        ticks=[True, True],
    )
    (row,) = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert row.state == "complete-unarchived"
    assert "human" in row.next_actor and "ratify" in row.next_actor
    assert "auto-clear-blocked-entries" in row.next_actor


def test_approved_complete_change_next_actor_is_spec_agent(program):
    _change(
        program,
        "done-approved",
        stage="dispatched",
        ticks=[True, True],
        approved_by="ratified: roman — shipped",
    )
    (row,) = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert "spec-agent" in row.next_actor  # approved → archive is spec-agent's


def test_ratify_blocked_nudge_routes_to_human(program):
    _change(program, "stuck", stage="dispatched", triage="dormant", ticks=[True, True])
    (row,) = derive_change_table(
        changes_dir=_changes_dir(program), bus_active_dir=_bus_dir(program)
    )
    assert nudge_target(row.next_actor, row.triage) == "human"  # not spec-agent (the bug)
    ok, msg = send_nudge(program, row)
    assert ok and "human" in msg
    assert list(_bus_dir(program).glob("*-nudge-stuck.md"))[0].read_text("utf-8").count("to: human")


# ---------------------------------------------------------------------------
# nudge


def test_nudge_target_extracts_agent():
    assert nudge_target("cli-agent, core-agent", "active") == "cli-agent"
    assert nudge_target("spec-agent (archive the change)", "archive-candidate") == "spec-agent"


def test_nudge_target_fallbacks():
    assert nudge_target("—", "paused-decision") == "human"  # human decision pending
    assert nudge_target("—", "dormant") == "spec-agent"  # default


def test_send_nudge_writes_message_and_last_nudged_reflects_it(program):
    _change(program, "stalled-x", stage="dispatched", triage="dormant", ticks=[False], owner="core")
    (row,) = derive_change_table(
        changes_dir=_changes_dir(program), bus_active_dir=_bus_dir(program)
    )
    assert row.last_nudged == ""  # never nudged
    ok, msg = send_nudge(program, row, note="please pick this up")
    assert ok is True and "core-agent" in msg
    # the nudge message exists, addressed to the next actor, naming the change
    nudges = list(_bus_dir(program).glob("*-nudge-stalled-x.md"))
    assert nudges and "to: core-agent" in nudges[0].read_text("utf-8")
    assert "please pick this up" in nudges[0].read_text("utf-8")
    # re-derive: last_nudged now reflects the bus record
    (row2,) = derive_change_table(
        changes_dir=_changes_dir(program), bus_active_dir=_bus_dir(program)
    )
    assert row2.last_nudged  # a date, no longer empty


# ---------------------------------------------------------------------------
# Textual table


@_textual
def test_table_renders_rows_grouped_by_triage(program):
    _change(program, "active-one", triage="active", ticks=[False])
    _change(program, "dormant-one", triage="dormant", ticks=[False])
    from textual.widgets import DataTable

    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()  # table loads off a worker thread
            await pilot.pause()
            table = app.screen.query_one("#lifecycle-table", DataTable)
            assert table.row_count == 2
            # active sorts before dormant → first row is the active change
            first = table.get_row_at(0)
            assert "active-one" in first
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_table_nudge_action_sends_message(program):
    _change(program, "nudge-me", stage="dispatched", triage="dormant", ticks=[False], owner="core")
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()  # rows must be loaded before nudge
            await pilot.pause()
            await app.screen.run_action("nudge")
            await pilot.pause()
            await pilot.press("enter")  # submit empty note in the reason modal
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    nudges = list(_bus_dir(program).glob("*-nudge-nudge-me.md"))
    assert nudges and "to: core-agent" in nudges[0].read_text("utf-8")
