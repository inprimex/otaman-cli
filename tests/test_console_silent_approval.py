"""Differential repro for the console silent-approval-loss defect (2026-09-09).

Roman spec-approved ``console-ux-redesign`` in ``otaman -i`` and NOTHING landed
— no stage write, no commit, no broadcast, no error — while a tenant-local
spec-approve 15 minutes later on the SAME session worked. spec-agent's plan: a
pilot that drives the review-screen approve against a cux-shaped change and
asserts write + commit + broadcast, plus an approve-on-stale-state pilot (the
change folder moves under the open screen, as three merges did under Roman's).

Acceptance: the happy path lands all three artifacts AND the session log records
the action; the stale path fails LOUDLY (error notify + journaled failure) and
writes no spurious success broadcast. The Textual pilots skip without the
console extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess

import pytest
import yaml

from otaman_cli.console import artifacts, bus

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


def _git(prog, *a):
    return subprocess.run(["git", "-C", str(prog.root), *a], capture_output=True, text=True)


@pytest.fixture
def gitprog(tmp_path, monkeypatch):
    """A git-backed program whose specs repo can be committed to (advance
    commits the stage bump). Roster has an eligible approver; OTAMAN_HUMAN=roman."""
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "specs" / "openspec" / "changes").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nspecs:\n  path: specs\n"
        "human-roster:\n  - name: roman\n    email: roman@x.io\n    roles: [cto, approver]\n",
        encoding="utf-8",
    )
    prog = bus.Program(name="demo", root=root)  # frozen dataclass
    _git(prog, "init", "-q")
    _git(prog, "config", "user.email", "t@x.io")
    _git(prog, "config", "user.name", "t")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    return prog


def _cux_change(prog, name="console-ux-redesign"):
    """A change shaped like the one that silently failed: authored stage PLUS the
    extra .openspec.yaml fields (delivery: auto, parenthesized approver, outcome
    line) that were candidate root causes — they must NOT break the write."""
    d = prog.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    (d / ".openspec.yaml").write_text(
        yaml.safe_dump(
            {
                "stage": "authored",
                "delivery": "auto",
                "approved_by": "roman (cto)",
                "outcome": "self-archiving review surface",
            }
        ),
        encoding="utf-8",
    )
    for f in ("proposal.md", "design.md", "tasks.md"):
        (d / f).write_text(f"# {f}\n\ncux content\n", encoding="utf-8")
    specs = d / "specs" / "console-ux"
    specs.mkdir(parents=True)
    (specs / "spec.md").write_text("## ADDED\n\n- delta\n", encoding="utf-8")
    _git(prog, "add", "-A")
    _git(prog, "commit", "-q", "-m", "seed cux")
    return d


def _broadcasts(prog, kind):
    active = prog.root / ".agents" / "bus" / "active"
    return [f for f in active.glob("*.md") if kind in f.name]


def _log_records(app):
    import json

    path = app.session_log.path
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


# ---------------------------------------------------------------------------


@_textual
def test_approve_writes_commits_and_broadcasts(gitprog, tmp_path):
    # The differential repro, GREEN: approve on the cux-shaped change lands all
    # three durable artifacts (stage write, git commit, bus broadcast).
    d = _cux_change(gitprog)
    from otaman_cli.console.app import ChangeReviewScreen, OtamanConsole

    async def go():
        app = OtamanConsole([gitprog], search_root=gitprog.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            change = artifacts.list_authored_changes(gitprog)[0]
            app.push_screen(ChangeReviewScreen(gitprog, change))
            await pilot.pause()
            await app.screen.run_action("approve")
            await pilot.pause()
            await pilot.press("enter")  # submit empty reason
            await pilot.pause()
            recs = _log_records(app)
            await app.action_quit()
            return recs

    recs = asyncio.run(go())

    from otaman_core.spec_lifecycle import read_stage

    # (1) stage write, (2) committed (extra yaml fields preserved), (3) broadcast
    assert read_stage(d / ".openspec.yaml") == "spec-approved"
    data = yaml.safe_load((d / ".openspec.yaml").read_text("utf-8"))
    assert data["delivery"] == "auto" and data["outcome"] == "self-archiving review surface"
    status = _git(gitprog, "status", "--porcelain", "--", "specs/openspec/changes")
    assert status.stdout.strip() == ""  # committed, not left dirty
    bc = _broadcasts(gitprog, "spec-approved")
    assert bc and "console-ux-redesign" in bc[0].read_text("utf-8")
    # (4) the session log recorded the action result (observability)
    result = [r for r in recs if r["event"] == "action-result"]
    assert result and result[-1]["ok"] is True


@_textual
def test_approve_on_stale_state_fails_loudly(gitprog, tmp_path):
    # The change folder moves out from under the open review screen (as three
    # merges did under Roman's session). Approve must NOT silently no-op: it
    # fails loudly (journaled failure) and writes no success broadcast.
    d = _cux_change(gitprog, name="moved-under-me")
    from otaman_cli.console.app import ChangeReviewScreen, OtamanConsole

    async def go():
        app = OtamanConsole([gitprog], search_root=gitprog.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            change = artifacts.list_authored_changes(gitprog)[0]
            app.push_screen(ChangeReviewScreen(gitprog, change))
            await pilot.pause()
            # simulate the merge: the folder is archived away while the screen is open
            archive = d.parent / "archive" / "2026-09-09-moved-under-me"
            archive.parent.mkdir(parents=True, exist_ok=True)
            d.rename(archive)
            await app.screen.run_action("approve")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            recs = _log_records(app)
            still_here = isinstance(app.screen, ChangeReviewScreen)
            await app.action_quit()
            return recs, still_here

    recs, still_here = asyncio.run(go())

    # loud: the log carries a failure (either a clean ok=False result or a caught
    # exception) for the approve — never an unrecorded silent drop.
    approve_events = [
        r
        for r in recs
        if r.get("action", "").startswith("approve")
        and r["event"] in ("action-result", "action-exception")
    ]
    assert approve_events, "approve produced no journaled outcome — that is the silent-loss bug"
    failed = any(r["event"] == "action-exception" or r.get("ok") is False for r in approve_events)
    assert failed  # the outcome is a visible failure
    # no spurious success broadcast, and the screen was NOT popped (approve didn't 'succeed')
    assert _broadcasts(gitprog, "spec-approved") == []
    assert still_here
