"""Resolve core's bus-stem writer/parser, with a transitional local fallback.

Same skew as `scr_gate` and `blocked_gate`: core shipped `bus_stem` in #70
without a version bump and no repo pins otaman-core, so `0.3.0` exists both with
and without it and a version expression cannot tell them apart. Probe the
attributes — canon for every shared-logic-single-home task.

Where this differs from BOTH earlier gates, and why it needs a third shape:

* `scr_gate` degrades to "no gate" — losing the SCR refusal still leaves
  `propose` able to file.
* `blocked_gate` refuses — without a parser there is no reading of blocked
  entries at all, so there is no reduced-but-useful mode.
* Neither works here. Stem construction backs `otaman send`, `notify-change`
  and the console decision audit. Refusing would take out the fleet's primary
  verb on a laggard bundle, which the degradation rule forbids outright; and
  there is no "reduced" stem — a message either gets a filename or it is not
  written.

So this falls back rather than refusing, and the fallback is deliberately in ONE
place. The alternative — leaving the four hand-built f-strings at their call
sites as the fallback path — would keep exactly the drift 1.3 exists to remove.
One shim, one home per repo, and :data:`FALLBACK` is what gets deleted the day
otaman-core is pinned.

The obvious objection to a fallback is that it is a second implementation that
can drift from core's. That is answered by test, not by assertion:
``tests/test_bus_stem_adoption.py`` runs both through the same input matrix and
requires identical output, so a divergence fails the suite rather than silently
renaming files.
"""

from __future__ import annotations

import re
from typing import Any

#: Everything the call sites use, probed together so a partially-updated core
#: reads as absent rather than failing at one of four writers.
_REQUIRED = (
    "build_stem",
    "build_filename",
    "slugify",
    "parse_stem",
    "timestamp_of",
    "ParsedStem",
)

_TS = re.compile(r"^(\d{8}T\d{6})")


class _Fallback:
    """Byte-compatible stand-in for `otaman_core.bus_stem` on a laggard core.

    Mirrors core's implementation exactly, including the details that are easy
    to get subtly wrong and that the local call sites DID get wrong before 1.3:
    strip before truncating (truncating first can leave a trailing hyphen), and
    a fallback token so a slug that reduces to nothing never yields a stem
    ending in a bare `-`.
    """

    @staticmethod
    def slugify(text: str, *, max_len: int | None = None, fallback: str = "item") -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
        if max_len is not None and len(slug) > max_len:
            slug = slug[:max_len].strip("-")
        return slug or fallback

    @staticmethod
    def build_stem(*, timestamp: str, sender: str, recipient: str, slug: str) -> str:
        safe = lambda s: str(s).replace("/", "-")  # noqa: E731 - mirrors core's _safe
        return f"{timestamp}-{safe(sender)}-to-{safe(recipient)}-{slug}"

    @classmethod
    def build_filename(cls, **kw: Any) -> str:
        return cls.build_stem(**kw) + ".md"

    @staticmethod
    def timestamp_of(name: str) -> str:
        m = _TS.match(str(name))
        return m.group(1) if m else ""


FALLBACK = _Fallback()


def bus_stem() -> Any:
    """Core's module, or the transitional shim — never None.

    Returning the shim instead of None is the point: every call site can write a
    stem unconditionally, so no writer needs its own laggard-core branch.
    """
    try:
        from otaman_core import bus_stem as module
    except Exception:  # noqa: BLE001 - absent/broken core → transitional shim
        return FALLBACK
    if not all(hasattr(module, name) for name in _REQUIRED):
        return FALLBACK
    return module


def is_core_backed() -> bool:
    """True when the real core module resolved — for the adoption test only."""
    return bus_stem() is not FALLBACK


__all__ = ["FALLBACK", "bus_stem", "is_core_backed"]
