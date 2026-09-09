"""console-lifecycle-actions 1.4 — approve-as-auto + [auto-delivery] badge.

delivery mode (auto/hitl) lives in .openspec.yaml (D3); the badge renders wherever
a change renders (lifecycle table, spec status, detail); approve-as-auto records
the durable intent in the SCR approval so autonomy is always visible (D4).
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest
import yaml

from otaman_cli.console import bus, decision
from otaman_cli.console.identity import resolve_identity
from otaman_cli.lifecycle import derive_change_table, derive_lifecycle

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "specs" / "openspec" / "changes").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nspecs:\n  path: specs\n"
        "human-roster:\n  - name: roman\n    email: roman@x.io\n    roles: [cto, approver]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    return bus.Program(name="demo", root=root)


def _change(program, name, *, delivery=None, ticks=(False,)):
    d = program.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    oy = {"stage": "dispatched"}
    if delivery:
        oy["delivery"] = delivery
    (d / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    (d / "tasks.md").write_text(
        "\n".join(f"- [{'x' if t else ' '}] t{i} @otaman-cli" for i, t in enumerate(ticks)) + "\n",
        encoding="utf-8",
    )
    return d


def _changes_dir(program):
    return program.root / "specs" / "openspec" / "changes"


def _stage_scr(program, stem="20260101T000000-core-agent-to-human-spec-change-request"):
    (program.root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\nfrom: core-agent\nto: human\npriority: high\ntype: spec-change-request\n"
        "timestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: add widget\n\nbody\n",
        encoding="utf-8",
    )
    return bus.list_pending_proposals(program)[0]


# ---------------------------------------------------------------------------
# delivery surfaced in the derivations


def test_derive_change_table_carries_delivery(program):
    _change(program, "auto-one", delivery="auto")
    (row,) = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert row.delivery == "auto"


def test_derive_lifecycle_carries_delivery(program):
    _change(program, "auto-one", delivery="auto", ticks=(False,))  # in-flight
    (row,) = derive_lifecycle(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert row.delivery == "auto"


def test_no_delivery_is_none(program):
    _change(program, "plain")
    (row,) = derive_change_table(changes_dir=_changes_dir(program), bus_active_dir=None)
    assert row.delivery is None


# ---------------------------------------------------------------------------
# approve-as-auto records the durable intent in the SCR approval


def test_approve_auto_marks_delivery_in_broadcast(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage_scr(program)
    ident = resolve_identity(program.root)
    ok, msg = decision.approve(program, proposal, ident, reason="ship it", delivery="auto")
    assert ok is True
    active = program.root / ".agents" / "bus" / "active"
    bc = [f for f in active.glob("*.md") if "spec-change-approved" in f.name]
    assert bc and "delivery: auto" in bc[0].read_text("utf-8")


def test_plain_approve_has_no_delivery_marker(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage_scr(program)
    ident = resolve_identity(program.root)
    decision.approve(program, proposal, ident, reason="ship it")  # no delivery
    active = program.root / ".agents" / "bus" / "active"
    bc = [f for f in active.glob("*.md") if "spec-change-approved" in f.name]
    assert bc and "delivery: auto" not in bc[0].read_text("utf-8")


# ---------------------------------------------------------------------------
# badge in spec status


def test_spec_status_shows_auto_delivery_badge(program, monkeypatch, capsys):
    from otaman_cli.commands import spec as spec_cmd

    _change(program, "auto-one", delivery="auto")
    monkeypatch.setattr(spec_cmd, "find_project_root", lambda: program.root)
    monkeypatch.setattr(
        "otaman_cli.main._resolve_bus_paths",
        lambda root: (
            root / ".agents" / "bus" / "active",
            root / ".agents" / "bus" / "active" / "acks",
        ),
    )
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "auto-one" in out and "auto-delivery" in out


# ---------------------------------------------------------------------------
# badge in the lifecycle table + approve-auto via the ProposalScreen


@_textual
def test_lifecycle_table_shows_auto_badge(program):
    _change(program, "auto-one", delivery="auto")
    from textual.widgets import DataTable

    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one("#lifecycle-table", DataTable)
            assert any("[auto]" in str(c) for c in table.get_row_at(0))
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_proposal_screen_approve_auto(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage_scr(program)
    from otaman_cli.console.app import OtamanConsole, ProposalScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ProposalScreen(program, proposal))
            await pilot.pause()
            await app.screen.run_action("approve_auto")
            await pilot.pause()
            await pilot.press("enter")  # submit empty reason in the modal
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    active = program.root / ".agents" / "bus" / "active"
    bc = [f for f in active.glob("*.md") if "spec-change-approved" in f.name]
    assert bc and "delivery: auto" in bc[0].read_text("utf-8")
