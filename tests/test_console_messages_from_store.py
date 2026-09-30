"""console-reactive-store 2.1 — Messages renders from the store.

The console's most-walked path is list → message → back. It used to cost a full
queue re-derivation on the way back: ~500ms for 762 rows even with memoized
reads, because the screen owned its own bus scan. The store already holds the
queue, so returning is now a dict read.

"No behavior/keymap change" is part of the task, so the bindings and the
rendered content are pinned alongside the new data path.
"""

from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from otaman_cli.console.loader import KIND_MESSAGE, Loader
from otaman_cli.console.store import Store

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@dataclass(frozen=True)
class Row:
    stem: str
    subject: str = "a subject"
    from_agent: str = "core-agent"
    timestamp: str = "2026-09-30T10:00:00Z"
    priority: str = "normal"
    path: Path = Path("/tmp/x.md")
    body: str = ""
    msg_type: str = "spec-change-request"

    @property
    def is_decision(self) -> bool:
        return self.msg_type in ("spec-change-request", "outcome-proposal")


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return Program(name="demo", root=root)


class _InertSource:
    """A provider that never fires — these tests drive the store directly."""

    def start(self, on_change):
        self.on_change = on_change

    def stop(self):
        pass


def _wired(program, rows):
    store = Store()
    loader = Loader(program, store, source=_InertSource(), lister=lambda p: list(rows), debounce=0)
    loader.cold_start()
    return store, loader


# ---------------------------------------------------------------------------
# the data path


def test_the_store_is_what_holds_the_queue(program):
    store, _ = _wired(program, [Row("m1"), Row("m2")])
    assert len(store.snapshot().of_kind(KIND_MESSAGE)) == 2


def test_the_canonical_row_object_survives_the_store(program):
    """Row widgets and the decision actions take a Proposal. Keeping the
    canonical object is what makes "no behavior change" true rather than
    approximately true."""
    store, _ = _wired(program, [Row("m1", msg_type="outcome-proposal")])
    held = store.snapshot().get(KIND_MESSAGE, "m1").get("proposal")
    assert held.stem == "m1" and held.is_decision is True


@_textual
def test_messages_renders_what_the_store_holds(program):
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen, OtamanConsole

    store, loader = _wired(program, [Row("m1"), Row("m2"), Row("m3")])

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = InboxScreen(program, store=store, loader=loader)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(screen.query_one("#inbox-list", ListView).children) == 3
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_returning_to_the_list_repaints_without_re_reading(program):
    """The win. Resume must not ask the Loader to scan — the store already
    holds the queue, which is what removes the ~500ms on the most-walked path.
    """
    from otaman_cli.console.app import InboxScreen, OtamanConsole

    store, loader = _wired(program, [Row("m1"), Row("m2")])
    scans = {"n": 0}
    original = loader.refresh

    def counting_refresh():
        scans["n"] += 1
        return original()

    loader.refresh = counting_refresh

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = InboxScreen(program, store=store, loader=loader)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            scans["n"] = 0

            screen.on_screen_resume()
            await pilot.pause()
            assert scans["n"] == 0, "resume triggered a rescan — the store already had it"
            from textual.widgets import ListView

            assert len(screen.query_one("#inbox-list", ListView).children) == 2
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_store_update_repaints_with_no_keypress(program):
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen, OtamanConsole
    from otaman_cli.console.store import Upsert

    store, loader = _wired(program, [Row("m1")])

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = InboxScreen(program, store=store, loader=loader)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(screen.query_one("#inbox-list", ListView).children) == 1

            store.dispatch(Upsert(KIND_MESSAGE, "m2", {"proposal": Row("m2")}))
            await pilot.pause()
            assert len(screen.query_one("#inbox-list", ListView).children) == 2
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# no behavior / keymap change


@_textual
def test_the_keymap_is_unchanged():
    """2.1 says no keymap change. These are the keys the human has learned."""
    from otaman_cli.console.app import InboxScreen

    keys = {b.key for b in InboxScreen.BINDINGS if hasattr(b, "key")}
    assert {"a", "A", "x", "d", "escape", "r", "q"} <= keys


@_textual
def test_an_empty_queue_still_says_so(program):
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen, OtamanConsole

    store, loader = _wired(program, [])

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = InboxScreen(program, store=store, loader=loader)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            lv = screen.query_one("#inbox-list", ListView)
            assert len(lv.children) == 1  # the "nothing addressed to you" row
            await app.action_quit()

    asyncio.run(go())


def test_the_screen_holds_no_bus_read():
    """The render-path property 2.3 will enforce repo-wide."""
    import inspect

    from otaman_cli.console.app import InboxScreen

    src = inspect.getsource(InboxScreen)
    for forbidden in ("list_human_queue", "read_text", ".glob(", "iterdir"):
        assert forbidden not in src, f"render path does IO: {forbidden}"
