"""Resolve the shared blocked-entry parser, refusing clearly when it is absent.

Same skew as `scr_gate`: core shipped the module without a version bump and no
repo pins otaman-core, so a version expression cannot distinguish a core that
HAS `blocked_entries` from one that does not. The adoption pattern is to probe
the attributes, which is now canon for every shared-logic-single-home task.

Where this DIFFERS from `scr_gate`, and why it is not a copy of it:

`scr_template` gates a verb — losing it costs the SCR refusal while `propose`
still files, so degrading to "no gate" is the right answer and the primary verb
survives. `blocked_entries` IS the verb: without a parser there is no reading or
writing of blocked entries at all, so there is no reduced-but-useful mode to
degrade into.

So this refuses with a remedy instead. The rule it honours is the one behind
"never lose a primary verb": what must never happen is a bare ImportError
traceback at the moment someone runs `otaman blocked`. A sentence naming the
cause and the fix is the most a missing parser can offer.
"""

from __future__ import annotations

from typing import Any

#: Everything cli calls, probed together so a partially-updated core reads as
#: absent rather than failing halfway through a tombstone sweep.
_REQUIRED = (
    "parse_entries",
    "render_entry",
    "tombstone",
    "stale_reason",
    "find_by_ref",
    "KIND_DEPENDENCY",
)

REMEDY = (
    "otaman blocked needs a newer otaman-core (it hosts the blocked-entry "
    "parser as of shared-logic-single-home). Update the bundle — "
    "`otaman upgrade` — then retry."
)


def blocked_entries() -> Any | None:
    """The shared parser module, or None when this install predates it."""
    try:
        from otaman_core import blocked_entries as module
    except Exception:  # noqa: BLE001 - absent/broken core → callers refuse cleanly
        return None
    if not all(hasattr(module, name) for name in _REQUIRED):
        return None
    return module


__all__ = ["REMEDY", "blocked_entries"]
