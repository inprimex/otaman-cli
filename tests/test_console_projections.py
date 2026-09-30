"""console-reactive-store 2.1 — lens projections as incremental indexes.

Measured on the live program before this layer existed, per lens switch:

    build_artifact_tree('value')    2773ms
    derive_lifecycle_rows           1468ms
    tree_fallback_notice             558ms

against a 50ms budget. Each ran again on every lens switch, every `:` filter
submission and every refresh, rebuilding data that had not changed.

What must hold: built once per generation, invalidated by a real signal, and
never blocking a keypress on a rebuild.
"""

from __future__ import annotations

import threading

import pytest

from otaman_cli.console.projections import Projections
from otaman_cli.console.store import Store, Upsert


@pytest.fixture
def counted(monkeypatch):
    """A Projections whose builders count their calls."""
    calls = {"tree": 0, "lifecycle": 0, "notice": 0}

    def fake_tree(program, *, show_closed=False, lens="value"):
        calls["tree"] += 1
        return [f"{lens}-root"]

    def fake_rows(program):
        calls["lifecycle"] += 1
        return ["row"]

    def fake_notice(program):
        calls["notice"] += 1
        return None  # a legitimate value, distinct from "not computed"

    monkeypatch.setattr("otaman_cli.console.tree.build_artifact_tree", fake_tree)
    monkeypatch.setattr("otaman_cli.console.lifecycle.derive_lifecycle_rows", fake_rows)
    monkeypatch.setattr("otaman_cli.console.tree.tree_fallback_notice", fake_notice)
    return Projections(program=object()), calls


# ---------------------------------------------------------------------------
# built once


def test_a_lens_is_built_once_and_read_thereafter(counted):
    pr, calls = counted
    for _ in range(10):
        pr.tree("value")
    assert calls["tree"] == 1, "a lens switch must not rebuild from disk"


def test_each_lens_is_cached_separately(counted):
    pr, calls = counted
    assert pr.tree("value") == ["value-root"]
    assert pr.tree("delivery") == ["delivery-root"]
    assert pr.tree("value") == ["value-root"]
    assert calls["tree"] == 2, "two lenses, two builds, no rebuild on return"


def test_show_closed_is_part_of_the_key(counted):
    """`f` toggles it, and the two trees genuinely differ."""
    pr, calls = counted
    pr.tree("value", show_closed=False)
    pr.tree("value", show_closed=True)
    assert calls["tree"] == 2


def test_lifecycle_rows_are_built_once(counted):
    pr, calls = counted
    for _ in range(5):
        pr.lifecycle_rows()
    assert calls["lifecycle"] == 1


def test_a_none_notice_is_cached_rather_than_recomputed(counted):
    """`tree_fallback_notice` returns None legitimately — caching on `is not
    None` would rebuild it forever."""
    pr, calls = counted
    for _ in range(5):
        assert pr.notice() is None
    assert calls["notice"] == 1


def test_the_awaiting_set_is_cached(counted):
    pr, _ = counted
    runs = {"n": 0}

    def derive():
        runs["n"] += 1
        return {"x"}

    for _ in range(4):
        assert pr.awaiting_ids(derive) == {"x"}
    assert runs["n"] == 1


# ---------------------------------------------------------------------------
# invalidated by a real signal


def test_invalidate_drops_everything(counted):
    pr, calls = counted
    pr.tree("value")
    pr.lifecycle_rows()
    pr.notice()
    pr.invalidate()
    pr.tree("value")
    pr.lifecycle_rows()
    pr.notice()
    assert calls == {"tree": 2, "lifecycle": 2, "notice": 2}


def test_invalidate_advances_the_generation(counted):
    pr, _ = counted
    before = pr.generation
    pr.invalidate()
    assert pr.generation == before + 1


def test_a_store_delta_invalidates_the_projections(counted):
    """An approval landing elsewhere changes what awaits the human, and the
    marker beside a row has to move with it."""
    pr, calls = counted
    store = Store()
    pr.bind(store)
    pr.tree("value")
    store.dispatch(Upsert("message", "m1", {}))
    pr.tree("value")
    assert calls["tree"] == 2


def test_unbinding_stops_the_invalidation(counted):
    pr, calls = counted
    store = Store()
    pr.bind(store)
    pr.unbind()
    pr.tree("value")
    store.dispatch(Upsert("message", "m1", {}))
    pr.tree("value")
    assert calls["tree"] == 1


def test_unbinding_twice_is_not_an_error(counted):
    pr, _ = counted
    pr.bind(Store())
    pr.unbind()
    pr.unbind()


# ---------------------------------------------------------------------------
# concurrency and warming


def test_concurrent_reads_do_not_duplicate_the_cache(counted):
    """The Loader's thread may rebuild while the UI reads."""
    pr, calls = counted

    def read():
        for _ in range(20):
            pr.tree("value")

    threads = [threading.Thread(target=read) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert pr.tree("value") == ["value-root"]


def test_warm_builds_everything_ahead_of_the_first_render(counted):
    """Warming happens off the UI thread; a keypress must never be the thing
    that triggers a 2.7s rebuild."""
    pr, calls = counted
    pr.warm(("value", "delivery"))
    assert calls == {"tree": 2, "lifecycle": 1, "notice": 1}
    pr.tree("value")
    pr.tree("delivery")
    pr.lifecycle_rows()
    assert calls == {"tree": 2, "lifecycle": 1, "notice": 1}, "warming must fill the cache"


def test_the_known_gap_is_stated():
    """A specs edit with no bus traffic needs `r` until the fswatch provider
    lands. An unstated gap reads as coverage."""
    doc = (
        Projections.__module__
        and __import__("otaman_cli.console.projections", fromlist=["x"]).__doc__
    )
    doc = " ".join((doc or "").split())
    assert "KNOWN GAP" in doc
    assert "fswatch" in doc and "tasks.md" in doc
