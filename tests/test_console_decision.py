"""interactive-human-console 1.2 — proposal viewer + decision flow.

Identity resolution (OTAMAN_HUMAN → roster, unverified fallback), the
approve/reject decision routing through the same ledger-gated privileged
writers as `otaman approve` (stamped with the SSH-derived identity), and the
Textual ProposalScreen pilot. The autouse conftest isolates the confirmation
ledger; the console tests skip cleanly without the `console` extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from otaman_cli.console import bus, decision
from otaman_cli.console.identity import resolve_identity

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\n"
        "human-roster:\n"
        "  - name: roman\n    email: roman@x.io\n    roles: [cofounder]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    return bus.Program(name="demo", root=root)


def _stage(program: bus.Program, stem="20260101T000000-core-agent-to-human-spec-change-request"):
    (program.root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\nfrom: core-agent\nto: human\npriority: high\ntype: spec-change-request\n"
        "timestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: add widget\n\nThe **body** of the proposal.\n",
        encoding="utf-8",
    )
    return bus.list_pending_proposals(program)[0]


def _broadcasts(program: bus.Program, kind: str) -> list[Path]:
    active = program.root / ".agents" / "bus" / "active"
    return [f for f in active.glob("*.md") if kind in f.name]


# ---------------------------------------------------------------------------
# identity


def test_identity_verified_when_in_roster(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ident = resolve_identity(program.root)
    assert ident.verified is True
    assert ident.audit_label == "roman"


def test_identity_unverified_when_not_in_roster(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "ghost")
    ident = resolve_identity(program.root)
    assert ident.verified is False
    assert "unverified-identity" in ident.audit_label


def test_identity_absent_is_unknown_unverified(program):
    ident = resolve_identity(program.root)
    assert ident.operator == "unknown-operator" and ident.verified is False


# ---------------------------------------------------------------------------
# decision — reuses the privileged ledger-gated writers, stamps identity


def test_approve_writes_broadcast_and_stamps_identity(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage(program)
    ident = resolve_identity(program.root)
    ok, msg = decision.approve(program, proposal, ident)
    assert ok is True
    bc = _broadcasts(program, "spec-change-approved")
    assert bc, "no approval broadcast written"
    text = bc[0].read_text("utf-8")
    assert "Confirmed in otaman -i by roman" in text  # identity stamped in the audit record
    acks = program.root / ".agents" / "bus" / "active" / "acks"
    assert (acks / f"{proposal.stem}.human.ack").read_text("utf-8").strip() == "approved"
    # decided proposal drops off the pending list
    assert bus.list_pending_proposals(program) == []


def test_approve_stamps_unverified_when_identity_absent(program):
    proposal = _stage(program)
    ident = resolve_identity(program.root)  # OTAMAN_HUMAN unset
    ok, _ = decision.approve(program, proposal, ident)
    assert ok is True
    text = _broadcasts(program, "spec-change-approved")[0].read_text("utf-8")
    assert "unverified-identity" in text


def test_reject_writes_rejection_and_stamps_identity(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage(program)
    ident = resolve_identity(program.root)
    ok, _ = decision.reject(program, proposal, ident, reason="not now")
    assert ok is True
    rj = _broadcasts(program, "spec-change-rejected")
    assert rj and "not now" in rj[0].read_text("utf-8")
    assert "Rejected in otaman -i by roman" in rj[0].read_text("utf-8")
    assert bus.list_pending_proposals(program) == []


# ---------------------------------------------------------------------------
# ProposalScreen pilot


@_textual
def test_proposal_screen_approve_flow(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    proposal = _stage(program)
    from otaman_cli.console.app import OtamanConsole, PendingListScreen, ProposalScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            app.push_screen(ProposalScreen(program, proposal))
            await pilot.pause()
            assert isinstance(app.screen, ProposalScreen)
            await app.screen.run_action("approve")
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    assert _broadcasts(program, "spec-change-approved"), "approval not written via the screen"


@_textual
def test_proposal_screen_renders_body(program):
    proposal = _stage(program)
    from textual.widgets import MarkdownViewer

    from otaman_cli.console.app import OtamanConsole, ProposalScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ProposalScreen(program, proposal))
            await pilot.pause()
            assert app.screen.query_one("#proposal-body", MarkdownViewer) is not None
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# regression: the CLI reject path still works after extracting _perform_rejection


def test_cli_reject_still_works_after_refactor(tmp_path, monkeypatch):
    from unittest import mock

    from otaman_cli.commands.approve import cmd_approve

    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / "platform.yaml").write_text("project: t\nrepos: []\n", encoding="utf-8")
    stem = "20260101T000000-core-agent-to-human-spec-change-request"
    (tmp_path / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\nfrom: core-agent\nto: human\npriority: normal\ntype: spec-change-request\n"
        "timestamp: t\nstatus: pending\n---\n\n## Subject: Spec change request: x\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="CONFIRM"),
    ):
        rc = cmd_approve(["reject", stem, "-d", "nope"])
    assert rc == 0
    active = tmp_path / ".agents" / "bus" / "active"
    assert any("spec-change-rejected" in f.name for f in active.glob("*.md"))
