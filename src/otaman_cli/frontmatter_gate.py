"""Resolve core's bus-frontmatter parser, with a transitional local fallback.

Same skew and same shape as :mod:`otaman_cli.bus_stem_gate`, and for the same
reason: frontmatter parsing backs `otaman check` and the console's read
surfaces, so refusing on a laggard core would take out a primary verb. It falls
back, in ONE place, and the fallback is held to core's behaviour by test.

What this module deliberately does NOT claim is that every frontmatter-shaped
parser in cli belongs here. Two stay local, because they are answering different
questions:

* ``models_report.parse_frontmatter`` reads AGENT DEFINITION files, not bus
  messages, and wants five scalar fields as strings. core's parser is documented
  for bus messages and YAML-types its values; routing it here would change both
  the domain and the types for no gain.
* ``console.bus._frontmatter_head`` reads a BOUNDED head (8KB) rather than the
  whole file. That bound is why the console scan over ~3000 messages dropped
  from 7-9s; it hands raw YAML text to the cheap tier of the two-tier index,
  which may only SKIP a row, never build one. Feeding it through a full-text
  typed parse would undo the measurement it was built from.

Single-home means one implementation of one decision, not one function for every
superficially similar string operation.
"""

from __future__ import annotations

import re
from typing import Any

_REQUIRED = ("parse", "cc_recipients", "is_cc_copy")
_FENCE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


class _Fallback:
    """Byte-compatible stand-in for `otaman_core.frontmatter` on a laggard core.

    Mirrors core's contract exactly, including the part that is easy to get
    wrong: a frontmatterless or non-mapping file yields ``({}, text)``, NOT
    ``(None, text)``. A read surface iterating a bus directory has to skip a
    non-message, not crash on one.
    """

    @staticmethod
    def parse(text: str) -> tuple[dict[str, Any], str]:
        import yaml

        match = _FENCE.match(text or "")
        if not match:
            return {}, text or ""
        try:
            parsed = yaml.safe_load(match.group(1) or "")
        except yaml.YAMLError:
            return {}, text or ""
        if not isinstance(parsed, dict):
            return {}, text or ""
        return parsed, match.group(2) or ""

    @staticmethod
    def cc_recipients(fm: dict[str, Any]) -> list[str]:
        value = (fm or {}).get("cc")
        if value is None:
            return []
        if isinstance(value, str):
            name = value.strip()
            return [name] if name else []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def is_cc_copy(fm: dict[str, Any]) -> bool:
        value = (fm or {}).get("x-cc")
        if value is True:
            return True
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return False


FALLBACK = _Fallback()


def frontmatter() -> Any:
    """Core's module, or the transitional shim — never None."""
    try:
        from otaman_core import frontmatter as module
    except Exception:  # noqa: BLE001 - absent/broken core → transitional shim
        return FALLBACK
    if not all(hasattr(module, name) for name in _REQUIRED):
        return FALLBACK
    return module


def is_core_backed() -> bool:
    """True when the real core module resolved — for the adoption test only."""
    return frontmatter() is not FALLBACK


__all__ = ["FALLBACK", "frontmatter", "is_core_backed"]
