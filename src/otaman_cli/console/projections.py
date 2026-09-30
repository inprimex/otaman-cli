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

Freshness without a watcher: a read first compares a cheap FINGERPRINT of the
artifact sources — the registry files' (mtime, size) and the changes directory's
own mtime. A specs-repo edit that produces no bus traffic (someone editing
tasks.md directly) therefore still invalidates, which was the gap this layer
opened when it stopped re-reading on every lens switch. The fingerprint is a
handful of stat calls, measured below the noise floor against the 50ms budget,
and it reuses the same (path, mtime, size) idea the YAML read cache already
keys on.

KNOWN LIMIT, stated because an unstated one reads as coverage: a stat-based
fingerprint cannot see an edit that leaves mtime AND size unchanged — the same
blind spot `invalidate_read_caches` exists to cover, and the same reason `r`
remains. A real watcher (1.3, otaman-fswatch) closes it properly.

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
        self._fingerprint: tuple | None = None
        self._paths: list | None = None
        self._details: dict[tuple[str, str], str] = {}
        self._emphasis: Any = _UNSET

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
            self._details.clear()
            self._emphasis = _UNSET

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

    def _sources_fingerprint(self) -> tuple:
        """(mtime, size) of the artifact sources — cheap enough to check per read.

        Deliberately shallow: the registry files themselves plus the changes
        directory's mtime, which moves when a change folder is added or removed.
        Walking every tasks.md would turn a freshness check back into a scan.
        """
        import os

        marks: list[tuple] = []
        for label, path in self._source_paths():
            try:
                st = os.stat(path)
                marks.append((label, st.st_mtime, st.st_size))
            except OSError:
                marks.append((label, None, None))
        return tuple(marks)

    def _source_paths(self) -> list[tuple[str, Any]]:
        """Where the artifact sources live, resolved ONCE.

        Resolving them per check cost 172ms — `strategy_repo` and
        `_specs_changes_dir` each re-parse platform.yaml and re-resolve repos,
        which is three times the whole budget for a job that is supposed to be a
        few stat calls. The locations do not move while the console is open;
        only their contents do, which is what the stats are for.
        """
        if self._paths is not None:
            return self._paths

        paths: list[tuple[str, Any]] = []
        try:
            from otaman_cli.registries.loader import strategy_repo

            home = strategy_repo(self.program.root)
        except Exception:  # noqa: BLE001 - unresolvable home → nothing to fingerprint
            home = None
        if home is not None:
            for name in ("outcomes.yaml", "solutions.yaml", "personas.yaml"):
                paths.append((name, home / name))
        try:
            from otaman_cli.commands.spec import _specs_changes_dir

            changes = _specs_changes_dir(self.program.root)
            if changes is not None:
                paths.append(("changes", changes))
        except Exception:  # noqa: BLE001 - unresolvable specs → skip that mark
            pass
        self._paths = paths
        return paths

    def _check_sources(self) -> None:
        """Invalidate if the artifact sources moved since the last build."""
        current = self._sources_fingerprint()
        with self._lock:
            known = self._fingerprint
        if known is not None and current != known:
            self.invalidate()
        with self._lock:
            self._fingerprint = current

    def tree(self, lens: str, *, show_closed: bool = False) -> list:
        """The artifact tree for *lens*, built once per generation."""
        self._check_sources()
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

    def node_detail(self, kind: str, node_id: str, emphasis: Any = None) -> str:
        """Rendered detail text for one node, built once per generation.

        `_update_panel` called this on every cursor move with the side panel
        open — node_detail_text 101ms + role_emphasis 27ms, per arrow key. The
        text depends only on the registry data, which is what a generation
        tracks, so it is computed once and read thereafter.
        """
        self._check_sources()
        key = (kind, node_id)
        with self._lock:
            cached = self._details.get(key)
        if cached is not None:
            return cached

        from otaman_cli.console.registry_detail import node_detail_text

        if emphasis is None:
            emphasis = self.role_emphasis()
        text = node_detail_text(self.program, kind, node_id, emphasis=emphasis) or ""
        with self._lock:
            self._details[key] = text
        return text

    def role_emphasis(self) -> Any:
        """The acting hat's emphasis, resolved once — it does not change while
        the console is open, and it cost 27ms per cursor move."""
        with self._lock:
            cached = self._emphasis
        if cached is not _UNSET:
            return cached

        from otaman_cli.console.registry_detail import role_emphasis

        value = role_emphasis(self.program)
        with self._lock:
            self._emphasis = value
        return value

    def lifecycle_rows(self) -> list:
        self._check_sources()
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
