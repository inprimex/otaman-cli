"""Resolve core's changelog-fragment modules, refusing clearly when they are absent.

Same skew as `scr_gate` and `blocked_gate`: core shipped these modules without a
version bump and no repo pins otaman-core, so a version expression cannot tell a
core that HAS them from one that does not. Probe the attributes — canon for every
shared-logic-single-home task.

Both callers here REFUSE on skew rather than degrade, and the reason is worth
stating because it inverts the usual rule:

`scr_template` gates a verb — losing it costs the SCR refusal while `propose`
still files, so degrading to "no gate" keeps the primary verb alive. That rule
does NOT transfer to a MERGE gate. `otaman policy check-changelog` exists only to
block a shipped-code PR that carries no fragment; a version of it that cannot
evaluate and exits 0 anyway reports a green CI check that never ran. A false
green on a merge gate is strictly worse than a red one — the whole point of the
gate is that its silence means "checked and clean".

`release clear-fragments` refuses for the plainer reason: without a manifest
parser there is nothing to read, so there is no reduced-but-useful mode at all.

What must never happen in either case is a bare ImportError traceback at the
moment someone runs the command, which is why this returns None and names a
REMEDY instead of letting the import escape.
"""

from __future__ import annotations

from typing import Any

#: The evaluator surface `policy check-changelog` calls, probed together so a
#: partially-updated core reads as absent rather than failing mid-verdict.
_EVALUATOR_REQUIRED = (
    "evaluate",
    "resolve_fragment_config",
    "fragment_required_by_policy",
    "is_shipped_path",
    "shipped_paths",
    "fragment_paths",
    "has_exemption",
    "FragmentConfig",
    "Verdict",
)

#: The manifest surface `release clear-fragments` calls. Separate from the
#: evaluators because core homed them in two modules: a core with the evaluators
#: but no manifest reader is a real intermediate state (1.1 landed before 1.3).
_MANIFEST_REQUIRED = (
    "from_dict",
    "to_dict",
    "fragment_hash",
    "Manifest",
    "ConsumedFragment",
    "HASH_ALGO",
)

REMEDY = (
    "this needs a newer otaman-core (it hosts the changelog-fragment evaluators "
    "and the consumed-fragments manifest as of release-notes-sibling-coverage). "
    "Update the bundle — `otaman upgrade` — then retry."
)


def changelog_fragment() -> Any | None:
    """Core's evaluator module, or None when this install predates it."""
    try:
        from otaman_core import changelog_fragment as module
    except Exception:  # noqa: BLE001 - absent/broken core → callers decide
        return None
    if not all(hasattr(module, name) for name in _EVALUATOR_REQUIRED):
        return None
    return module


def changelog_manifest() -> Any | None:
    """Core's consumed-fragments manifest module, or None when absent."""
    try:
        from otaman_core import changelog_manifest as module
    except Exception:  # noqa: BLE001 - absent/broken core → callers decide
        return None
    if not all(hasattr(module, name) for name in _MANIFEST_REQUIRED):
        return None
    return module


__all__ = ["REMEDY", "changelog_fragment", "changelog_manifest"]
