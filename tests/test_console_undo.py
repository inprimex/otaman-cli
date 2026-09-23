"""console-undo 1.1–1.4.

Roman deferred a decision item and could not tell which one. The prompt said
"Defer — enter a reason": accurate, and useless. Damage was zero only because
defer happens to be non-destructive, and reconstructing what happened meant
grepping console logs and the bus from OUTSIDE the console.

Three gaps, three sections below: confirmations name nothing, nothing is
reversible, "what did I just do" is unanswerable in-session.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from otaman_cli.console import bus
from otaman_cli.console.undo import (
    IRREVERSIBLE,
    REVERSIBLE,
    UNKNOWN_REASON,
    UndoableAction,
    classify,
    is_reversible,
    refusal_message,
    table_is_exhaustive,
    why_irreversible,
)

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


# ---------------------------------------------------------------------------
# 1.1 — confirmations name their target


def test_a_proposal_describes_itself_with_type_title_and_sender():
    p = bus.Proposal(
        stem="20260923T080000-backend-agent-to-human-scr",
        subject="add rate limiting",
        from_agent="backend-agent",
        timestamp="",
        priority="normal",
        path=Path("x"),
        body="",
        msg_type="spec-change-request",
    )
    assert p.describe() == "SCR 'add rate limiting' from backend-agent"


def test_an_outcome_proposal_reads_as_one():
    p = bus.Proposal(
        stem="s",
        subject="raise the price",
        from_agent="cpo-agent",
        timestamp="",
        priority="normal",
        path=Path("x"),
        body="",
        msg_type="outcome-proposal",
    )
    assert p.describe() == "outcome proposal 'raise the price' from cpo-agent"


def test_a_subjectless_proposal_falls_back_to_its_stem_not_an_empty_quote():
    """A prompt naming nothing is the defect being closed, so the fallback must
    still name something."""
    p = bus.Proposal(
        stem="20260923T080000-x",
        subject="",
        from_agent="a",
        timestamp="",
        priority="normal",
        path=Path("x"),
        body="",
    )
    described = p.describe()
    assert "''" not in described
    assert "20260923T080000-x" in described


@_textual
def test_the_reason_modal_shows_the_target_in_its_prompt():
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole, ReasonModal

    async def go():
        app = OtamanConsole([], search_root=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ReasonModal("defer", "SCR 'add rate limiting' from backend-agent"))
            await pilot.pause()
            prompt = str(app.screen.query_one("#reason-prompt", Static).render())
            assert "add rate limiting" in prompt
            assert "backend-agent" in prompt
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_modal_without_a_target_keeps_the_old_wording():
    """Callers that genuinely have nothing to name must not render an empty quote."""
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole, ReasonModal

    async def go():
        app = OtamanConsole([], search_root=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ReasonModal("defer"))
            await pilot.pause()
            prompt = str(app.screen.query_one("#reason-prompt", Static).render())
            assert prompt.startswith("Defer —")
            await app.action_quit()

    asyncio.run(go())


def test_every_reason_modal_call_site_passes_a_target():
    """1.1 says EVERY decision prompt names its target — a structural check, so
    a new call site cannot quietly reintroduce the unnamed prompt."""
    import re

    source = (
        Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "console" / "app.py"
    ).read_text(encoding="utf-8")
    # `push_screen(ReasonModal(...))` only — the class DEFINITION line
    # (`class ReasonModal(ModalScreen[...])`) is not a call site.
    calls = re.findall(r"push_screen\(\s*ReasonModal\(([^)]*)\)", source)
    assert calls, "found no ReasonModal call sites at all — the regex is wrong"
    unnamed = [c for c in calls if "," not in c]
    assert not unnamed, f"ReasonModal call sites with no target: {unnamed}"


# ---------------------------------------------------------------------------
# 1.2 — the reversibility table


def test_the_table_is_explicit_not_inferred():
    """1.2 requires a table. These are the classifications the proposal names."""
    for action in ("defer", "reject", "choose", "discard", "nudge"):
        assert is_reversible(action), f"{action} should be reversible"
    for action in ("spec-approve", "ratify", "archive", "accept-cost"):
        assert not is_reversible(action), f"{action} should be irreversible"


def test_an_unlisted_action_is_irreversible_not_reversible():
    """The fail-safe direction: undoing something that already left the console
    is worse than refusing to undo something that could have been."""
    assert not is_reversible("some-new-verb-nobody-classified")
    assert why_irreversible("some-new-verb-nobody-classified") == UNKNOWN_REASON


def test_every_irreversible_action_carries_a_reason():
    """A bare "cannot undo" tells the operator nothing about what already left."""
    for action, reason in IRREVERSIBLE.items():
        assert reason.strip(), f"{action} has no reason"
        assert len(reason) > 15, f"{action}'s reason is not a reason: {reason!r}"


def test_every_reversible_action_carries_its_justification():
    for action, reason in REVERSIBLE.items():
        assert reason.strip(), f"{action} has no justification"


def test_the_auto_delivery_suffix_classifies_the_same_as_its_verb():
    """`defer-auto` is a delivery mode, not a different decision."""
    assert is_reversible("defer") == is_reversible("defer-auto")
    assert is_reversible("approve") == is_reversible("approve-auto")


def test_no_action_is_in_both_tables():
    assert not (set(REVERSIBLE) & set(IRREVERSIBLE))


def test_the_known_console_actions_are_all_classified():
    """Drift check: a new console verb still works, it is simply not undoable —
    this says so out loud rather than letting an operator discover it."""
    known = [
        "defer",
        "defer-auto",
        "reject",
        "reject-auto",
        "approve",
        "approve-auto",
        "spec-approve",
        "ratify",
        "archive",
        "nudge",
        "request-changes",
        "choose",
        "discard",
        "accept-cost",
    ]
    assert table_is_exhaustive(known) == []


def test_classify_returns_both_halves():
    reversible, reason = classify("defer")
    assert reversible and "ack=False" in reason
    reversible, reason = classify("ratify")
    assert not reversible and reason


def test_a_refusal_names_the_action_and_the_consequence():
    item = UndoableAction(action="ratify", target="some-change", described="change 'some-change'")
    message = refusal_message(item)
    assert "ratify" in message
    assert "some-change" in message
    assert "agents dispatch" in message, "must say WHY, not just no"


# ---------------------------------------------------------------------------
# 1.2 — the inverse entry


def test_undo_writes_an_inverse_entry_and_erases_nothing(program):
    from otaman_cli.console.undo import write_inverse_entry

    active, _acks = program.bus_paths()
    original = active / "20260923T080000-human-to-all-console-deferred-x.md"
    original.write_text("---\nid: x\n---\n\noriginal\n", encoding="utf-8")

    stem = write_inverse_entry(
        program,
        UndoableAction(action="defer", target="20260923T080000-x", described="SCR 'x'"),
        identity=type("I", (), {"audit_label": "roman"})(),
    )

    assert original.exists(), "undo erased the original entry"
    written = (active / f"{stem}.md").read_text(encoding="utf-8")
    assert "UNDONE" in written
    assert "The original entry stands" in written
    assert "roman" in written


def test_the_inverse_stem_reads_as_a_pair_with_the_original(program):
    from otaman_cli.console.undo import write_inverse_entry

    stem = write_inverse_entry(
        program,
        UndoableAction(action="defer", target="some-target", described="SCR 'x'"),
        identity=type("I", (), {"audit_label": "roman"})(),
    )
    assert "console-undo-defer" in stem


# ---------------------------------------------------------------------------
# 1.3 — the session-actions view


def test_reading_session_actions_skips_malformed_lines(tmp_path):
    from otaman_cli.console.journal import read_session_actions

    log = tmp_path / "s.log"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-09-23T08:00:00Z",
                "event": "action-result",
                "action": "defer",
                "target": "x",
                "ok": True,
            }
        )
        + "\n"
        "{not json at all\n"
        + json.dumps({"ts": "2026-09-23T08:00:01Z", "event": "session-start"})
        + "\n",
        encoding="utf-8",
    )
    rows = read_session_actions(log)
    assert len(rows) == 1, "a broken line or a non-action event leaked in"
    assert rows[0]["action"] == "defer"


def test_a_missing_log_yields_no_rows_rather_than_raising(tmp_path):
    from otaman_cli.console.journal import read_session_actions

    assert read_session_actions(tmp_path / "absent.log") == []
    assert read_session_actions(None) == []


def test_a_row_describes_time_action_target_and_outcome():
    from otaman_cli.console.journal import describe_action_row

    ts, action, target, outcome = describe_action_row(
        {
            "ts": "2026-09-23T08:15:30Z",
            "event": "action-result",
            "action": "defer",
            "target": "scr-x",
            "ok": True,
        }
    )
    assert ts == "08:15:30"
    assert action == "defer" and target == "scr-x" and outcome == "ok"


def test_a_failed_action_reads_as_failed():
    from otaman_cli.console.journal import describe_action_row

    _ts, _a, _t, outcome = describe_action_row(
        {"event": "action-exception", "action": "ratify", "target": "c", "error": "boom"}
    )
    assert "FAILED" in outcome and "boom" in outcome


def test_an_undo_appears_in_the_view():
    from otaman_cli.console.journal import describe_action_row

    _ts, _a, _t, outcome = describe_action_row({"event": "undo", "action": "defer", "target": "x"})
    assert outcome == "undone"


# ---------------------------------------------------------------------------
# 1.4 — pilot coverage of the keyboard contract


@_textual
def test_u_with_nothing_to_undo_says_so(program):
    from otaman_cli.console.app import OtamanConsole

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.notify = lambda msg, **kw: notes.append(str(msg))
            app.action_undo()
            await pilot.pause()
            assert any("Nothing to undo" in n for n in notes)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_u_refuses_an_irreversible_action_naming_why(program):
    from otaman_cli.console.app import OtamanConsole

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.notify = lambda msg, **kw: notes.append(str(msg))
            app.last_action = UndoableAction(
                action="ratify", target="some-change", described="change 'some-change'"
            )
            app.action_undo()
            await pilot.pause()
            assert any("Cannot undo ratify" in n for n in notes)
            assert any("agents dispatch" in n for n in notes), "refusal must name the consequence"
            assert app.last_action is not None, "a refused undo must not consume the action"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_u_undoes_a_reversible_action_once(program):
    from otaman_cli.console.app import OtamanConsole

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(
                __import__("otaman_cli.console.app", fromlist=["HomeScreen"]).HomeScreen(program)
            )
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify = lambda msg, **kw: notes.append(str(msg))
            app.last_action = UndoableAction(
                action="defer", target="scr-x", described="SCR 'x' from backend-agent"
            )
            app.action_undo()
            await pilot.pause()
            assert any("Undid defer" in n for n in notes)
            # One undo per action — pressing u twice must not write two inverses.
            assert app.last_action is None
            notes.clear()
            app.action_undo()
            await pilot.pause()
            assert any("Nothing to undo" in n for n in notes)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_session_actions_view_opens_from_any_screen(program):
    from otaman_cli.console.app import OtamanConsole, SessionActionsScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_session_actions()
            await pilot.pause()
            assert isinstance(app.screen, SessionActionsScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_an_empty_session_view_says_why_rather_than_showing_a_blank_grid(program):
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_session_actions()
            await pilot.pause()
            table = app.screen.query_one("#session-actions", DataTable)
            assert table.row_count >= 1, "an empty grid tells the reader nothing"
            await app.action_quit()

    asyncio.run(go())
