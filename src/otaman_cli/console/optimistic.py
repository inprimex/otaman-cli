"""console-reactive-store 2.2 — writes apply their expected delta at once.

Approving from a detail view used to cost seconds: the write ran, then the list
re-scanned the bus to discover what the write had just done. The store already
knows — an approved proposal is no longer pending — so the delta is applied
immediately and the list repaints from memory. The rescan-after-write is gone.

What makes that safe rather than merely fast is the reconciliation. An optimistic
state is a PREDICTION, and a prediction that quietly corrects itself is worse
than one that was never made: the human saw the row vanish, believed the
approval landed, and moved on. So when the watcher's echo disagrees, the
divergence renders loudly and is journalled — never a quiet snap-back (D4, and
the no-silent-success class it belongs to).

The prediction is deliberately narrow. A decided item leaves the pending queue;
that is the only thing predicted here, because it is the only thing the console
can know without re-deriving what it just avoided re-deriving. Anything richer
would be guessing at file-level outcomes, which is what the echo is for.

Ordering is sound by construction: the write completes before the expectation is
recorded, so any FULL rescan landing afterwards has already seen the written
file. A row still present in such a rescan has genuinely not been decided — the
write silently failed, or something re-created it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Expectation:
    """One prediction, and the version it was made at."""

    kind: str
    id: str
    action: str
    version: int
    #: What the row said, so a divergence can name it rather than an opaque id.
    label: str = ""


@dataclass(frozen=True)
class Divergence:
    """The echo disagreed with a prediction."""

    expectation: Expectation
    observed_version: int

    @property
    def message(self) -> str:
        what = self.expectation.label or self.expectation.id
        return (
            f"{self.expectation.action} of {what!r} did NOT take: the item is still "
            "pending after the watcher re-read it. Nothing was silently undone — "
            "re-check it and retry."
        )


class OptimisticWrites:
    """Applies expected deltas and reconciles them against the watcher's echo.

    Deliberately not a queue of retries. It predicts, then tells the truth about
    whether the prediction held; deciding what to do about a failed write is the
    human's, because the console cannot know why it failed.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._pending: list[Expectation] = []

    @property
    def pending(self) -> list[Expectation]:
        return list(self._pending)

    def expect_decided(self, kind: str, id: str, action: str, *, label: str = "") -> None:
        """Apply the delta for a decided item: it leaves the pending queue.

        The Remove goes through the store like any other action, so every
        subscriber repaints from it — the detail screen, the list behind it, and
        the awaiting count — without any of them re-reading anything.
        """
        from otaman_cli.console.store import Remove

        snapshot = self._store.dispatch(Remove(kind, id))
        self._pending.append(
            Expectation(kind=kind, id=id, action=action, version=snapshot.version, label=label)
        )

    def reconcile(self, snapshot: Any) -> list[Divergence]:
        """Check outstanding predictions against *snapshot*; return divergences.

        Only snapshots NEWER than the prediction are evidence: the version the
        prediction itself produced obviously agrees with it, and an older one
        was taken before the write. A prediction that holds is dropped silently
        — that is the expected case, and announcing it would train the reader to
        ignore the announcements that matter.
        """
        if not self._pending:
            return []
        diverged: list[Divergence] = []
        still_pending: list[Expectation] = []
        for expectation in self._pending:
            if snapshot.version <= expectation.version:
                still_pending.append(expectation)  # no echo yet
                continue
            if snapshot.get(expectation.kind, expectation.id) is not None:
                diverged.append(
                    Divergence(expectation=expectation, observed_version=snapshot.version)
                )
            # Either way the prediction has been answered and is no longer
            # outstanding: a divergence is reported once, not on every version.
        self._pending = still_pending
        return diverged

    def forget(self) -> None:
        """Drop outstanding predictions (screen teardown)."""
        self._pending.clear()


def journal_divergence(app: Any, divergence: Divergence) -> None:
    """Record a divergence where the session's other actions are recorded.

    A loud notification the human dismisses leaves no trace; the journal is what
    makes "the approval did not take" answerable an hour later.
    """
    log = getattr(app, "session_log", None)
    if log is None:
        return
    try:
        # The same `session_log` and `event` shape `run_decision_action` writes,
        # so a divergence sits beside the action it contradicts rather than in a
        # separate record nobody correlates.
        log.event(
            "action-diverged",
            action=divergence.expectation.action,
            target=divergence.expectation.id,
            ok=False,
            message=divergence.message,
            predicted_at_version=divergence.expectation.version,
            observed_at_version=divergence.observed_version,
        )
    except Exception:  # noqa: BLE001 - a journal that cannot write must not eat the warning
        return


__all__ = ["Divergence", "Expectation", "OptimisticWrites", "journal_divergence"]
