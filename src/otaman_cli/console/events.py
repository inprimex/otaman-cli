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

    def __init__(self, program: Program, *, interval: float = 2.0) -> None:
        self.program = program
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last: tuple[str, ...] | None = None

    def snapshot(self) -> list[Proposal]:
        return list_pending_proposals(self.program)

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


def make_event_source(program: Program) -> EventSource:
    """The default provider for Iteration 1 (polling). Swap here (or via config)
    when fswatch/NATS providers land — no console change needed."""
    return PollingEventSource(program)


__all__ = ["EventSource", "PollingEventSource", "make_event_source"]
