"""Path discovery + round-trip YAML I/O for registry files.

Path resolution order:
1. ``OTAMAN_BUSINESS_DIR`` env var if set (test + scripted override)
2. The repo owned by ``cpo-agent`` (or ``main-agent`` in single-repo mode)
   as declared in ``platform.yaml`` ``repos[]``
3. ``find_project_root() / "outcomes.yaml"`` etc. (cwd fallback for
   simple single-repo programs)

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


def find_business_repo(root: Path) -> Path | None:
    """Locate the program's business repo from ``platform.yaml``.

    Resolution chain:
    1. ``OTAMAN_BUSINESS_DIR`` env var (absolute path; for tests/scripts)
    2. Repo with ``owner: cpo-agent``
    3. Repo with ``owner: main-agent`` (single-repo programs from
       cli-init-smart-entry-point)
    4. ``None`` (caller decides whether to error)
    """
    env_override = os.environ.get("OTAMAN_BUSINESS_DIR", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()

    repos = _read_repos(root)
    for owner_hint in ("cpo-agent", "main-agent"):
        for r in repos:
            if r.get("owner") == owner_hint:
                rel = r.get("path") or ""
                if not rel:
                    continue
                # Path is relative to platform.yaml's directory (otaman-meta root)
                return (root / rel).expanduser().resolve()
    return None


def resolve_registry_path(root: Path, kind: str) -> Path | None:
    """Resolve the absolute path to ``outcomes.yaml`` / ``solutions.yaml`` /
    ``personas.yaml`` for the program rooted at *root*.

    *kind* is one of ``outcomes`` | ``solutions`` | ``personas``.
    """
    if kind not in ("outcomes", "solutions", "personas"):
        raise ValueError(f"unknown registry kind: {kind!r}")

    business = find_business_repo(root)
    if business is None:
        return None

    # Read program extensions to get per-kind path override (defaults to
    # "<kind>.yaml" — see platform_ext defaults)
    try:
        ext = load_program_extensions(_platform_yaml_path(root))
    except Exception:
        ext = ProgramExtensions()

    process_cfg = getattr(ext.processes, kind)
    return business / process_cfg.path


__all__ = [
    "yaml_load",
    "yaml_dump",
    "find_business_repo",
    "resolve_registry_path",
]
