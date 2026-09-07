"""Console adapter over the shared lifecycle derivation (IHC 1.3 / SLE 2.1 D3).

The derivation now lives in :mod:`otaman_cli.lifecycle` — one derivation, two
renderers (this console view + ``otaman spec status`` / doctor). This module only
resolves the program's specs-changes dir + bus dir and delegates; the console
``LifecycleScreen`` renders the returned rows.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from otaman_cli.console.bus import Program
from otaman_cli.lifecycle import (
    APPROVED_UNAUTHORED,
    COMPLETE_UNARCHIVED,
    IN_FLIGHT,
    LifecycleRow,
    derive_lifecycle,
)


def _specs_changes_dir(program: Program) -> Path | None:
    """The specs repo's ``openspec/changes`` dir (from platform.yaml specs.path)."""
    import yaml

    try:
        cfg = yaml.safe_load((program.root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    specs = cfg.get("specs") if isinstance(cfg, dict) else None
    path = specs.get("path") if isinstance(specs, dict) else None
    if not path:
        return None
    changes = (program.root / path / "openspec" / "changes").resolve()
    return changes if changes.is_dir() else None


def list_lifecycle_states(program: Program, *, now: datetime | None = None) -> list[LifecycleRow]:
    """Catchable lifecycle states for *program*, derived from bus + specs repo."""
    active_dir, _ = program.bus_paths()
    return derive_lifecycle(
        changes_dir=_specs_changes_dir(program),
        bus_active_dir=active_dir if active_dir.is_dir() else None,
        now=now,
    )


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "LifecycleRow",
    "list_lifecycle_states",
]
