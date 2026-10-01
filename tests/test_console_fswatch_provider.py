"""console-reactive-store 1.3, console half — fswatch behind the same seam.

`console/events.py` shipped its provider protocol with zero consumers and
survived deletion only on the unique-ownership question. fswatch's
BusEventSource satisfies it structurally, with no otaman-cli import on their
side, so the dependency direction stays cli -> fswatch and this is the only
place that changed when it landed.

Two failures have to be survived, and only one is visible at construction:
otaman-fswatch is not a declared dependency (the console must run without it),
and inotify watch limits are per-user and exhaustible, so a provider that
imports fine can still fail to START. fswatch suggested the second fallback
themselves.
"""

from __future__ import annotations

import pytest

from otaman_cli.console.events import FallbackEventSource, PollingEventSource


class _Spy:
    def __init__(self, *, fail_start=False, rows=()):
        self.fail_start = fail_start
        self.rows = list(rows)
        self.started = False
        self.stopped = False

    def snapshot(self):
        return self.rows

    def start(self, on_change):
        if self.fail_start:
            raise OSError("inotify watch limit reached")
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    return Program(name="demo", root=root)


def _src(program, **kw):
    kw.setdefault("lister", lambda p: [])
    return FallbackEventSource(program, **kw)


# ---------------------------------------------------------------------------
# which provider


def test_fswatch_is_used_when_it_is_available(program):
    primary = _Spy()
    src = _src(program, primary=primary)
    src.start(lambda: None)
    assert src.watching is True and primary.started
    assert src.degraded_reason == ""


def test_polling_is_used_when_fswatch_is_absent(program):
    """It is not a declared dependency — the console must run without it."""
    src = _src(program, primary=None)
    assert src.watching is False
    assert "not installed" in src.degraded_reason


def test_the_console_keeps_working_without_fswatch(program):
    src = _src(program, primary=None)
    src.start(lambda: None)
    src.stop()


# ---------------------------------------------------------------------------
# the failure that is only visible at start()


def test_a_provider_that_cannot_start_falls_back(program):
    """inotify limits are per-user and exhaustible: importing fine is not the
    same as being able to watch."""
    fallback = _Spy()
    src = _src(program, primary=_Spy(fail_start=True), fallback=lambda: fallback)
    src.start(lambda: None)
    assert fallback.started, "the poller must take over"
    assert src.watching is False


def test_the_fallback_records_why(program):
    """A console that silently fell back looks like one that is merely slow."""
    src = _src(program, primary=_Spy(fail_start=True), fallback=lambda: _Spy())
    src.start(lambda: None)
    assert "could not start" in src.degraded_reason
    assert "inotify" in src.degraded_reason


def test_a_failing_poller_raises_rather_than_pretending(program):
    """Nothing left to fall back to — swallowing this would leave a console
    with no live updates at all and no indication.

    Constructed with NO primary, so the poller is active from the start: that is
    the branch where a start failure has nowhere to go. An earlier version of
    this test used a failing primary AND a failing fallback, which exercised the
    other path and passed whether or not the guard existed.
    """
    built = []

    def factory():
        spy = _Spy(fail_start=True)
        built.append(spy)
        return spy

    src = _src(program, primary=None, fallback=factory)
    with pytest.raises(OSError):
        src.start(lambda: None)

    # The raise happens either way — what the guard prevents is BUILDING AND
    # STARTING A SECOND POLLER when the poller is already the active one, which
    # would leave two of them behind. I had this test asserting only the raise,
    # and it passed with the guard removed; the side effect is the real subject.
    assert len(built) == 1, "a second poller was constructed after the first failed"


def test_both_providers_failing_still_raises(program):
    """The other order: fswatch present but unstartable, poller also broken."""
    src = _src(program, primary=_Spy(fail_start=True), fallback=lambda: _Spy(fail_start=True))
    with pytest.raises(OSError):
        src.start(lambda: None)


# ---------------------------------------------------------------------------
# delegation


def test_snapshot_comes_from_the_active_provider(program):
    src = _src(program, primary=_Spy(rows=["a", "b"]))
    assert src.snapshot() == ["a", "b"]


def test_snapshot_follows_the_fallback_after_a_failed_start(program):
    src = _src(
        program,
        primary=_Spy(fail_start=True, rows=["primary"]),
        fallback=lambda: _Spy(rows=["fallback"]),
    )
    src.start(lambda: None)
    assert src.snapshot() == ["fallback"]


def test_stop_stops_whichever_is_live(program):
    fallback = _Spy()
    src = _src(program, primary=_Spy(fail_start=True), fallback=lambda: fallback)
    src.start(lambda: None)
    src.stop()
    assert fallback.stopped


# ---------------------------------------------------------------------------
# the seam itself


def test_make_event_source_returns_the_fallback_wrapper(program):
    from otaman_cli.console.events import make_event_source

    assert isinstance(make_event_source(program), FallbackEventSource)


def test_the_poller_remains_available_as_the_floor(program):
    """Swapping providers must never remove the one that always works."""
    assert PollingEventSource(program) is not None


def test_cli_does_not_import_fswatch_at_module_scope():
    """Optional dependency: a top-level import would make the console refuse to
    start on a host without it."""
    from pathlib import Path

    import otaman_cli.console.events as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    head = src.split("def ", 1)[0]
    assert "otaman_fswatch" not in head, "fswatch must be imported lazily, inside the probe"
