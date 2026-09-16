"""One parsed pass over the bus, shared by every reader (console perf).

Home was scanning the active bus directory THREE times per render — the inbox
badge, the human decision queue, and the pending-proposal count each globbed
all ~5500 message files, read each one's head, and parsed its frontmatter
independently. Two of those passes were pure duplication, and every one of them
re-did the same work on the next render.

Measured on the live bus (5473 active messages), one full pass costs:

    glob + sort                71 ms
    read a bounded head each  178 ms   <- the I/O floor
    frontmatter regex          43 ms
    YAML-parse every head     728 ms   <- the actual cost
    flat `key: value` parse     22 ms   (5471 of 5473 parse flat)

So the scan is dominated by YAML-parsing thousands of headers to decide that
almost none of them are interesting. This module therefore reads in TWO tiers:

* **tags** — a flat ``key: value`` parse of the frontmatter, 33x cheaper than
  YAML, used only to DECIDE whether a message is interesting.
* **fm** — the authoritative YAML parse, done lazily and memoized, only for the
  messages a caller actually keeps (~735 of 5473 here).

The tiering is not cosmetic. YAML types values: 5451 of these timestamps become
``datetime`` objects whose ``str()`` is ``2026-05-20 08:41:22+00:00``, while 17
stay strings like ``2026-05-20T08:41:22Z``. Building rows from flat strings
would quietly change both the timestamps on screen and the sort order that
depends on them, so the flat parse is never allowed to produce a row — only to
skip one.

Caching is keyed on ``(path, mtime_ns, size)``. Bus messages are effectively
append-only — a message is written once and acknowledged by a SEPARATE ack file
— so a second visit to a screen is nearly free, while an edited or new message
re-parses because its key changed.

This module makes NO judgement about which messages matter: what to show is an
IA decision, not a caching one. Callers keep their own filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Cheap-tier values that mean "true" (YAML would type these as booleans).
_TRUE = frozenset({"true", "yes", "on", "1"})


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def flat_tags(text: str) -> dict[str, str] | None:
    """A flat ``key: value`` parse of frontmatter, or None if it isn't flat.

    Returns None the moment anything needs a real YAML parser — an indented
    line, a block scalar, a key-less line — so the caller falls back rather
    than guessing. Bus frontmatter is machine-written and flat in practice
    (5471 of 5473 on the live bus).
    """
    out: dict[str, str] = {}
    for line in text.split("\n"):
        if not line.strip():
            continue
        if line[0] in " \t-#":  # indented, list item, or comment → not flat
            return None
        key, sep, value = line.partition(":")
        if not sep:
            return None
        out[key.strip()] = _unquote(value.strip())
    return out


@dataclass
class BusEntry:
    """One active bus message, read in two tiers.

    ``tags`` are cheap strings for filtering; ``fm`` is the authoritative
    parse, produced on first access and never used to decide what to skip.
    """

    path: Path
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return self.path.stem

    def tag(self, name: str) -> str:
        return self.tags.get(name, "")

    def flag(self, name: str) -> bool:
        """A boolean frontmatter flag (e.g. ``x-cc``) from the cheap tier."""
        return self.tags.get(name, "").lower() in _TRUE

    @property
    def fm(self) -> dict:
        """The authoritative YAML frontmatter. Parsed on demand, memoized.

        Returns ``{}`` when the frontmatter is absent or unparseable — display
        paths degrade rather than raising into the TUI.
        """
        return _authoritative(self.path)


#: (path, mtime_ns, size) -> cheap tags, or None when there is no frontmatter
_tag_cache: dict[tuple[str, int, int], dict[str, str] | None] = {}
#: (path, mtime_ns, size) -> authoritative parsed frontmatter
_fm_cache: dict[tuple[str, int, int], dict] = {}

#: Bounded so a long session over a growing bus cannot grow without limit,
#: sized well above a large active dir so the common case never evicts.
_MAX_ENTRIES = 20000


def _key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size)


def _store(cache: dict, key, value):
    if len(cache) >= _MAX_ENTRIES:
        # Plain FIFO: a render reads a stable working set, so tracking recency
        # would cost more than it saves.
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def _head_text(path: Path) -> str | None:
    from otaman_cli.console.bus import _frontmatter_head

    return _frontmatter_head(path)


def _tags(path: Path) -> dict[str, str] | None:
    """Cheap tags for *path*, memoized. None when there is no frontmatter."""
    key = _key(path)
    if key is None:
        return None
    if key in _tag_cache:
        return _tag_cache[key]
    text = _head_text(path)
    if text is None:
        return _store(_tag_cache, key, None)
    tags = flat_tags(text)
    if tags is None:
        # Not flat — fall back to the real parser for the tags too, so a
        # non-flat message is never silently excluded from a listing.
        fm = _authoritative(path)
        tags = {k: str(v) for k, v in fm.items()} if fm else {}
    return _store(_tag_cache, key, tags)


def _authoritative(path: Path) -> dict:
    """The YAML-parsed frontmatter for *path*, memoized."""
    key = _key(path)
    if key is None:
        return {}
    if key in _fm_cache:
        return _fm_cache[key]
    from otaman_cli.yaml_fast import fast_parse

    text = _head_text(path)
    fm: dict = {}
    if text is not None:
        try:
            parsed = fast_parse(text)
        except Exception:  # noqa: BLE001 - malformed → skipped, never fatal
            parsed = None
        if isinstance(parsed, dict):
            fm = parsed
    return _store(_fm_cache, key, fm)


def active_entries(program) -> list[BusEntry]:
    """Every active bus message with frontmatter, sorted by filename.

    Filenames are timestamp-prefixed, so this is the same order the callers'
    old ``sorted(active_dir.glob("*.md"))`` produced — no caller's ordering
    changes.
    """
    active_dir, _ = program.bus_paths()
    if not active_dir.is_dir():
        return []
    out: list[BusEntry] = []
    for f in sorted(active_dir.glob("*.md")):
        tags = _tags(f)
        if tags is not None:
            out.append(BusEntry(path=f, tags=tags))
    return out


def clear_cache() -> None:
    """Drop every memoized read. Called by the console's refresh path: `r`
    means "read the world again", and a surviving cache would make it a lie."""
    _tag_cache.clear()
    _fm_cache.clear()


def cache_size() -> tuple[int, int]:
    """``(cheap tags, authoritative parses)`` held — for tests/diagnostics."""
    return len(_tag_cache), len(_fm_cache)


__all__ = [
    "BusEntry",
    "active_entries",
    "cache_size",
    "clear_cache",
    "flat_tags",
]
