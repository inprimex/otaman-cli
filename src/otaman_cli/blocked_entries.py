"""Deprecated shim — blocked entries now live in ``otaman_core.blocked_entries``.

The parser moved to core under shared-logic-single-home. It had to: plugin's
`otaman_check` carries its OWN regex for `## Blocked:` — a SIXTH parser, after
blocked-entry-lifecycle consolidated five here — and that one requires a
`**Proposal**:` line, which `awaiting-dependency` entries do not have. Measured
on a file holding one of each, plugin saw 1 of 2 entries: an agent blocked on
another AGENT was invisible through MCP and visible through the CLI, with
blocked-subcommand's spec naming both kinds. plugin cannot import cli (the
circular-dependency bind scr_template hit), so core is the only home both can
reach.

This module re-exports for ONE release so nothing breaks mid-flight. New code
should import from ``otaman_core.blocked_entries``. Removal waits for a clean
usage signal, never the same release as the move (D8).

NOTE on the version-skew pattern: `scr_gate` probes and degrades to "no gate",
because losing the SCR refusal still leaves `propose` able to file. That shape
does not transfer here — this module IS `otaman blocked`'s logic, not a gate on
it, so there is nothing to degrade to. `blocked_gate` therefore probes and
REFUSES with a remedy rather than letting an ImportError traceback out.
"""

from __future__ import annotations

from otaman_core.blocked_entries import (
    KIND_APPROVAL,
    KIND_DEPENDENCY,
    KINDS,
    MALFORMED_TITLE,
    BlockedEntry,
    find_by_ref,
    parse_entries,
    render_entry,
    stale_reason,
    tombstone,
)

__all__ = [
    "KINDS",
    "KIND_APPROVAL",
    "KIND_DEPENDENCY",
    "MALFORMED_TITLE",
    "BlockedEntry",
    "find_by_ref",
    "parse_entries",
    "render_entry",
    "stale_reason",
    "tombstone",
]
