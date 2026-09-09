"""console-lifecycle-actions 1.3 — per-change detail view.

change_detail assembles stage, triage, tasks tick-state, artifacts, gate results
(with block reasons), delivery badge, and the viewer's available actions. The
Textual ChangeDetailScreen renders it (skips without the console extra).
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest
import yaml

from otaman_cli.console import bus
from otaman_cli.console.lifecycle import change_detail

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


def _change(
    program,
    name,
    *,
    stage="dispatched",
    triage=None,
    note="",
    ticks=(),
    delivery=None,
    approved_by=None,
    artifacts=("proposal.md",),
):
    d = program.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    oy = {"stage": stage}
    if triage:
        oy["triage"] = triage
    if note:
        oy["triage_note"] = note
    if delivery:
        oy["delivery"] = delivery
    if approved_by:
        oy["approved_by"] = approved_by
    (d / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    if ticks:
        (d / "tasks.md").write_text(
            "\n".join(f"- [{'x' if t else ' '}] task {i} @otaman-cli" for i, t in enumerate(ticks))
            + "\n",
            encoding="utf-8",
        )
    for a in artifacts:
        (d / a).write_text(f"# {a}\ncontent\n", encoding="utf-8")
    return d


def test_detail_assembles_all_fields(program):
    _change(
        program,
        "feature",
        stage="dispatched",
        triage="active",
        note="moving",
        ticks=[True, False],
        delivery="hitl",
    )
    det = change_detail(program, "feature")
    assert det["name"] == "feature" and det["stage"] == "dispatched"
    assert det["triage"] == "active" and det["triage_note"] == "moving"
    assert det["tasks_done"] == 1 and len(det["tasks"]) == 2
    assert det["tasks"][0] == (True, "task 0 @otaman-cli")
    assert "proposal.md" in det["artifacts"] and ".openspec.yaml" in det["artifacts"]
    assert set(det["gates"]) == {"merge", "dispatch", "archive"}


def test_detail_actions_include_ratify_for_blocked(program):
    _change(program, "stuck", stage="dispatched", ticks=[True, True])  # done, unapproved
    det = change_detail(program, "stuck")
    assert det["state"] == "complete-unarchived"
    assert "human" in det["next_actor"]
    assert any("ratify" in a for a in det["actions"])
    # archive gate blocks on approval-missing → shows a block reason
    assert det["gates"]["archive"]["violations"]


def test_detail_actions_include_archive_when_approved(program):
    _change(
        program, "done", stage="dispatched", ticks=[True, True], approved_by="ratified: roman — ok"
    )
    det = change_detail(program, "done")
    assert any("archive" in a for a in det["actions"])
    assert not det["gates"]["archive"]["violations"]  # clean


def test_detail_delivery_field_surfaced(program):
    _change(program, "auto-one", delivery="auto", ticks=[False])
    assert change_detail(program, "auto-one")["delivery"] == "auto"


def test_detail_unknown_change_is_empty(program):
    assert change_detail(program, "ghost") == {}


@_textual
def test_detail_screen_renders(program):
    _change(
        program,
        "feature",
        triage="active",
        ticks=[True, False],
        artifacts=("proposal.md", "design.md"),
    )
    from textual.widgets import ListView

    from otaman_cli.console.app import ChangeDetailScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ChangeDetailScreen(program, "feature"))
            await pilot.pause()
            # the screen assembled the detail + a summary + rendered the artifacts
            assert app.screen._detail["name"] == "feature"
            assert "gates:" in app.screen._summary_text() and "stage:" in app.screen._summary_text()
            files = app.screen.query_one("#detail-files", ListView)
            assert len(files.children) == 4  # proposal, design, tasks.md, .openspec.yaml
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_lifecycle_row_enter_opens_detail(program):
    _change(program, "feature", triage="active", ticks=[True, False])
    from textual.widgets import DataTable

    from otaman_cli.console.app import ChangeDetailScreen, LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.screen.query_one("#lifecycle-table", DataTable).focus()
            await pilot.pause()
            await pilot.press("enter")  # DataTable Enter → RowSelected → detail
            await pilot.pause()
            assert isinstance(app.screen, ChangeDetailScreen)
            await app.action_quit()

    asyncio.run(go())
