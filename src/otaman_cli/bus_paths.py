"""Bus path resolution and ack status — the two helpers sixteen call sites share.

Extracted from `main.py` (CTO review 2026-09-26). Both were named with a leading
underscore while being imported by name across eighteen sites in two repos' worth of
surfaces; a private name with eighteen external consumers is documentation that is
wrong, not an access control. The names are kept AS WRITTEN so the move stays
mechanical and reviewable — renaming them is a separate, cosmetic change, and two tests
patch `_resolve_bus_paths` by string path.

A leaf: it imports nothing from `otaman_cli` at all.
"""

from __future__ import annotations

from pathlib import Path


def _resolve_bus_paths(root: Path) -> tuple[Path, Path]:
    """Resolve bus active dir and acks dir from project root."""
    try:
        import yaml as _yaml

        config_path = root / "platform.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = _yaml.safe_load(f)
            bus_rel = config.get("communication", {}).get("bus_path", ".agents/bus")
        else:
            bus_rel = ".agents/bus"
    except Exception:
        bus_rel = ".agents/bus"
    active_dir = root / bus_rel / "active"
    acks_dir = active_dir / "acks"
    return active_dir, acks_dir


def _get_agent_ack_status(msg_stem: str, agent: str, acks_dir: Path) -> str:
    """Get ack status for a specific agent+message. Returns 'pending', 'read', or 'resolved'."""
    for status in ("resolved", "read"):
        ack_file = acks_dir / f"{msg_stem}.{agent}.ack"
        if ack_file.exists():
            content = ack_file.read_text(encoding="utf-8").strip()
            if content == status or status in content:
                return status
    # Check if any ack file exists at all
    ack_file = acks_dir / f"{msg_stem}.{agent}.ack"
    if ack_file.exists():
        return ack_file.read_text(encoding="utf-8").strip() or "read"
    return "pending"


__all__ = ["_get_agent_ack_status", "_resolve_bus_paths"]
