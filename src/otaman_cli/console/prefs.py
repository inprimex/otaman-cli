"""Console preferences persistence (interactive-human-console 5.1 finding #2.3).

The command palette lets the operator pick a theme, but the choice was lost on
exit. Persist console UI preferences (theme, …) in a small values-free JSON at
``~/.otaman/console-prefs.json`` — tenant-scoped like everything else, holding
UI settings only, never a secret.
"""

from __future__ import annotations

import json
from pathlib import Path


def console_prefs_path(home: Path | None = None) -> Path:
    """``~/.otaman/console-prefs.json`` (single indirection; test-isolated)."""
    return (home or Path.home()) / ".otaman" / "console-prefs.json"


def load_prefs(path: Path | None = None) -> dict:
    """Parse the prefs file; ``{}`` on missing/unreadable/non-mapping."""
    p = path or console_prefs_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/corrupt → defaults
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(prefs: dict, path: Path | None = None) -> None:
    """Write *prefs* (values-free UI settings) atomically."""
    p = path or console_prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(prefs, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


__all__ = ["console_prefs_path", "load_prefs", "save_prefs"]
