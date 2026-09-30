"""console-reactive-store 2.1 — lens projections as incremental indexes.

Measured on the live program before this existed:

    build_artifact_tree('value')   2773ms
    build_artifact_tree('delivery') 1920ms
    derive_lifecycle_rows           1468ms
    tree_fallback_notice             558ms

Every one of those ran again on each lens switch, each `:` filter submission,
and each refresh, because the lens screen rebuilt from disk on every reload.
The spec's budget for a lens switch and a filter is 50ms, so the gap was not
marginal — it was two orders of magnitude, and the whole of it was rebuilding
data that had not changed.

A projection is built ONCE per generation and read by every render after it.
Switching lens, typing a filter, toggling closed rows and returning from a
detail view all read a prebuilt structure; none of them touch the filesystem.
The generation advances when the store does, or when the human explicitly
refreshes — so a delta rebuilds the index rather than a keypress rebuilding it.

KNOWN GAP, stated because an unstated one reads as coverage: the generation
advances on a STORE delta (the bus) and on an explicit `r`. A specs-repo edit
that produces no bus traffic — someone editing tasks.md directly — is not seen
until one of those. Before this layer every lens switch re-read from disk, so
that case refreshed by accident; the trade is deliberate (2773ms per switch
against a 50ms budget), and the escape hatch is one keypress. It closes for
good when the artifact data itself is ingested and the fswatch provider (1.3)
watches the specs tree.

Filtering deliberately stays OUTSIDE the cache. `matches`/`matches_tree` run
over the prebuilt tree in memory, which is microseconds, and caching per query
would key on text the human is still typing — a cache that never hits, holding
every abandoned prefix.
"""

from __future__ import annotations

import threading
from typing import Any

from otaman_cli.console.bus import Program


class Projections:
    """Prebuilt lens structures for one program.

    Thread-safe: the Loader's thread rebuilds while the UI thread reads. A read
    during a rebuild returns the PREVIOUS generation rather than blocking or a
    half-built tree — stale for one frame beats a stalled keypress, and the
    subscription repaints as soon as the new generation lands.
    """

    def __init__(self, program: Program, store: Any | None = None) -> None:
        self.program = program
        self._store = store
        self._lock = threading.Lock()
        self._generation = 0
        self._trees: dict[tuple[str, bool], list] = {}
        self._lifecycle: list | None = None
        self._awaiting: set[str] | None = None
        self._notice: Any = _UNSET
        self._unsubscribe = None

    # -- invalidation -----------------------------------------------------

    def invalidate(self) -> None:
        """Drop every cached projection; the next read rebuilds.

        Called on a store delta and on an explicit refresh. Cheap — it clears
        dicts; the cost is paid by whoever reads next, which is why the caller
        should be a background worker and not a keystroke handler.
        """
        with self._lock:
            self._generation += 1
            self._trees.clear()
            self._lifecycle = None
            self._awaiting = None
            self._notice = _UNSET

    def bind(self, store: Any) -> None:
        """Invalidate whenever *store* advances a version."""
        self._store = store
        self._unsubscribe = store.subscribe(lambda _snapshot: self.invalidate())

    def unbind(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def generation(self) -> int:
        return self._generation

    # -- the projections --------------------------------------------------

    def tree(self, lens: str, *, show_closed: bool = False) -> list:
        """The artifact tree for *lens*, built once per generation."""
        key = (lens, show_closed)
        with self._lock:
            cached = self._trees.get(key)
        if cached is not None:
            return cached

        from otaman_cli.console.tree import build_artifact_tree

        built = build_artifact_tree(self.program, show_closed=show_closed, lens=lens)
        with self._lock:
            self._trees[key] = built
        return built

    def lifecycle_rows(self) -> list:
        with self._lock:
            cached = self._lifecycle
        if cached is not None:
            return cached

        from otaman_cli.console.lifecycle import derive_lifecycle_rows

        rows = derive_lifecycle_rows(self.program)
        with self._lock:
            self._lifecycle = rows
        return rows

    def notice(self) -> Any:
        with self._lock:
            cached = self._notice
        if cached is not _UNSET:
            return cached

        from otaman_cli.console.tree import tree_fallback_notice

        value = tree_fallback_notice(self.program)
        with self._lock:
            self._notice = value
        return value

    def awaiting_ids(self, compute) -> set[str]:
        """Ids awaiting the human. *compute* is the caller's own derivation —
        passed in rather than imported so this module stays free of the screen's own logic
        while still caching its result."""
        with self._lock:
            cached = self._awaiting
        if cached is not None:
            return cached

        value = compute()
        with self._lock:
            self._awaiting = value
        return value

    def warm(self, lenses=(), *, show_closed: bool = False) -> None:
        """Build everything ahead of the first render, off the UI thread."""
        for lens in lenses:
            self.tree(lens, show_closed=show_closed)
        self.lifecycle_rows()
        self.notice()


class _Unset:
    """Distinct from None, which `tree_fallback_notice` legitimately returns."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


_UNSET = _Unset()

__all__ = ["Projections"]
