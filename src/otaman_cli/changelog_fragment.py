"""Deprecated shim — the evaluators now live in ``otaman_core.changelog_fragment``.

release-notes-fragments 1.2 put the changelog-fragment predicate here, and
release-notes-sibling-coverage needed three more consumers of the same decision:
otaman-deploy's release assembler, every sibling repo's merge-time CI gate, and
plugin's scaffolder. None of them can import otaman-cli — deploy and the siblings
because the dependency runs cli -> core, so importing cli would be circular, and a
sibling's CI has no otaman project to resolve a policy from at all.

otaman-core is the home every one of them already depends on, alongside the other
shared pure-logic modules (`validate_message`, `policy`, `identity`, `scr_template`,
`blocked_entries`). core hosts these as of core #65, API identical — verified by
diff: only the docstrings differ, no logic drift, which is what makes the cli
wrapper and the core-invokable gate give the same verdict.

This module re-exports for ONE release so nothing breaks mid-flight. New code
should import from ``otaman_core.changelog_fragment`` directly, or go through
:mod:`otaman_cli.changelog_gate` when it needs to survive a core that predates
the move. Delete this once the usage signal is clean — never in the same release
as the move (D8).
"""

from __future__ import annotations

from otaman_core.changelog_fragment import (
    FragmentConfig,
    Verdict,
    evaluate,
    fragment_paths,
    fragment_required_by_policy,
    has_exemption,
    is_shipped_path,
    resolve_fragment_config,
    shipped_paths,
)

__all__ = [
    "FragmentConfig",
    "Verdict",
    "evaluate",
    "fragment_paths",
    "fragment_required_by_policy",
    "has_exemption",
    "is_shipped_path",
    "resolve_fragment_config",
    "shipped_paths",
]
