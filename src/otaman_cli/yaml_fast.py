"""Fast, memoized YAML reads for read-only display paths.

The console was re-parsing the same files over and over. Measured on the live
program (5526 bus files, 53 changes, 69 capabilities):

    build_home_summary            7818 ms
    build_artifact_tree (value)   7992 ms   (warm: 7603 ms — nothing cached)
    derive_lifecycle_rows         3800 ms

Profiling put 11.5 s of a single tree build inside ``yaml.safe_load``, called
241 times, and 9.4 s inside ``_specs_changes_dir`` alone — called 107 times per
render, each call re-reading and re-parsing the SAME 376-line platform.yaml.
The cost was never the volume of data; it was parsing one small file a hundred
times with the slow parser.

Two independent multipliers, both invisible to the reader:

1. **libyaml is installed and was unused.** ``yaml.safe_load`` runs the
   pure-Python parser at ~32 ms for platform.yaml; ``CSafeLoader`` does the same
   document in ~2.9 ms. 11x, for free, on every parse.
2. **The same path is read many times per render.** Memoizing on
   ``(path, mtime_ns, size)`` collapses those 107 parses into 1.

This module is for READ-ONLY paths. Registry writes keep going through
``registries.loader.yaml_load`` (ruamel round-trip), which preserves comments
and formatting — speed is not worth losing a human's comments on write-back.

Staleness: the key includes mtime and size, so an edited file re-parses on the
next read. Because some filesystems have coarse mtime granularity, the console's
refresh path also calls :func:`clear_cache` explicitly — a human pressing `r`
must never be shown a cached read. Tests that rewrite a fixture in-place should
do the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: The fastest available safe loader. libyaml is present in every environment
#: that installs PyYAML with the C extension; the pure-Python class is the
#: fallback so this module never depends on the build.
_LOADER = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader

#: How many parsed documents to retain. A render touches a handful of distinct
#: files; the bound exists so a long session cannot grow without limit.
_MAX_ENTRIES = 256

#: (resolved path, mtime_ns, size) -> parsed document
_cache: dict[tuple[str, int, int], Any] = {}


def fast_parse(text: str) -> Any:
    """Parse a YAML string with the fastest safe loader available.

    Use for content that is already in memory — message frontmatter, for
    instance, where there are thousands of small documents and no file to key a
    cache on.
    """
    return yaml.load(text, Loader=_LOADER)  # noqa: S506 - safe loader, not full


def load_file(path: Path, default: Any = None) -> Any:
    """Parse the YAML at *path*, memoized on its mtime and size.

    Returns *default* when the file is missing, empty, or unparseable — display
    paths degrade to "no data" rather than raising into the TUI. Failures are
    cached too, so a broken file is not re-read a hundred times per render.

    The returned object is SHARED with every other caller holding the same
    (path, mtime, size). Treat it as read-only: mutate it and you have edited
    what the next reader sees. Callers that need to modify a document must load
    it through ``registries.loader.yaml_load`` instead, which round-trips and is
    the only thing safe to write back anyway.
    """
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return default

    if key in _cache:
        return _cache[key]

    try:
        text = path.read_text(encoding="utf-8")
        data = fast_parse(text) if text.strip() else default
    except (OSError, yaml.YAMLError):
        data = default

    if len(_cache) >= _MAX_ENTRIES:
        # Plain FIFO eviction: a render reads a small, stable working set, so
        # tracking recency would cost more than it saves.
        _cache.pop(next(iter(_cache)))
    _cache[key] = data
    return data


def clear_cache() -> None:
    """Drop every memoized document.

    Called by the console's refresh path: `r` means "read the world again", and
    a cache that survived it would make the key a lie.
    """
    _cache.clear()


def cache_size() -> int:
    """How many documents are memoized (for tests and diagnostics)."""
    return len(_cache)


__all__ = ["cache_size", "clear_cache", "fast_parse", "load_file"]
