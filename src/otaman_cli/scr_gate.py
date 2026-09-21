"""Resolve the shared SCR template, degrading to "no gate" if it is absent.

The template lives in ``otaman_core.scr_template`` (core #63). cli cannot
express "a core that HAS it" as a dependency: core shipped the module without a
version bump, and no repo pins otaman-core at all — so ``0.3.0`` exists both
with and without it. A version pin would be false assurance about the one skew
that actually occurs.

plugin-agent hit the same gap on their side and solved it at the call site
rather than in packaging; this is their pattern, adopted. The argument that
decided it: our call sites imported bare, so on a laggard bundle
``otaman propose`` and ``otaman send`` would raise ImportError at call time.
**Losing the quality gate is recoverable; losing `propose` is not — and it
fails at the exact moment someone is trying to report a problem.**

The attribute probe also covers what no version expression can: core 0.3.0
WITH the module versus core 0.3.0 WITHOUT it.
"""

from __future__ import annotations

from typing import Any

#: Everything cli calls. Probed together so a partially-updated core — the
#: shape a pin cannot describe — reads as absent rather than failing halfway
#: through a propose.
_REQUIRED = (
    "SECTIONS",
    "SECTION_KEYS",
    "render",
    "validate",
    "is_hollow",
    "completeness",
    "completeness_line",
)


def scr_template() -> Any | None:
    """The shared template module, or None when this install predates it.

    Callers treat None as "no gate": they do their normal work unguarded rather
    than refusing. A missing module means an old core, not a bad request.
    """
    try:
        from otaman_core import scr_template as module
    except Exception:  # noqa: BLE001 - absent/broken core → no gate, never a crash
        return None
    if not all(hasattr(module, name) for name in _REQUIRED):
        return None
    return module


__all__ = ["scr_template"]
