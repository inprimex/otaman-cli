"""console-reactive-store 1.1 — the Store.

Every console surface currently reads the filesystem on its render path; the
store is what removes that. These tests are aimed at the four properties that
make it safe to render from, because each has a failure mode that is invisible
until it costs something:

* identity-mapped — two surfaces silently disagreeing about one entity
* derived at ingest — work creeping back onto the render path
* versioned snapshots — a render observing half a batch
* subscription notify — a subscriber's bug taking the console down with it
"""

from __future__ import annotations

import threading

from otaman_cli.console.store import (
    Entity,
    Remove,
    ReplaceKind,
    Snapshot,
    Store,
    Upsert,
    marshal_to_app,
)

# ---------------------------------------------------------------------------
# identity


def test_the_same_entity_reached_twice_is_one_object():
    """Two surfaces must not be able to disagree about one outcome."""
    s = Store()
    s.dispatch(Upsert("outcome", "JTBD-1", {"title": "first"}))
    s.dispatch(Upsert("outcome", "JTBD-1", {"status": "Backlog"}))
    snap = s.snapshot()
    assert len(snap) == 1
    assert snap.get("outcome", "JTBD-1").get("title") == "first"


def test_an_upsert_merges_rather_than_replacing():
    """A delta carrying two changed fields must not erase the rest."""
    s = Store()
    s.dispatch(Upsert("outcome", "o1", {"title": "t", "status": "Backlog", "owner": "roman"}))
    s.dispatch(Upsert("outcome", "o1", {"status": "Doing"}))
    e = s.snapshot().get("outcome", "o1")
    assert e.get("status") == "Doing"
    assert e.get("title") == "t" and e.get("owner") == "roman"


def test_kinds_do_not_collide_on_a_shared_id():
    s = Store()
    s.dispatch(Upsert("outcome", "x", {"a": 1}), Upsert("solution", "x", {"a": 2}))
    assert s.snapshot().get("outcome", "x").get("a") == 1
    assert s.snapshot().get("solution", "x").get("a") == 2


def test_removing_an_absent_entity_is_not_an_error():
    """A delta for something already gone is a race, not a bug."""
    s = Store()
    s.dispatch(Remove("outcome", "never-existed"))
    assert len(s.snapshot()) == 0


def test_replace_kind_drops_what_is_no_longer_there():
    """A rescan that only upserts leaves deleted rows visible forever — the
    kind of staleness nobody notices until someone acts on the stale row."""
    s = Store()
    s.dispatch(Upsert("message", "m1", {}), Upsert("message", "m2", {}))
    s.dispatch(ReplaceKind("message", (("m2", {}), ("m3", {}))))
    ids = {e.id for e in s.snapshot().of_kind("message")}
    assert ids == {"m2", "m3"}


def test_replace_kind_leaves_other_kinds_alone():
    s = Store()
    s.dispatch(Upsert("message", "m1", {}), Upsert("outcome", "o1", {}))
    s.dispatch(ReplaceKind("message", ()))
    assert s.snapshot().of_kind("outcome") and not s.snapshot().of_kind("message")


# ---------------------------------------------------------------------------
# derived at ingest


def test_derived_fields_are_computed_once_at_ingest_not_per_read():
    """If this runs per read, the work is back on the render path."""
    calls = {"n": 0}

    def deriver(fields):
        calls["n"] += 1
        return {"slug": str(fields.get("title", "")).lower().replace(" ", "-")}

    s = Store({"outcome": deriver})
    s.dispatch(Upsert("outcome", "o1", {"title": "Hello World"}))
    e = s.snapshot().get("outcome", "o1")
    for _ in range(10):
        assert e.get("slug") == "hello-world"
    assert calls["n"] == 1, "a derived field must not be recomputed on read"


def test_a_deriver_runs_again_when_the_entity_changes():
    calls = {"n": 0}

    def deriver(fields):
        calls["n"] += 1
        return {"n": fields.get("n", 0) * 2}

    s = Store({"k": deriver})
    s.dispatch(Upsert("k", "1", {"n": 2}))
    s.dispatch(Upsert("k", "1", {"n": 3}))
    assert s.snapshot().get("k", "1").get("n") == 6
    assert calls["n"] == 2


def test_a_raising_deriver_does_not_lose_the_entity():
    """A cosmetic computation must not take the console down."""

    def boom(fields):
        raise ValueError("bad")

    s = Store({"k": boom})
    s.dispatch(Upsert("k", "1", {"raw": "kept"}))
    assert s.snapshot().get("k", "1").get("raw") == "kept"


def test_derived_wins_over_raw_on_read():
    """A deriver exists to correct or compute a view of a raw field."""
    s = Store({"k": lambda f: {"title": "derived"}})
    s.dispatch(Upsert("k", "1", {"title": "raw"}))
    assert s.snapshot().get("k", "1").get("title") == "derived"


def test_replace_kind_derives_too():
    s = Store({"k": lambda f: {"up": str(f.get("t", "")).upper()}})
    s.dispatch(ReplaceKind("k", (("1", {"t": "abc"}),)))
    assert s.snapshot().get("k", "1").get("up") == "ABC"


# ---------------------------------------------------------------------------
# versioned snapshots


def test_a_batch_advances_the_version_exactly_once():
    """The Loader debounces a burst into one dispatch; a render sees all of it
    or none — never half."""
    s = Store()
    before = s.version
    s.dispatch(
        Upsert("outcome", "o1", {}), Upsert("outcome", "o2", {}), Upsert("outcome", "o3", {})
    )
    assert s.version == before + 1
    assert len(s.snapshot()) == 3


def test_a_held_snapshot_does_not_change_underneath_a_render():
    """The property the whole design rests on."""
    s = Store()
    s.dispatch(Upsert("outcome", "o1", {"status": "Backlog"}))
    held = s.snapshot()
    s.dispatch(Upsert("outcome", "o1", {"status": "Done"}), Upsert("outcome", "o2", {}))
    assert held.get("outcome", "o1").get("status") == "Backlog"
    assert len(held) == 1
    assert s.snapshot().get("outcome", "o1").get("status") == "Done"


def test_the_version_advances_even_when_nothing_matched():
    """A caller must be able to tell "applied, no effect" from "never ran"."""
    s = Store()
    before = s.version
    s.dispatch(Remove("outcome", "absent"))
    assert s.version == before + 1


def test_an_empty_dispatch_still_produces_a_snapshot():
    s = Store()
    assert isinstance(s.dispatch(), Snapshot)


def test_concurrent_dispatches_do_not_lose_writes():
    """The Loader writes from a background thread while the UI reads."""
    s = Store()

    def writer(start):
        for i in range(start, start + 50):
            s.dispatch(Upsert("k", str(i), {"i": i}))

    threads = [threading.Thread(target=writer, args=(b,)) for b in (0, 100, 200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.snapshot()) == 150
    assert s.version == 150


# ---------------------------------------------------------------------------
# subscriptions


def test_subscribers_are_told_the_new_version():
    seen = []
    s = Store()
    s.subscribe(lambda snap: seen.append(snap.version))
    s.dispatch(Upsert("k", "1", {}))
    s.dispatch(Upsert("k", "2", {}))
    assert seen == [1, 2]


def test_a_subscriber_receives_the_snapshot_not_a_diff():
    """A subscriber that missed batches re-reads and is correct — what makes
    the store disposable."""
    got = {}
    s = Store()
    s.dispatch(Upsert("k", "1", {}))
    s.subscribe(lambda snap: got.update({"n": len(snap)}))
    s.dispatch(Upsert("k", "2", {}))
    assert got["n"] == 2


def test_unsubscribing_stops_the_callbacks():
    seen = []
    s = Store()
    off = s.subscribe(lambda snap: seen.append(snap.version))
    s.dispatch(Upsert("k", "1", {}))
    off()
    s.dispatch(Upsert("k", "2", {}))
    assert seen == [1]


def test_unsubscribing_twice_is_not_an_error():
    s = Store()
    off = s.subscribe(lambda snap: None)
    off()
    off()


def test_one_raising_subscriber_does_not_stop_the_others():
    seen = []
    s = Store()
    s.subscribe(lambda snap: (_ for _ in ()).throw(RuntimeError("bad")))
    s.subscribe(lambda snap: seen.append(snap.version))
    s.dispatch(Upsert("k", "1", {}))
    assert seen == [1], "a broken subscriber must not silence the rest"


def test_a_subscriber_may_dispatch_without_deadlocking():
    """An optimistic write reconciling from inside a notify would deadlock if
    notification held the write lock."""
    s = Store()
    done = []

    def reconcile(snap):
        if snap.version == 1:
            s.dispatch(Upsert("k", "echo", {}))
            done.append(True)

    s.subscribe(reconcile)
    s.dispatch(Upsert("k", "1", {}))
    assert done == [True] and s.snapshot().get("k", "echo") is not None


def test_a_subscriber_may_unsubscribe_itself_from_inside_the_callback():
    s = Store()
    calls = []

    def once(snap):
        calls.append(snap.version)
        off()

    off = s.subscribe(once)
    s.dispatch(Upsert("k", "1", {}))
    s.dispatch(Upsert("k", "2", {}))
    assert calls == [1]


# ---------------------------------------------------------------------------
# the Textual seam


def test_the_store_does_not_import_textual():
    """D2's deferred seam and plain testability both depend on this."""
    from pathlib import Path

    src = Path("src/otaman_cli/console/store.py").read_text(encoding="utf-8")
    assert "import textual" not in src and "from textual" not in src


def test_marshal_to_app_hands_the_callback_to_the_apps_thread():
    calls = []

    class FakeApp:
        def call_from_thread(self, fn, *args):
            calls.append("marshalled")
            fn(*args)

    got = []
    wrapped = marshal_to_app(FakeApp(), lambda snap: got.append(snap.version))
    wrapped(Snapshot(version=7))
    assert calls == ["marshalled"] and got == [7]


def test_marshalling_survives_an_app_shutting_down():
    """A notify racing teardown is normal, not an error to surface."""

    class DyingApp:
        def call_from_thread(self, fn, *args):
            raise RuntimeError("app is shutting down")

    marshal_to_app(DyingApp(), lambda snap: None)(Snapshot(version=1))


def test_entity_key_is_kind_and_id():
    assert Entity(kind="outcome", id="o1").key == ("outcome", "o1")
