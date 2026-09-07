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


def test_only_authored_changes_listed(program):
    _change(program, "ready-for-review", stage="authored")
    _change(program, "still-draft", stage="approved")  # authored-but-unauthored? no: approved
    _change(program, "shipped", stage="dispatched")
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


def test_advance_refuses_non_authored(program, monkeypatch):
    _change(program, "c", stage="approved")  # not yet authored
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    ok, msg = artifacts.advance_to_spec_approved(program, "c")
    assert ok is False and "only an 'authored'" in msg


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
