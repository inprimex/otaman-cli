"""Path discovery + round-trip YAML I/O for registry files.

The registry HOME is configuration, not convention (team-mode Phase A 1.1):
1. ``OTAMAN_STRATEGY_DIR`` env var if set (test + scripted override)
2. ``program.registries.strategy_repo`` — a repo slug resolved against
   ``platform.yaml`` ``repos[]``
3. otherwise None — no silent fallback (the old ``find_business_repo``
   cpo-agent/main-agent owner-scan is retired; a missing key is a doctor error)

YAML I/O uses ruamel.yaml round-trip mode so existing comments and
formatting survive mutations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from otaman_cli.registries.platform_ext import (
    ProgramExtensions,
    load_program_extensions,
)

# Shared ruamel.yaml instance; round-trip preserves comments + key order.
_YAML = YAML()
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)


def yaml_load(path: Path) -> Any:
    """Round-trip load a YAML file. Returns the parsed document or {}.

    Empty / non-existent files return an empty dict so caller can populate
    fresh entries without special-casing.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    return _YAML.load(text)


def yaml_dump(data: Any, path: Path) -> None:
    """Round-trip write *data* back to *path* preserving format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        _YAML.dump(data, f)


def _platform_yaml_path(root: Path) -> Path:
    return root / "platform.yaml"


def _read_repos(root: Path) -> list[dict[str, Any]]:
    """Read raw ``repos:`` list from ``platform.yaml``.

    Returns [] if the file is missing or has no ``repos:`` key.
    """
    cfg_path = _platform_yaml_path(root)
    if not cfg_path.is_file():
        return []
    raw = yaml_load(cfg_path) or {}
    repos = raw.get("repos") or []
    return [r for r in repos if isinstance(r, dict)]


def _resolve_repo_by_name(root: Path, name: str) -> Path | None:
    """Absolute path of the ``repos[]`` entry named *name*, or None."""
    for r in _read_repos(root):
        if r.get("name") == name:
            rel = r.get("path") or ""
            if rel:
                # path is relative to platform.yaml's dir (otaman-meta root)
                return (root / rel).expanduser().resolve()
    return None


def strategy_repo(root: Path) -> Path | None:
    """The registry home from ``program.registries.strategy_repo`` (team-mode
    Phase A 1.1 — "roles are hats, repos are homes").

    Resolution: an explicit ``OTAMAN_STRATEGY_DIR`` override (tests/ops), else
    the ``program.registries.strategy_repo`` repo slug resolved against
    ``repos[]``. NO silent convention-based fallback — a registries program that
    hasn't set the key resolves to None (doctor reports it). This RETIRES the old
    find_business_repo owner-scan (cpo-agent/main-agent) discovery.
    """
    env_override = os.environ.get("OTAMAN_STRATEGY_DIR", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()

    cfg = yaml_load(_platform_yaml_path(root)) or {}
    program = cfg.get("program") if isinstance(cfg, dict) else None
    regs = program.get("registries") if isinstance(program, dict) else None
    slug = regs.get("strategy_repo") if isinstance(regs, dict) else None
    if isinstance(slug, str) and slug.strip():
        return _resolve_repo_by_name(root, slug.strip())
    return None


def resolve_registry_path(root: Path, kind: str) -> Path | None:
    """Resolve the absolute path to ``outcomes.yaml`` / ``solutions.yaml`` /
    ``personas.yaml`` for the program rooted at *root*.

    *kind* is one of ``outcomes`` | ``solutions`` | ``personas``. Reads the
    registry HOME from ``program.registries.strategy_repo`` (team-mode 1.1);
    returns None when the key is unset — no fallback.
    """
    if kind not in ("outcomes", "solutions", "personas"):
        raise ValueError(f"unknown registry kind: {kind!r}")

    home = strategy_repo(root)
    if home is None:
        return None

    # Read program extensions to get per-kind path override (defaults to
    # "<kind>.yaml" — see platform_ext defaults)
    try:
        ext = load_program_extensions(_platform_yaml_path(root))
    except Exception:
        ext = ProgramExtensions()

    process_cfg = getattr(ext.processes, kind)
    return home / process_cfg.path


__all__ = [
    "yaml_load",
    "yaml_dump",
    "strategy_repo",
    "resolve_registry_path",
]
