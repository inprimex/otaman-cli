"""SLE 2.3 / IHC iteration 2 — artifact review wired to the spec-approved stage.

The second HITL stage: a human reviews an `authored` change's artifacts, then
either advances it to `spec-approved` (approver-gated — the authoritative signal
is the stage in .openspec.yaml, plus a derived bus notification) or requests
changes back to the author. The Textual screens skip cleanly without the console
extra.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import artifacts, bus

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


def _change(program, name, *, stage, files=("proposal.md", "design.md")):
    d = program.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    import yaml

    (d / ".openspec.yaml").write_text(yaml.safe_dump({"stage": stage}), encoding="utf-8")
    for f in files:
        (d / f).write_text(f"# {f}\n\ncontent of {f}\n", encoding="utf-8")
    return d


def _broadcasts(program, kind):
    active = program.root / ".agents" / "bus" / "active"
    return [f for f in active.glob("*.md") if kind in f.name]


# ---------------------------------------------------------------------------
# list_authored_changes


def test_only_reviewable_changes_listed(program):
    _change(program, "ready-for-review", stage="authored")
    _change(program, "approved-shell", stage="approved", files=())  # no artifacts → excluded
    _change(program, "shipped", stage="dispatched")  # past → excluded
    out = artifacts.list_authored_changes(program)
    assert [c.name for c in out] == ["ready-for-review"]
    assert "proposal.md" in out[0].files and "design.md" in out[0].files


def test_read_artifact_and_traversal_guard(program):
    d = _change(program, "c", stage="authored")
    assert "content of proposal.md" in artifacts.read_artifact(d, "proposal.md")
    assert artifacts.read_artifact(d, "../../../etc/passwd") == ""  # confined


# ---------------------------------------------------------------------------
# advance_to_spec_approved — approver-gated, sets the real signal


def test_advance_requires_eligible_approver(program, monkeypatch):
    _change(program, "c", stage="authored")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)  # unresolved → not eligible
    ok, msg = artifacts.advance_to_spec_approved(program, "c")
    assert ok is False and "approver" in msg
    # stage unchanged, no broadcast
    from otaman_core.spec_lifecycle import read_stage

    assert read_stage(program.root / "specs" / "openspec" / "changes" / "c" / ".openspec.yaml") == (
        "authored"
    )
    assert _broadcasts(program, "spec-approved") == []


def test_advance_sets_stage_and_broadcasts(program, monkeypatch):
    d = _change(program, "keystone", stage="authored")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, msg = artifacts.advance_to_spec_approved(program, "keystone", reason="reviewed, ship it")
    assert ok is True and "spec-approved" in msg
    from otaman_core.spec_lifecycle import read_stage

    assert read_stage(d / ".openspec.yaml") == "spec-approved"
    bc = _broadcasts(program, "spec-approved")
    assert bc and "keystone" in bc[0].read_text("utf-8")
    assert "roman" in bc[0].read_text("utf-8")


def test_advance_refuses_pre_authored_stage(program, monkeypatch):
    _change(program, "c", stage="proposed")  # earlier than authored, no review yet
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, msg = artifacts.advance_to_spec_approved(program, "c")
    assert ok is False and "spec-approved" in msg and "authored" in msg


def test_advance_from_approved_with_artifacts(program, monkeypatch):
    # gap #3: an approved-stage change carrying artifacts (pre-convention, e.g. SLE)
    # must be advanceable in-console, not stuck.
    d = _change(program, "sle", stage="approved", files=("proposal.md", "design.md"))
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, _ = artifacts.advance_to_spec_approved(program, "sle")
    assert ok is True
    from otaman_core.spec_lifecycle import read_stage

    assert read_stage(d / ".openspec.yaml") == "spec-approved"


def test_advance_refuses_approved_without_artifacts(program, monkeypatch):
    _change(program, "shell", stage="approved", files=())  # SCR-approved shell, no artifacts
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, msg = artifacts.advance_to_spec_approved(program, "shell")
    assert ok is False and "artifacts" in msg


def test_list_includes_approved_with_artifacts(program):
    _change(program, "authored-one", stage="authored")
    _change(program, "approved-with-art", stage="approved", files=("proposal.md",))
    _change(program, "approved-shell", stage="approved", files=())
    names = {c.name for c in artifacts.list_authored_changes(program)}
    assert names == {"authored-one", "approved-with-art"}  # shell excluded


def test_advance_refuses_already_spec_approved(program, monkeypatch):
    _change(program, "c", stage="spec-approved")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, msg = artifacts.advance_to_spec_approved(program, "c")
    assert ok is False and "already at or past" in msg


# ---------------------------------------------------------------------------
# request_changes


def test_request_changes_notifies_author(program):
    _change(program, "c", stage="authored")
    ok, msg = artifacts.request_changes(program, "c", "design section 3 is underspecified")
    assert ok is True
    rr = _broadcasts(program, "review-request")
    assert rr and "spec-agent" in rr[0].read_text("utf-8")
    assert "underspecified" in rr[0].read_text("utf-8")


def test_request_changes_needs_a_comment(program):
    _change(program, "c", stage="authored")
    ok, _ = artifacts.request_changes(program, "c", "   ")
    assert ok is False


# ---------------------------------------------------------------------------
# Textual screens


@_textual
def test_browser_lists_authored_changes(program):
    _change(program, "ready", stage="authored")
    from textual.widgets import ListView

    from otaman_cli.console.app import ArtifactBrowserScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ArtifactBrowserScreen(program))
            await pilot.pause()
            lv = app.screen.query_one("#authored-list", ListView)
            assert len(lv.children) == 1
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_review_screen_approve_flow_advances_stage(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    d = _change(program, "keystone", stage="authored")
    from otaman_cli.console.app import ArtifactBrowserScreen, ChangeReviewScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ArtifactBrowserScreen(program))
            await pilot.pause()
            change = artifacts.list_authored_changes(program)[0]
            app.push_screen(ChangeReviewScreen(program, change))
            await pilot.pause()
            await app.screen.run_action("approve")
            await pilot.pause()
            await pilot.press("enter")  # submit empty reason in ReasonModal
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    from otaman_core.spec_lifecycle import read_stage

    assert read_stage(d / ".openspec.yaml") == "spec-approved"


def test_advance_commits_stage_in_git_checkout(program, monkeypatch):
    # gap #2: repo is truth — the stage bump must be COMMITTED, not left dirty.
    import subprocess

    root = program.root

    def _git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)

    _git("init", "-q")
    _git("config", "user.email", "t@x.io")
    _git("config", "user.name", "t")
    _change(program, "keystone", stage="authored")
    _git("add", "-A")
    _git("commit", "-q", "-m", "seed")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")

    ok, msg = artifacts.advance_to_spec_approved(program, "keystone")
    assert ok is True and "committed" in msg  # committed (push pending, no remote)
    # the .openspec.yaml stage change is committed, not a dirty working-tree edit
    status = _git("status", "--porcelain", "--", "specs/openspec/changes/keystone/.openspec.yaml")
    assert status.stdout.strip() == ""
    log = _git("log", "-1", "--format=%s")
    assert "spec-approved" in log.stdout


@_textual
def test_every_screen_has_a_mode_banner(program):
    from textual.widgets import Static

    from otaman_cli.console.app import (
        ArtifactBrowserScreen,
        LifecycleScreen,
        OtamanConsole,
        PendingListScreen,
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()  # picker
            assert app.screen.query_one("#mode-banner", Static)
            for screen in (
                PendingListScreen(program),
                LifecycleScreen(program),
                ArtifactBrowserScreen(program),
            ):
                app.push_screen(screen)
                await pilot.pause()
                banner = app.screen.query_one("#mode-banner", Static)
                assert banner is not None
                app.pop_screen()
                await pilot.pause()
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_pending_list_opens_browser_via_binding(program):
    from otaman_cli.console.app import ArtifactBrowserScreen, OtamanConsole, PendingListScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            await app.screen.run_action("browse")
            await pilot.pause()
            assert isinstance(app.screen, ArtifactBrowserScreen)
            await app.action_quit()

    asyncio.run(go())
