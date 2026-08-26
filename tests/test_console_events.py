"""interactive-human-console 1.3 — event-source interface + polling provider.

The polling provider (thread, real behavior) and the console's use of the ONE
swappable interface. Provider swap requires no console change — the screen
only ever calls start/snapshot/stop.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path

import pytest

from otaman_cli.console import bus
from otaman_cli.console.events import EventSource, PollingEventSource, make_event_source

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "p"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: p\n", encoding="utf-8")
    return bus.Program(name="p", root=root)


def _stage(program: bus.Program, stem: str) -> Path:
    f = program.root / ".agents" / "bus" / "active" / f"{stem}.md"
    f.write_text(
        "---\nfrom: a\nto: human\npriority: normal\ntype: spec-change-request\n"
        "timestamp: t\nstatus: pending\n---\n\n## Subject: Spec change request: x\n\nb\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# PollingEventSource (real thread)


def test_polling_is_an_eventsource(program):
    assert isinstance(PollingEventSource(program), EventSource)


def test_polling_snapshot_matches_bus(program):
    src = PollingEventSource(program)
    assert src.snapshot() == []
    _stage(program, "20260101T000000-a-to-human-spec-change-request")
    assert len(src.snapshot()) == 1


def test_polling_fires_on_new_proposal(program):
    src = PollingEventSource(program, interval=0.05)
    fired = threading.Event()
    src.start(lambda: fired.set())
    try:
        _stage(program, "20260101T000000-a-to-human-spec-change-request")
        assert fired.wait(2.0), "on_change not fired for a new proposal"
    finally:
        src.stop()


def test_polling_fires_on_removal(program):
    stem = "20260101T000000-a-to-human-spec-change-request"
    _stage(program, stem)
    src = PollingEventSource(program, interval=0.05)
    fired = threading.Event()
    src.start(lambda: fired.set())
    try:
        # ack it → drops out of pending → set changes → fire
        (program.root / ".agents" / "bus" / "active" / "acks" / f"{stem}.human.ack").write_text(
            "approved\n", encoding="utf-8"
        )
        assert fired.wait(2.0)
    finally:
        src.stop()


def test_polling_stop_halts_callbacks(program):
    src = PollingEventSource(program, interval=0.05)
    count = {"n": 0}
    src.start(lambda: count.__setitem__("n", count["n"] + 1))
    src.stop()
    _stage(program, "20260101T000000-a-to-human-spec-change-request")
    threading.Event().wait(0.3)  # give any rogue thread time to (not) fire
    assert count["n"] == 0


def test_make_event_source_is_polling(program):
    assert isinstance(make_event_source(program), PollingEventSource)


# ---------------------------------------------------------------------------
# console wiring — screen starts + stops the source (provider-agnostic)


@_textual
def test_pending_screen_starts_and_stops_source(program, monkeypatch):
    class _Fake:
        def __init__(self):
            self.on_change = None
            self.stopped = False

        def snapshot(self):
            return []

        def start(self, on_change):
            self.on_change = on_change

        def stop(self):
            self.stopped = True

    fake = _Fake()
    monkeypatch.setattr("otaman_cli.console.events.make_event_source", lambda program: fake)

    from otaman_cli.console.app import OtamanConsole, PendingListScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            assert callable(fake.on_change)  # screen wired the source
            await app.action_quit()

    asyncio.run(go())
    assert fake.stopped is True  # screen stopped its own source on unmount
