"""console-reactive-store 1.1 — the in-memory store the views render from.

Today every console surface reads the filesystem on its render path, so a lens
switch costs a rescan and an approve-then-return costs seconds. D1 rejects a
cache in front of those reads: invalidation ends up scattered per surface, and
#172 already proved a screen cache can vanish in a refactor without anyone
noticing. Instead the dependency inverts — the store is the only thing views
read, and IO becomes a background concern with exactly one owner (the Loader,
task 1.2).

Four properties this module exists to guarantee:

**Identity-mapped.** One canonical object per `(kind, id)`. The same outcome
reached through the value lens and through a detail view is the same object, so
two surfaces cannot disagree about it.

**Derived at ingest.** A field that costs work to compute is computed once when
the entity enters the store, never per render. Rendering must be a dict lookup.

**Versioned snapshots.** A read takes an immutable snapshot carrying a version.
A batch of changes advances the version exactly once, so no render can observe a
half-applied mutation — the delta that lands mid-render simply belongs to the
next version.

**Subscription notify.** Subscribers learn that a new version exists. They are
told the version, not handed a diff: a subscriber that missed three batches
re-reads the current snapshot and is correct, which is what makes the store
disposable (D2 — crash, restart, full state under a second).

Deliberately free of Textual. The pump is where actions are DISPATCHED (a
background Loader marshals onto the UI thread), not where they live; keeping the
import out means the store is testable without a running app, and a future
daemon could sit behind the same interface (D2's deferred seam).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: `(kind, id)`. Kind is the entity family — "outcome", "solution", "change",
#: "scr", "message", "agent" — and id is unique within it.
EntityKey = tuple[str, str]

#: Computes a kind's derived fields from its raw ones. Runs at ingest, inside
#: the reducer, so a render never pays for it.
Deriver = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class Entity:
    """One canonical object. Frozen: a snapshot handed to a render must not
    change under it, and sharing the same instance across snapshots is what
    makes an unchanged entity free to carry forward."""

    kind: str
    id: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    derived: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> EntityKey:
        return (self.kind, self.id)

    def get(self, name: str, default: Any = None) -> Any:
        """Read a field, derived first — the render-side accessor.

        Derived wins deliberately: a deriver exists to present a corrected or
        computed view of a raw field, so a surface asking for the name gets the
        answer the deriver decided on rather than the raw one it superseded.
        """
        if name in self.derived:
            return self.derived[name]
        return self.fields.get(name, default)


@dataclass(frozen=True)
class Snapshot:
    """An immutable view of every entity at one version."""

    version: int
    entities: Mapping[EntityKey, Entity] = field(default_factory=dict)

    def get(self, kind: str, id: str) -> Entity | None:
        return self.entities.get((kind, id))

    def of_kind(self, kind: str) -> list[Entity]:
        """Every entity of *kind*, in insertion order (dicts preserve it, so a
        projection built from this is stable between renders)."""
        return [e for (k, _), e in self.entities.items() if k == kind]

    def __len__(self) -> int:
        return len(self.entities)


# ---------------------------------------------------------------------------
# Actions — the only way state changes.


@dataclass(frozen=True)
class Upsert:
    """Add or update one entity. Fields MERGE into an existing entity, so a
    delta carrying two changed fields does not erase the rest."""

    kind: str
    id: str
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Remove:
    """Drop one entity. Removing an absent entity is a no-op, not an error: a
    delta for something already gone is a race, not a bug."""

    kind: str
    id: str


@dataclass(frozen=True)
class ReplaceKind:
    """Swap an entire kind — the cold-start bulk scan's action.

    Distinct from a stream of Upserts because it must also REMOVE what is no
    longer there. A rescan that only upserts leaves deleted entities visible
    forever, which is the shape of bug nobody notices until a stale row is
    acted on.
    """

    kind: str
    entities: tuple[tuple[str, Mapping[str, Any]], ...] = ()


Action = Upsert | Remove | ReplaceKind


# ---------------------------------------------------------------------------
# The store.


class Store:
    """Identity-mapped entities behind versioned snapshots.

    Writes are serialized by a lock; reads take none. A reader grabs the current
    snapshot reference (an atomic attribute read) and is then immune to whatever
    lands next — which is what "no render observes a half-applied mutation"
    means concretely.
    """

    def __init__(self, derivers: Mapping[str, Deriver] | None = None) -> None:
        self._snapshot = Snapshot(version=0)
        self._derivers: dict[str, Deriver] = dict(derivers or {})
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[Snapshot], None]] = []

    # -- reads ------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The current snapshot. Never blocks, never partial."""
        return self._snapshot

    @property
    def version(self) -> int:
        return self._snapshot.version

    # -- derivers ---------------------------------------------------------

    def register_deriver(self, kind: str, deriver: Deriver) -> None:
        """Teach the store how to derive *kind*'s computed fields.

        Registered BEFORE ingest by convention: entities already in the store
        are not retroactively re-derived, because doing so silently would make
        a snapshot's contents depend on registration order rather than on the
        actions applied to it.
        """
        self._derivers[kind] = deriver

    def _derive(self, kind: str, fields: Mapping[str, Any]) -> Mapping[str, Any]:
        deriver = self._derivers.get(kind)
        if deriver is None:
            return {}
        try:
            return dict(deriver(fields))
        except Exception:  # noqa: BLE001 - a bad deriver must not poison ingest
            # An entity with no derived fields still renders from its raw ones;
            # losing the whole batch because one deriver raised would take the
            # console down for a cosmetic computation.
            return {}

    # -- writes -----------------------------------------------------------

    def dispatch(self, *actions: Action) -> Snapshot:
        """Apply *actions* as ONE version.

        A batch is atomic on purpose: the Loader debounces a burst of filesystem
        deltas into a single dispatch, and every render either sees all of that
        burst or none of it. Returns the new snapshot; the version advances even
        when nothing matched, so a caller can tell "applied" from "not run".
        """
        flat: list[Action] = []
        for action in actions:
            if isinstance(action, Iterable) and not isinstance(action, (str, bytes)):
                flat.extend(action)  # a list of actions, for ergonomic callers
            else:
                flat.append(action)

        with self._lock:
            entities = dict(self._snapshot.entities)
            for action in flat:
                self._apply(entities, action)
            new = Snapshot(version=self._snapshot.version + 1, entities=entities)
            self._snapshot = new

        self._notify(new)
        return new

    def _apply(self, entities: dict[EntityKey, Entity], action: Action) -> None:
        if isinstance(action, Upsert):
            key = (action.kind, action.id)
            existing = entities.get(key)
            merged = dict(existing.fields) if existing else {}
            merged.update(action.fields)
            entities[key] = Entity(
                kind=action.kind,
                id=action.id,
                fields=merged,
                derived=self._derive(action.kind, merged),
            )
        elif isinstance(action, Remove):
            entities.pop((action.kind, action.id), None)
        elif isinstance(action, ReplaceKind):
            for key in [k for k in entities if k[0] == action.kind]:
                del entities[key]
            for ident, fields in action.entities:
                entities[(action.kind, ident)] = Entity(
                    kind=action.kind,
                    id=ident,
                    fields=dict(fields),
                    derived=self._derive(action.kind, fields),
                )

    # -- subscriptions ----------------------------------------------------

    def subscribe(self, callback: Callable[[Snapshot], None]) -> Callable[[], None]:
        """Register *callback*; returns an unsubscribe callable.

        Subscribers are handed the new SNAPSHOT, not a diff. One that missed
        three batches re-reads and is correct — the property that lets the store
        be rebuilt from scratch without anyone tracking what they missed.
        """
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass  # already gone — unsubscribing twice is not an error

        return unsubscribe

    def _notify(self, snapshot: Snapshot) -> None:
        # Outside the lock: a subscriber that dispatches (an optimistic write
        # reconciling, say) would deadlock against a held write lock. Iterating
        # a copy so a subscriber may unsubscribe itself from inside the call.
        for callback in list(self._subscribers):
            try:
                callback(snapshot)
            except Exception:  # noqa: BLE001 - one bad subscriber is not the others' problem
                continue


# ---------------------------------------------------------------------------
# Textual adapter — kept here so the import stays lazy and the store stays pure.


def marshal_to_app(app: Any, callback: Callable[[Snapshot], None]) -> Callable[[Snapshot], None]:
    """Wrap *callback* so it runs on the app's thread, not the Loader's.

    The Loader watches the filesystem on a background thread; Textual widgets
    may only be touched from the app's own loop. `call_from_thread` is the pump
    handoff — this is the whole of the store's relationship with Textual, and it
    lives behind a function so nothing in the store imports it.
    """

    def marshalled(snapshot: Snapshot) -> None:
        try:
            app.call_from_thread(callback, snapshot)
            return
        except Exception:  # noqa: BLE001 - see below; fall through to a direct call
            pass
        # `call_from_thread` REFUSES to run on the app's own thread, so a
        # dispatch made from the UI — an optimistic write (2.2), or a test
        # driving the store directly — raised here and the repaint was silently
        # dropped. Swallowing that was the bug: the store had the new version
        # and the screen never heard about it.
        #
        # Already on the app's thread is exactly when calling directly is safe,
        # so do that. If THAT also fails the app is going away mid-notify, which
        # is normal during teardown and nothing to surface.
        try:
            callback(snapshot)
        except Exception:  # noqa: BLE001 - teardown race
            return

    return marshalled


__all__ = [
    "Action",
    "Deriver",
    "Entity",
    "EntityKey",
    "Remove",
    "ReplaceKind",
    "Snapshot",
    "Store",
    "Upsert",
    "marshal_to_app",
]
