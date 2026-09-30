"""console-reactive-store 1.2 — the Loader: the store's only reader.

D1 gives IO exactly one owner. Every other console module reads the store; this
one reads the filesystem, and it is the only place a render-path guard (2.3) has
to carve an exception for.

Two jobs:

**Cold start.** One bulk scan into one dispatch, so first paint sees a complete
store or an empty one — never a half-filled one growing under it. The scan is
frontmatter-only: `list_human_queue` already reads a bounded head per file and
carries no bodies, which is what took the console's bus scan from 7-9s down
(5.1 finding #5). Bodies stay lazy — `bus.read_body` fetches one when a detail
screen opens, where a single read is free and 700 were not.

**Deltas.** `console/events.py` shipped its provider seam with no consumers and
survived deletion only on the unique-ownership question; this is its intended
consumer (D3). The poll provider goes first for correctness, fswatch second for
latency (task 1.3, otaman-fswatch) — behind the same interface, so neither the
Loader nor any view changes when it swaps.

Deltas are DEBOUNCED into versioned batches. A burst of filesystem events — one
write touching several files, or a poll catching three at once — becomes a
single dispatch and a single version. Applying them one at a time would let a
render land between two halves of one logical change, which is the exact
guarantee the store's versioning exists to give.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from otaman_cli.console.bus import Program, list_human_queue
from otaman_cli.console.store import ReplaceKind, Snapshot, Store

#: Entity kind for a bus message. Named here rather than inline so the Loader
#: and the views that query it cannot drift on the spelling.
KIND_MESSAGE = "message"

#: How long to wait for the burst to finish before dispatching. Long enough to
#: coalesce a multi-file write, short enough that an external change still feels
#: immediate — the spec's budget is "within the debounce window".
DEBOUNCE_SECONDS = 0.25


def message_deriver(fields: dict[str, Any]) -> dict[str, Any]:
    """Fields computed once at ingest, never per render.

    Each of these was a per-row computation on a render path: the display name
    of a message type, whether the row is a decision (which drives its actions),
    and a sortable timestamp. Cheap individually and not cheap across a few
    thousand rows, several times a second, while someone holds a key down.
    """
    msg_type = str(fields.get("msg_type") or "")
    return {
        "is_decision": msg_type in ("spec-change-request", "outcome-proposal"),
        "type_name": {
            "spec-change-request": "SCR",
            "outcome-proposal": "outcome proposal",
        }.get(msg_type, msg_type or "message"),
        "sort_key": str(fields.get("timestamp") or ""),
    }


def _fields_of(proposal: Any) -> dict[str, Any]:
    """The raw fields of one Proposal — everything BUT the body.

    The body is deliberately absent rather than empty-stringed: a view that
    wants it calls `bus.read_body(proposal)`, and keeping it out of the store
    means a cold scan never pays for text nobody is looking at.
    """
    return {
        "stem": proposal.stem,
        "subject": proposal.subject,
        "from_agent": proposal.from_agent,
        "timestamp": proposal.timestamp,
        "priority": proposal.priority,
        "path": proposal.path,
        "msg_type": proposal.msg_type,
    }


class Loader:
    """Fills a :class:`Store` from one program's bus, and keeps it filled."""

    def __init__(
        self,
        program: Program,
        store: Store,
        *,
        source: Any | None = None,
        lister: Callable[[Program], list] | None = None,
        debounce: float = DEBOUNCE_SECONDS,
    ) -> None:
        self.program = program
        self.store = store
        self.debounce = debounce
        #: What the provider watches MUST match what the store holds, or a new
        #: plain message would never trip a refresh and the surface would sit
        #: confidently out of date on exactly the rows it was merged to carry.
        self._lister = lister or list_human_queue
        self._source = source
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._stopped = False
        store.register_deriver(KIND_MESSAGE, message_deriver)

    # -- scanning ---------------------------------------------------------

    def _scan(self) -> ReplaceKind:
        """The whole current set as ONE action.

        ReplaceKind rather than a stream of Upserts because a message that has
        been acked is GONE from the pending set, and an upsert-only refresh
        would leave it on screen forever — actionable and already handled.
        """
        items = self._lister(self.program)
        return ReplaceKind(KIND_MESSAGE, tuple((p.stem, _fields_of(p)) for p in items))

    def cold_start(self) -> Snapshot:
        """Fill the store in one batch. Returns the resulting snapshot.

        One dispatch on purpose: first paint sees a complete store or an empty
        one, never one filling in underneath it.
        """
        return self.store.dispatch(self._scan())

    def refresh(self) -> Snapshot:
        """Rescan now, bypassing the debounce (an explicit user refresh)."""
        return self.store.dispatch(self._scan())

    # -- deltas -----------------------------------------------------------

    def start(self) -> None:
        """Begin applying deltas. Does NOT cold-start — call that explicitly.

        Kept separate because `start` is called from a screen's mount, and the
        provider's own contract already forbids scanning on the calling thread
        there: a synchronous scan at mount blocked first paint (5.1 finding #4).
        """
        if self._source is None:
            from otaman_cli.console.events import make_event_source

            self._source = make_event_source(self.program, lister=self._lister)
        self._stopped = False
        self._source.start(self._on_change)

    def _on_change(self) -> None:
        """Provider says the set may have moved — coalesce, then dispatch once."""
        with self._lock:
            if self._stopped:
                return
            if self._timer is not None:
                self._timer.cancel()  # a later event restarts the quiet period
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            self._timer = None
            if self._stopped:
                return
        try:
            self.store.dispatch(self._scan())
        except Exception:  # noqa: BLE001 - a failed rescan must not kill the watcher
            # The store keeps the last good snapshot, which is stale rather than
            # wrong, and the next event tries again. Taking the provider thread
            # down would leave the console silently frozen instead.
            return

    def stop(self) -> None:
        """Stop watching and cancel any pending flush. Idempotent."""
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._source is not None:
            try:
                self._source.stop()
            except Exception:  # noqa: BLE001 - teardown races are normal
                pass


__all__ = [
    "DEBOUNCE_SECONDS",
    "KIND_MESSAGE",
    "Loader",
    "message_deriver",
]
