"""Gate 6.1 F2 — decision actions must never be silently swallowed.

Roman approved SCR 20260916T213147 twice from the merged Messages surface.
Both attempts vanished identically: he typed a reason, pressed Enter, the modal
closed — perceived success — and nothing executed. No `action-intent` in the
session journal, no `spec-change-approved` broadcast, no `.human.ack`, SCR still
pending.

The cause was NOT a dropped dismiss callback (the standing hypothesis). The
callback fired correctly with the right verb and reason. `_apply_decision` then
RE-READ `self._decision_target()` — and on the merged list that target comes
from the ListView highlight, which `on_screen_resume` → `_load()` had already
cleared while rebuilding the list as the modal popped. Target was None, the
guard returned bare, and the approval evaporated with no trace.

The old ProposalScreen path never hit this: its target is a fixed attribute,
not a live selection. The merged surface introduced the re-read.

These tests drive REAL KEYPRESSES through the whole chain. The per-key binding
guard passed while this was broken — a key that opens a modal does "dispatch" —
so the guard needs the chain, not the keypress.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
import yaml

_textual = pytest.mark.skipif(
    __import__("importlib").util.find_spec("textual") is None, reason="textual not installed"
)


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [{"name": "r", "path": "r"}],
                "specs": {"path": "../specs"},
            }
        ),
        encoding="utf-8",
    )
    (active / "20260916T000000-spec-agent-to-human-spec-change-request.md").write_text(
        "---\nid: scr-1\nfrom: spec-agent\nto: human\npriority: high\n"
        "type: spec-change-request\ntimestamp: 2026-09-16T00:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: a real decision\n\nbody\n",
        encoding="utf-8",
    )
    return Program(name="demo", root=root)


def _events(app) -> list[dict]:
    path = getattr(app.session_log, "path", None)
    if not path or not pathlib.Path(path).is_file():
        return []
    out = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


async def _open_messages(app, pilot, program):
    """Push the merged Messages list and highlight its first row."""
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen

    screen = InboxScreen(program)
    app.push_screen(screen)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    screen.query_one("#inbox-list", ListView).index = 0
    await pilot.pause()
    return screen


# ---------------------------------------------------------------------------
# the chain, driven by real keys


@_textual
def test_approving_from_the_merged_list_actually_executes(program):
    """THE REGRESSION. Real keys the whole way: a -> reason -> Enter."""
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_messages(app, pilot, program)
            await pilot.press("a")
            await pilot.pause()
            from otaman_cli.console.app import ReasonModal

            assert isinstance(app.screen, ReasonModal)
            await pilot.press("o", "k")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            events = _events(app)
            kinds = [e.get("event") for e in events]
            assert "action-intent" in kinds, f"intent never journaled: {kinds}"
            assert "action-result" in kinds, f"result never journaled: {kinds}"
            result = next(e for e in events if e["event"] == "action-result")
            assert result["ok"] is True, result

            # and the WRITES actually happened — the part Roman lost
            active = program.root / ".agents" / "bus" / "active"
            broadcast = list(active.glob("*spec-change-approved*.md"))
            assert broadcast, "no spec-change-approved broadcast was written"
            acks = list((active / "acks").glob("*.human.ack"))
            assert acks, "no .human.ack was written"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_decision_survives_the_list_reloading_under_the_modal(program):
    """The exact mechanism: dismissing the modal fires on_screen_resume ->
    _load(), which rebuilds the list and clears the highlight BEFORE the
    callback runs. The captured target must carry the decision through."""
    from textual.widgets import ListView

    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_messages(app, pilot, program)
            await pilot.press("a")
            await pilot.pause()
            # simulate the reload race deterministically: the highlight is gone
            screen.query_one("#inbox-list", ListView).index = None
            assert screen._decision_target() is None  # re-deriving would fail
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            kinds = [e.get("event") for e in _events(app)]
            assert "action-intent" in kinds, "decision lost when the highlight cleared"
            assert "action-target-lost" not in kinds
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_cancelling_writes_modal_cancelled_and_nothing_else(program):
    """Requirement 2: 'user escaped' must never look like 'callback dropped'."""
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_messages(app, pilot, program)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            kinds = [e.get("event") for e in _events(app)]
            assert "modal-cancelled" in kinds
            assert "action-intent" not in kinds  # cancelling decides nothing
            active = program.root / ".agents" / "bus" / "active"
            assert not list(active.glob("*spec-change-approved*.md"))
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_submitted_modal_is_immediately_followed_by_intent(program):
    """Requirement 2, the other half: `modal-submitted` with no `action-intent`
    after it is the signature of a broken chain, and now says so."""
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_messages(app, pilot, program)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            kinds = [e.get("event") for e in _events(app)]
            i = kinds.index("modal-submitted")
            assert "action-intent" in kinds[i:], f"submitted but never dispatched: {kinds}"
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# the guard itself


def test_apply_decision_uses_the_captured_target_not_a_live_lookup():
    """A decision belongs to the row the human was looking at when they pressed
    the key — not to whatever is selected after an async round-trip."""
    import inspect

    from otaman_cli.console.app import _DecisionActions

    sig = inspect.signature(_DecisionActions._apply_decision)
    assert "target" in sig.parameters, "_apply_decision must accept a captured target"
    src = inspect.getsource(_DecisionActions._prompt_and_decide)
    assert "target=decided" in src, "the prompt must pass the target it captured"


def test_a_lost_target_is_loud_not_silent():
    """The bare `return` is what made this invisible. If the target is somehow
    gone, it must be journaled AND shown."""
    import inspect

    from otaman_cli.console.app import _DecisionActions

    src = inspect.getsource(_DecisionActions._apply_decision)
    assert "action-target-lost" in src
    assert "notify" in src


def test_reason_modal_journals_both_outcomes():
    import inspect

    from otaman_cli.console.app import ReasonModal

    assert "modal-submitted" in inspect.getsource(ReasonModal.on_input_submitted)
    assert "modal-cancelled" in inspect.getsource(ReasonModal.action_cancel)


def test_no_modal_callback_re_derives_a_live_selection():
    """Requirement 3 — the same wiring audited everywhere, including the tree's
    `v` advance. Every other `_after` closes over a value captured before the
    modal (a node, a row, a fixed attribute); only the merged path re-read one.
    """
    import inspect
    import re

    from otaman_cli.console import app

    src = inspect.getsource(app)
    # Each ReasonModal push must be preceded by a callback that does NOT call a
    # selection accessor inside itself.
    offenders = []
    for match in re.finditer(
        r"def _after\(.*?\n(.*?)self\.app\.push_screen\(ReasonModal", src, re.S
    ):
        body = match.group(1)
        for accessor in (
            "self._decision_target()",
            "self._highlighted()",
            "_highlighted_proposal()",
        ):
            if accessor in body:
                offenders.append(accessor)
    assert offenders == [], f"modal callback re-derives a live selection: {offenders}"
