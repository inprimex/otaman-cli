"""console-reactive-store 2.2 — optimistic writes and their reconciliation.

Approving used to cost seconds because the list re-scanned the bus to discover
what the write had just done. The store already knows, so the delta is applied
at once and the rescan is gone.

What makes that safe is the reconciliation. An optimistic state is a PREDICTION,
and a prediction that quietly corrects itself is worse than one never made: the
human watched the row vanish, believed the approval landed, and moved on. So the
tests below are weighted toward divergence being loud, recorded, and reported
exactly once.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.console.optimistic import (
    Divergence,
    Expectation,
    OptimisticWrites,
    journal_divergence,
)
from otaman_cli.console.store import ReplaceKind, Store, Upsert

KIND = "message"


def _store_with(*ids):
    store = Store()
    store.dispatch(ReplaceKind(KIND, tuple((i, {"subject": i}) for i in ids)))
    return store


# ---------------------------------------------------------------------------
# the expected delta


def test_a_decided_item_leaves_the_queue_at_once():
    store = _store_with("m1", "m2")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    assert store.snapshot().get(KIND, "m1") is None
    assert store.snapshot().get(KIND, "m2") is not None


def test_the_delta_goes_through_the_store_so_everyone_repaints():
    """Not a screen-local hide: the list, the detail behind it and the awaiting
    count all read the same store."""
    store = _store_with("m1")
    seen = []
    store.subscribe(lambda snap: seen.append(snap.version))
    OptimisticWrites(store).expect_decided(KIND, "m1", "approve")
    assert seen, "subscribers must learn about an optimistic delta"


def test_the_prediction_records_what_the_row_said():
    """A divergence has to name the item, not an opaque stem."""
    store = _store_with("m1")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve", label="Approve the thing")
    assert ow.pending[0].label == "Approve the thing"


# ---------------------------------------------------------------------------
# reconciliation


def test_a_prediction_that_holds_is_silent():
    """The expected case. Announcing it would train the reader to ignore the
    announcements that matter."""
    store = _store_with("m1", "m2")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    echo = store.dispatch(ReplaceKind(KIND, (("m2", {}),)))  # the watcher agrees
    assert ow.reconcile(echo) == []
    assert ow.pending == []


def test_a_row_still_pending_after_the_echo_is_a_divergence():
    """THE case this exists for: the write did not take, and the human already
    watched the row disappear."""
    store = _store_with("m1")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve", label="the approval")
    echo = store.dispatch(ReplaceKind(KIND, (("m1", {}),)))  # it came back
    diverged = ow.reconcile(echo)
    assert len(diverged) == 1
    assert "did NOT take" in diverged[0].message
    assert "the approval" in diverged[0].message


def test_the_version_that_made_the_prediction_is_not_evidence():
    """It trivially agrees with itself — treating it as an echo would report
    every write as confirmed before the watcher had looked."""
    store = _store_with("m1")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    own = store.snapshot()
    assert ow.reconcile(own) == []
    assert ow.pending, "the prediction is still outstanding — no echo yet"


def test_an_older_snapshot_is_not_evidence():
    """A snapshot taken before the write cannot speak to it."""
    store = _store_with("m1")
    stale = store.snapshot()
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    assert ow.reconcile(stale) == []


def test_a_divergence_is_reported_once_not_on_every_version():
    """A toast on every poll tick is a toast nobody reads."""
    store = _store_with("m1")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    echo = store.dispatch(ReplaceKind(KIND, (("m1", {}),)))
    assert len(ow.reconcile(echo)) == 1
    later = store.dispatch(Upsert(KIND, "m9", {}))
    assert ow.reconcile(later) == [], "the divergence must not repeat"


def test_several_predictions_reconcile_independently():
    store = _store_with("m1", "m2", "m3")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    ow.expect_decided(KIND, "m2", "reject")
    echo = store.dispatch(ReplaceKind(KIND, (("m2", {}), ("m3", {}))))
    diverged = ow.reconcile(echo)
    assert [d.expectation.id for d in diverged] == ["m2"]


def test_forget_drops_outstanding_predictions():
    store = _store_with("m1")
    ow = OptimisticWrites(store)
    ow.expect_decided(KIND, "m1", "approve")
    ow.forget()
    assert ow.pending == []


def test_reconciling_with_nothing_outstanding_is_free():
    store = _store_with("m1")
    assert OptimisticWrites(store).reconcile(store.snapshot()) == []


# ---------------------------------------------------------------------------
# the journal


def test_a_divergence_is_journalled_beside_the_action_it_contradicts():
    """A notification the human dismisses leaves no trace; "did my approval
    land?" has to be answerable an hour later."""
    events = []

    class FakeLog:
        def event(self, kind, **fields):
            events.append((kind, fields))

    class FakeApp:
        session_log = FakeLog()

    d = Divergence(
        expectation=Expectation(kind=KIND, id="m1", action="approve", version=3, label="x"),
        observed_version=4,
    )
    journal_divergence(FakeApp(), d)
    assert events and events[0][0] == "action-diverged"
    assert events[0][1]["ok"] is False
    assert events[0][1]["target"] == "m1"


def test_a_missing_session_log_does_not_raise():
    class NoLog:
        session_log = None

    journal_divergence(
        NoLog(),
        Divergence(
            expectation=Expectation(kind=KIND, id="m1", action="approve", version=1),
            observed_version=2,
        ),
    )


def test_a_failing_journal_does_not_eat_the_warning():
    """Observability must never be the thing that crashes — but it also must
    not swallow the divergence it failed to record."""

    class BadLog:
        def event(self, kind, **fields):
            raise OSError("disk full")

    class App:
        session_log = BadLog()

    journal_divergence(
        App(),
        Divergence(
            expectation=Expectation(kind=KIND, id="m1", action="approve", version=1),
            observed_version=2,
        ),
    )


# ---------------------------------------------------------------------------
# the rescan is gone


def test_the_decision_path_no_longer_rescans():
    """2.2's headline: "remove every rescan-after-write"."""
    import inspect

    from otaman_cli.console.app import InboxScreen

    src = inspect.getsource(InboxScreen._after_decision)
    assert "expect_decided" in src
    assert "_load(" not in src, "a rescan-after-write is exactly what this removes"


def test_the_decided_target_is_passed_not_re_derived():
    """gate 6.1 F2: the highlight has already moved by the time this runs, and
    re-deriving the target there cost Roman two real approvals."""
    import inspect

    from otaman_cli.console.app import _DecisionActions

    src = inspect.getsource(_DecisionActions._apply_decision)
    assert "self._after_decision(target)" in src


def test_the_optimistic_module_predicts_only_what_it_can_know():
    """Anything richer than "a decided item leaves the queue" would be guessing
    at file-level outcomes — which is what the echo is for."""
    src = Path(__import__("otaman_cli.console.optimistic", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert "read_text" not in src and "glob" not in src
