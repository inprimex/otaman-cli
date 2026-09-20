"""Deprecated shim — the SCR template now lives in ``otaman_core.scr_template``.

1.1 put the ONE template here, and 1.2 needed the plugin's MCP propose path to
consume the same source. It could not: `otaman-cli` DEPENDS ON `otaman-plugin`,
so plugin importing cli would have been circular. plugin-agent proved it with a
real wheel install rather than by reading pyproject — and noted that their
pytest pythonpath lists `../otaman-cli/src`, so the import would have passed
their whole suite and CI, then failed on every real install. Green tests,
broken product.

otaman-core is the home both repos already depend on, and where the other
shared pure-logic modules live (`validate_message`, `policy`, `identity`,
`spec_gate`). core hosts it as of core #63, API identical.

This module re-exports for ONE release so nothing breaks mid-flight. New code
should import from ``otaman_core.scr_template`` directly. Delete this once the
usage signal is clean — never in the same release as the move (D8).
"""

from __future__ import annotations

from otaman_core.scr_template import (
    EVIDENCE_LEVELS,
    SECTION_KEYS,
    SECTIONS,
    Section,
    completeness,
    completeness_line,
    declared_evidence_level,
    has_template,
    is_hollow,
    render,
    unfilled_sections,
    validate,
    validate_evidence_level,
)

__all__ = [
    "EVIDENCE_LEVELS",
    "SECTIONS",
    "SECTION_KEYS",
    "Section",
    "completeness",
    "completeness_line",
    "declared_evidence_level",
    "has_template",
    "is_hollow",
    "render",
    "unfilled_sections",
    "validate",
    "validate_evidence_level",
]
