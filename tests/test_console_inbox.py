"""console-ux-redesign wave 1, task 1.2 — the messages-to-human inbox.

Bus messages addressed to the human (from agents OR other humans) that aren't
decisions surface as a first-class inbox with a full read view. These cover the
Textual-free reader (includes human-to-human; excludes decisions/CC/acked) and
the pilots: Home `m` opens the inbox, and Enter opens the read view.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.inbox import list_inbox_messages

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _msg(
    program, stem, *, to, frm, mtype="info", subject="Hi", ts="2026-09-09T10:00:00Z", cc=False
):
    active, _ = program.bus_paths()
    x_cc = "x-cc: true\n" if cc else ""
    active.joinpath(f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: {frm}\nto: {to}\ntype: {mtype}\n"
        f"timestamp: {ts}\n{x_cc}status: pending\n---\n\n## Subject: {subject}\n\nbody of {stem}\n",
        encoding="utf-8",
    )
    return stem


# ---------------------------------------------------------------------------
# list_inbox_messages


def test_includes_agent_and_human_senders_excludes_others(program):
    _msg(program, "20260909T100001-agent", to="human", frm="deploy-agent", subject="from an agent")
    _msg(program, "20260909T100002-human", to="human", frm="roman", subject="from a human")
    _msg(program, "20260909T100003-scr", to="human", frm="spec-agent", mtype="spec-change-request")
    _msg(program, "20260909T100004-cc", to="human", frm="x-agent", cc=True)
    _msg(program, "20260909T100005-other", to="cli-agent", frm="core-agent")  # not to human

    msgs = list_inbox_messages(program)
    stems = {m.stem for m in msgs}
    assert "20260909T100001-agent" in stems
    assert "20260909T100002-human" in stems  # human-to-human included
    assert "20260909T100003-scr" not in stems  # decisions live in the queue
    assert "20260909T100004-cc" not in stems  # CC copy excluded
    assert "20260909T100005-other" not in stems  # not addressed to the human


def test_from_human_flag_and_subject(program):
    _msg(program, "20260909T100001-h", to="human", frm="roman", subject="Ping")
    _msg(program, "20260909T100002-a", to="human", frm="deploy-agent")
    by = {m.stem: m for m in list_inbox_messages(program)}
    assert by["20260909T100001-h"].from_human is True
    assert by["20260909T100001-h"].subject == "Ping"
    assert by["20260909T100002-a"].from_human is False  # *-agent → not human


def test_acked_message_drops_from_inbox(program):
    _msg(program, "20260909T100001-a", to="human", frm="deploy-agent")
    _, acks = program.bus_paths()
    acks.joinpath("20260909T100001-a.human.ack").write_text("read\n", encoding="utf-8")
    assert list_inbox_messages(program) == []


def test_newest_first(program):
    _msg(program, "old", to="human", frm="a-agent", ts="2026-09-01T00:00:00Z")
    _msg(program, "new", to="human", frm="b-agent", ts="2026-09-09T00:00:00Z")
    assert [m.stem for m in list_inbox_messages(program)] == ["new", "old"]


# ---------------------------------------------------------------------------
# pilots


@_textual
def test_home_m_opens_inbox(program):
    _msg(program, "20260909T100001-a", to="human", frm="deploy-agent")
    from otaman_cli.console.app import HomeScreen, InboxScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action("messages")
            await pilot.pause()
            assert isinstance(app.screen, InboxScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_inbox_enter_opens_read_view(program):
    _msg(program, "20260909T100001-a", to="human", frm="deploy-agent", subject="Read me")
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxMessageScreen, InboxScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(InboxScreen(program))
            await pilot.pause()
            lv = app.screen.query_one("#inbox-list", ListView)
            assert len(lv.children) == 1
            lv.focus()
            lv.index = 0  # highlight the row so Enter selects it
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, InboxMessageScreen)
            assert app.screen.message.subject == "Read me"
            await app.action_quit()

    asyncio.run(go())
