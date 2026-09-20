"""Messages refreshes itself when the bus moves (gate-6.1 follow-up).

The PendingListScreen reachability probe spec-agent asked for turned up a third
answer. The screen IS unreachable — nothing in `src/` constructs it since 2.1
merged decisions into Messages — but it was not dead weight: it held the ONLY
wiring for the event-source subsystem (`console/events.py`, built with
swappable providers so poll -> fswatch -> NATS needs no console rework).

That wiring did not come across in the merge. Measured with Messages open: a
decision landing on the bus stayed invisible until the human pressed `r`. On
the surface Roman watches for things that need him, that is the surface being
wrong rather than merely stale.

Two halves, both regressions of the same merge:
  * InboxScreen now starts/stops an event source, like the screen it replaced.
  * The source watches the set the screen RENDERS. It was fixed to the
    decision-only lister because its only consumer showed decisions; the merged
    queue also carries plain messages, so a new one would never have tripped a
    refresh.
"""

from __future__ import annotations

import asyncio

import pytest
import yaml

_textual = pytest.mark.skipif(
    __import__("importlib").util.find_spec("textual") is None, reason="textual not installed"
)


class FakeSource:
    """An event source whose trigger the TEST owns — no poll thread, no sleep."""

    def __init__(self):
        self.on_change = None
        self.started = False
        self.stopped = False

    def start(self, on_change):
        self.on_change = on_change
        self.started = True

    def stop(self):
        self.stopped = True

    def fire(self):
        """Fire from a SEPARATE thread, as a real provider does.

        The console marshals the callback with `call_from_thread`, which
        refuses to run on the app's own thread — so firing inline would test a
        shape no provider actually has.
        """
        import threading

        assert self.on_change is not None, "source was never started"
        error: list[BaseException] = []

        def run():
            try:
                self.on_change()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
                error.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)
        if error:
            raise error[0]


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "demo", "version": "1.0", "repos": []}), encoding="utf-8"
    )
    return Program(name="demo", root=root)


def _add(program, n: int, *, msg_type: str = "spec-change-request") -> None:
    active = program.root / ".agents" / "bus" / "active"
    (active / f"2026092{n}T000000-spec-agent-to-human-{msg_type}.md").write_text(
        f"---\nid: m-{n}\nfrom: spec-agent\nto: human\npriority: high\ntype: {msg_type}\n"
        f"timestamp: 2026-09-2{n}T00:00:00Z\nstatus: pending\n---\n\n## Subject: item {n}\n",
        encoding="utf-8",
    )


def _rows(screen) -> int:
    from textual.widgets import ListView

    return len(screen.query_one("#inbox-list", ListView).children)


async def _open(app, pilot, program, source):
    from otaman_cli.console.app import InboxScreen

    screen = InboxScreen(program, event_source=source)
    app.push_screen(screen)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return screen


@_textual
def test_a_decision_landing_while_watching_appears_without_a_keypress(program, tmp_path):
    """THE REGRESSION: this required pressing `r` before the fix."""
    from otaman_cli.console.app import OtamanConsole

    _add(program, 1)
    source = FakeSource()

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open(app, pilot, program, source)
            assert _rows(screen) == 1

            _add(program, 2)  # arrives while the human is looking at the screen
            source.fire()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _rows(screen) == 2, "the new decision never appeared"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_plain_message_also_triggers_a_refresh(program, tmp_path):
    """The watched set must match the RENDERED set. A source fixed to the
    decision-only lister would leave the merged queue stale on exactly the rows
    the merge added."""
    from otaman_cli.console.app import OtamanConsole

    _add(program, 1)
    source = FakeSource()

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open(app, pilot, program, source)
            _add(program, 3, msg_type="info")
            source.fire()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _rows(screen) == 2
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_screen_stops_a_source_it_created(program, tmp_path, monkeypatch):
    """A poll thread that outlives its screen is a leak."""
    import otaman_cli.console.events as events
    from otaman_cli.console.app import InboxScreen, OtamanConsole

    source = FakeSource()
    monkeypatch.setattr(events, "make_event_source", lambda program, **kw: source)

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(InboxScreen(program))  # no injected source → owns one
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert source.started
            app.pop_screen()
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    assert source.stopped, "the poll thread would outlive the screen"


@_textual
def test_an_injected_source_is_not_stopped_by_the_screen(program, tmp_path):
    """Ownership: a source the caller supplied is the caller's to stop. This is
    what makes the screen testable without a real poll thread."""
    from otaman_cli.console.app import InboxScreen, OtamanConsole

    source = FakeSource()

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = InboxScreen(program, event_source=source)
            assert screen._own_source is False
            await app.action_quit()

    asyncio.run(go())


def test_the_default_source_watches_the_merged_queue():
    """Pins the pairing rather than the plumbing: the screen's source must be
    built with the lister the screen renders from."""
    import inspect

    from otaman_cli.console.app import InboxScreen

    src = inspect.getsource(InboxScreen.on_mount)
    assert "list_human_queue" in src
    assert "make_event_source" in src


def test_the_provider_still_defaults_to_pending_decisions():
    """Backwards compatible: an existing caller that asks for no lister keeps
    the old behaviour."""
    import inspect

    from otaman_cli.console.events import PollingEventSource

    sig = inspect.signature(PollingEventSource.__init__)
    assert sig.parameters["lister"].default is None
    src = inspect.getsource(PollingEventSource.__init__)
    assert "list_pending_proposals" in src
