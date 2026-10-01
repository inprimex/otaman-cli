"""One event-source interface with swappable providers (task 1.3 / Q10).

The console learns of new/changed proposals through a SINGLE subscription
interface so the provider can migrate poll → otaman-fswatch → NATS → A2A with
NO console rework. Iteration 1 ships the polling provider.

The interface is provider-owns-its-trigger: a provider decides HOW it detects
change (a poll thread here; a filesystem watch or a NATS subscription later)
and calls the console's `on_change` callback when the pending set may have
moved. The console only ever calls `start(on_change)` / `snapshot()` /
`stop()` — swapping providers never touches console code.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from otaman_cli.console.bus import Program, Proposal, list_pending_proposals


@runtime_checkable
class EventSource(Protocol):
    """A migratable source of "the pending proposals may have changed"."""

    def snapshot(self) -> list[Proposal]:
        """The current pending set (what the console renders)."""
        ...

    def start(self, on_change: Callable[[], None]) -> None:
        """Begin watching; call *on_change* whenever the set may have changed."""
        ...

    def stop(self) -> None:
        """Stop watching and release resources (idempotent)."""
        ...


class PollingEventSource:
    """Iteration-1 provider: a daemon thread re-reads the bus every *interval*
    and fires `on_change` only when the set of pending stems actually moves.

    Thread-based so the provider owns its own trigger (the console marshals
    `on_change` back onto the UI thread). fswatch/NATS providers later swap in
    behind this same interface.
    """

    def __init__(self, program: Program, *, interval: float = 2.0, lister=None) -> None:
        self.program = program
        self.interval = interval
        # WHAT the provider watches must match what the screen SHOWS. This was
        # fixed to the decision-only lister because its only consumer showed
        # decisions. The merged Messages surface (2.1) shows the human's whole
        # queue, so a new plain message would never have tripped a refresh —
        # the surface would sit confidently out of date on exactly the rows it
        # was merged to carry.
        self._lister = lister or list_pending_proposals
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last: tuple[str, ...] | None = None

    def snapshot(self) -> list[Proposal]:
        return self._lister(self.program)

    def _stems(self) -> tuple[str, ...]:
        return tuple(p.stem for p in self.snapshot())

    def start(self, on_change: Callable[[], None]) -> None:
        # start() MUST NOT scan the bus on the calling thread: the console
        # calls it from Screen.on_mount, and a synchronous scan there blocks
        # the screen's FIRST PAINT (5.1 finding #4 — a 700+ file bus read sat
        # between picker-Enter and the "Loading…" row appearing). The baseline
        # is established inside the poll thread instead; the console's own
        # mount-time worker paints the initial list, so deferring the baseline
        # loses no change.
        self._last = None
        self._stop.clear()

        def loop() -> None:
            # wait() returns True on stop, False on timeout → poll after each gap
            while not self._stop.wait(self.interval):
                stems = self._stems()
                if self._last is None:
                    self._last = stems  # first in-thread scan sets the baseline
                    continue
                if stems != self._last:
                    self._last = stems
                    try:
                        on_change()
                    except Exception:  # noqa: BLE001 - never let a callback kill the poller
                        pass

        self._thread = threading.Thread(target=loop, name="otaman-console-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class FallbackEventSource:
    """fswatch when it works, polling when it does not (crs 1.3, console half).

    Two different failures have to be survived, and only one of them is visible
    at construction time:

    * **otaman-fswatch is not installed.** It is not a declared dependency of
      otaman-cli — the console must run without it — so the import is probed and
      the poller is used when it is absent.
    * **`start()` raises.** inotify watch limits are per-user and exhaustible, so
      a provider that imports fine can still fail to begin watching. fswatch
      suggested this fallback themselves; it is the reason this wrapper exists
      rather than a plain `if available` at construction.

    Degradation is RECORDED, never silent: `degraded_reason` says which failure
    happened, so a caller can surface "live updates are polling, not watching"
    instead of the human wondering why a change took two seconds to appear.
    """

    def __init__(self, program: Program, *, lister=None, primary=None, fallback=None) -> None:
        self.program = program
        self._lister = lister or list_pending_proposals
        self._primary = primary if primary is not None else _fswatch_source(program, self._lister)
        self._fallback_factory = fallback or (
            lambda: PollingEventSource(program, lister=self._lister)
        )
        self._active = self._primary
        self.degraded_reason = "" if self._primary is not None else "otaman-fswatch not installed"
        if self._active is None:
            self._active = self._fallback_factory()

    @property
    def watching(self) -> bool:
        """True when the fswatch provider is the live one."""
        return self._primary is not None and self._active is self._primary

    def snapshot(self) -> list[Proposal]:
        return self._active.snapshot()

    def start(self, on_change: Callable[[], None]) -> None:
        try:
            self._active.start(on_change)
            return
        except Exception as exc:  # noqa: BLE001 - see the class docstring
            if self._active is not self._primary:
                raise  # the poller itself failed; there is nothing left to try
            self.degraded_reason = f"fswatch could not start ({type(exc).__name__}: {exc})"
        self._active = self._fallback_factory()
        self._active.start(on_change)

    def stop(self) -> None:
        self._active.stop()


def _fswatch_source(program: Program, lister):
    """fswatch's provider, or None when it is not installed.

    Probed by import, not by a version check: the package is optional, and the
    question is whether THIS install has it.
    """
    try:
        from otaman_fswatch import BusEventSource
    except Exception:  # noqa: BLE001 - optional dependency → poll instead
        return None
    try:
        return BusEventSource(program.bus_paths(), lambda: lister(program))
    except Exception:  # noqa: BLE001 - a provider that cannot be built is not a provider
        return None


def make_event_source(program: Program, *, lister=None) -> EventSource:
    """The console's provider: fswatch when available, polling otherwise.

    The seam did its job — fswatch's BusEventSource satisfies the same
    `start`/`snapshot`/`stop` protocol with no otaman-cli import on their side,
    so the dependency direction stays cli -> fswatch and this is the only place
    that changed when it landed.

    *lister* lets a caller watch the set IT renders; it defaults to the pending
    decisions for backwards compatibility.
    """
    return FallbackEventSource(program, lister=lister)


__all__ = [
    "EventSource",
    "FallbackEventSource",
    "PollingEventSource",
    "make_event_source",
]
