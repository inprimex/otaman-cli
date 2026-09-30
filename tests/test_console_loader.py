"""console-reactive-store 1.2 — the Loader.

The store's only reader. The properties that matter are the ones that decide
whether a view can trust it: a cold start must land in one batch, a burst must
coalesce into one version, an acked message must actually disappear, and a
failed rescan must leave a stale store rather than a dead watcher.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from otaman_cli.console.loader import KIND_MESSAGE, Loader, message_deriver
from otaman_cli.console.store import Store


@dataclass(frozen=True)
class FakeProposal:
    stem: str
    subject: str = "s"
    from_agent: str = "a"
    timestamp: str = "2026-09-30T10:00:00Z"
    priority: str = "normal"
    path: Path = Path("/tmp/x.md")
    body: str = ""
    msg_type: str = "spec-change-request"


class FakeSource:
    """Stands in for the poll provider — the console never calls anything else."""

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
        self.on_change()


def _loader(items, *, debounce=0.01, source=None):
    store = Store()
    box = {"items": list(items)}
    ldr = Loader(
        program=object(),
        store=store,
        source=source,
        lister=lambda program: box["items"],
        debounce=debounce,
    )
    return ldr, store, box


# ---------------------------------------------------------------------------
# cold start


def test_cold_start_fills_the_store_in_one_version():
    """First paint must see a complete store or an empty one — never one
    filling in underneath it."""
    ldr, store, _ = _loader([FakeProposal("m1"), FakeProposal("m2"), FakeProposal("m3")])
    before = store.version
    ldr.cold_start()
    assert store.version == before + 1
    assert len(store.snapshot().of_kind(KIND_MESSAGE)) == 3


def test_cold_start_carries_no_bodies():
    """The whole reason the scan is affordable: 700 bodies were not free."""
    ldr, store, _ = _loader([FakeProposal("m1", body="a very long body")])
    ldr.cold_start()
    entity = store.snapshot().get(KIND_MESSAGE, "m1")
    assert "body" not in entity.fields, "a body in the store is a body read at scan time"


def test_cold_start_keeps_the_path_so_the_body_can_be_fetched_later():
    ldr, store, _ = _loader([FakeProposal("m1", path=Path("/tmp/real.md"))])
    ldr.cold_start()
    assert store.snapshot().get(KIND_MESSAGE, "m1").get("path") == Path("/tmp/real.md")


def test_an_empty_bus_is_an_empty_store_not_an_error():
    ldr, store, _ = _loader([])
    ldr.cold_start()
    assert len(store.snapshot()) == 0


# ---------------------------------------------------------------------------
# what the deriver precomputes


def test_derived_fields_land_at_ingest():
    ldr, store, _ = _loader([FakeProposal("m1", msg_type="outcome-proposal")])
    ldr.cold_start()
    e = store.snapshot().get(KIND_MESSAGE, "m1")
    assert e.get("is_decision") is True
    assert e.get("type_name") == "outcome proposal"


def test_a_plain_message_is_not_a_decision():
    """`is_decision` drives which actions a row offers — a wrong one here
    offers approve/reject on something that cannot be decided."""
    assert message_deriver({"msg_type": "info"})["is_decision"] is False
    assert message_deriver({"msg_type": "spec-change-request"})["is_decision"] is True


def test_an_unknown_type_still_gets_a_display_name():
    assert message_deriver({"msg_type": "brand-new-type"})["type_name"] == "brand-new-type"
    assert message_deriver({})["type_name"] == "message"


# ---------------------------------------------------------------------------
# deltas


def test_an_acked_message_disappears_rather_than_lingering():
    """An upsert-only refresh would leave a handled row on screen forever —
    actionable, and already dealt with."""
    ldr, store, box = _loader([FakeProposal("m1"), FakeProposal("m2")])
    ldr.cold_start()
    box["items"] = [FakeProposal("m2")]
    ldr.refresh()
    assert {e.id for e in store.snapshot().of_kind(KIND_MESSAGE)} == {"m2"}


def test_a_burst_of_events_coalesces_into_one_version():
    """The guarantee the store's versioning exists to give: a render cannot
    land between two halves of one logical change."""
    source = FakeSource()
    ldr, store, box = _loader([FakeProposal("m1")], debounce=0.05, source=source)
    ldr.cold_start()
    v = store.version
    ldr.start()
    box["items"] = [FakeProposal("m1"), FakeProposal("m2")]
    for _ in range(5):
        source.fire()
    time.sleep(0.25)
    assert store.version == v + 1, "five events must produce one batch, not five"
    assert len(store.snapshot().of_kind(KIND_MESSAGE)) == 2


def test_a_later_event_restarts_the_quiet_period():
    source = FakeSource()
    ldr, store, box = _loader([], debounce=0.12, source=source)
    ldr.start()
    v = store.version
    source.fire()
    time.sleep(0.06)
    source.fire()  # still inside the window — must defer the flush
    time.sleep(0.06)
    assert store.version == v, "the flush fired before the burst settled"
    time.sleep(0.2)
    assert store.version == v + 1


def test_start_does_not_scan_on_the_calling_thread():
    """A synchronous scan at mount blocked first paint (5.1 finding #4)."""
    source = FakeSource()
    ldr, store, _ = _loader([FakeProposal("m1")], source=source)
    v = store.version
    ldr.start()
    assert store.version == v, "start() must not dispatch"
    assert source.started


def test_a_failing_rescan_leaves_the_store_stale_not_dead():
    """Stale is recoverable; a dead watcher freezes the console silently."""
    source = FakeSource()
    store = Store()
    state = {"fail": False}

    def lister(program):
        if state["fail"]:
            raise OSError("bus vanished")
        return [FakeProposal("m1")]

    ldr = Loader(program=object(), store=store, source=source, lister=lister, debounce=0.01)
    ldr.cold_start()
    v = store.version
    state["fail"] = True
    ldr.start()

    # Called DIRECTLY, not through the timer: an exception raised inside a
    # Timer thread dies quietly, so asserting "the store stayed stale" passes
    # whether or not anything is caught. An earlier version of this test did
    # exactly that and proved nothing. In a TUI an escaped traceback also
    # corrupts the display, which is the real consequence being guarded.
    ldr._flush()

    assert store.version == v, "a failed rescan must not advance the version"
    assert store.snapshot().get(KIND_MESSAGE, "m1") is not None, "last good state survives"

    # ...and the watcher is still live: the next good event applies.
    state["fail"] = False
    source.fire()
    time.sleep(0.15)
    assert store.version == v + 1, "a failure must not stop later updates"


# ---------------------------------------------------------------------------
# lifecycle


def test_stop_cancels_a_pending_flush():
    source = FakeSource()
    ldr, store, box = _loader([], debounce=0.2, source=source)
    ldr.start()
    v = store.version
    source.fire()
    ldr.stop()
    time.sleep(0.35)
    assert store.version == v, "a flush must not fire after stop"
    assert source.stopped


def test_stop_leaves_no_pending_timer():
    """The `_stopped` flag already prevents a late dispatch, so the cancel is
    about the THREAD: a stopped console must not leave a timer alive waiting to
    fire into a torn-down app."""
    source = FakeSource()
    ldr, _, _ = _loader([], debounce=5.0, source=source)
    ldr.start()
    source.fire()
    assert ldr._timer is not None
    ldr.stop()
    assert ldr._timer is None, "stop() must cancel and clear the pending timer"


def test_stop_is_idempotent():
    source = FakeSource()
    ldr, _, _ = _loader([], source=source)
    ldr.start()
    ldr.stop()
    ldr.stop()


def test_events_after_stop_are_ignored():
    source = FakeSource()
    ldr, store, _ = _loader([], debounce=0.01, source=source)
    ldr.start()
    ldr.stop()
    v = store.version
    source.fire()
    time.sleep(0.1)
    assert store.version == v


def test_the_loader_wires_the_poll_provider_by_default():
    """events.py shipped its seam with no consumer; this is it (D3)."""
    src = Path("src/otaman_cli/console/loader.py").read_text(encoding="utf-8")
    assert "make_event_source" in src


def test_the_loader_watches_what_the_store_holds():
    """If the provider watches a narrower set than the store renders, a new
    plain message never trips a refresh and the surface sits out of date."""
    src = Path("src/otaman_cli/console/loader.py").read_text(encoding="utf-8")
    assert "list_human_queue" in src
    assert "list_pending_proposals" not in src, "the decisions-only lister is the narrower set"


def test_concurrent_events_do_not_lose_the_final_state():
    source = FakeSource()
    ldr, store, box = _loader([], debounce=0.05, source=source)
    ldr.start()

    def fire_many():
        for _ in range(20):
            source.fire()

    box["items"] = [FakeProposal("final")]
    threads = [threading.Thread(target=fire_many) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.3)
    assert {e.id for e in store.snapshot().of_kind(KIND_MESSAGE)} == {"final"}
