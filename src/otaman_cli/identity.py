"""Per-tab agent identity resolution (carved from legacy cli/maestro.py).  # legacy: filename

Resolution priority chain (agent-identity-per-directory spec D1, amended 2026-05-28):

1. ``OTAMAN_AGENT`` environment variable  (highest — automated session spawn)
2. ``.otaman`` ``agent:`` field found by walking up from CWD  (per-repo)
3. ``.agents/current-agent`` file  (deprecated project-global fallback)
4. ``None`` / ERROR — no identity; caller must prompt user

The CWD ancestry walk (step 2) starts at the current working directory and
walks up parent directories.  A ``.otaman`` file WITHOUT an ``agent:`` field
does NOT stop the walk — the walker continues up until an ``agent:`` value is
found or the filesystem root is reached.

Step 3 emits a DEPRECATED warning to stderr when it is the source.  Step 4
returns ``None``; callers that can't continue without an identity should exit
with an instructive message.

``~/.otaman-session`` is no longer read.  It was user-global state (not
session-local) and caused concurrent-session identity collisions (2026-05-28
watchtower incident).  Per-repo ``.otaman agent:`` fields written by
``otaman init`` replace it entirely.

The CWD→platform.yaml→owner fallback (2026-04-29 fix) is preserved within
step 2 for repos not yet updated by ``otaman init --update``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from otaman_core._resolve import find_maestro_root, resolve_worktree_main


def find_project_root(start: Path | None = None) -> Path | None:
    """Find the otaman root directory.

    Resolution chain:
    1. .otaman marker file (or legacy: .maestro) — contains relative path to otaman folder
    2. MAESTRO_ROOT environment variable
    3. Walk-up fallback (legacy: platform.yaml or .agents/ in parent)

    Thin wrapper over otaman_core._resolve.find_maestro_root for clarity at
    call sites that don't otherwise import from otaman_core directly.
    """
    return find_maestro_root(start)


def _read_otaman_agent_field(cwd: Path) -> str | None:
    """Walk up from *cwd* looking for a ``.otaman`` file with an ``agent:`` line.

    A ``.otaman`` without an ``agent:`` field does NOT stop the walk — the
    walker continues to parent directories (spec D1, task 2.2).

    Returns the agent name if found, otherwise ``None``.
    """
    for directory in [cwd, *cwd.parents]:
        marker = directory / ".otaman"
        if marker.is_file():
            try:
                text = marker.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("agent:"):
                    value = stripped[len("agent:"):].strip()
                    if value:
                        return value
            # No agent: field in this .otaman — keep walking up (D1)
    return None


def resolve_agent_identity(
    root: Path,
    cwd: Path | None = None,
    explicit: str | None = None,
) -> str | None:
    """Resolve which agent identity to act as.

    Priority chain (D1, amended 2026-05-28 — no ~/.otaman-session):
    1. explicit arg (CLI --agent / direct call)
    2. OTAMAN_AGENT environment variable
    3. .otaman ``agent:`` field found by CWD ancestry walk
       (falls back to platform.yaml CWD->owner for un-updated repos)
    4. .agents/current-agent (deprecated; emits warning)
    5. None (caller decides whether to error)
    """
    # 1. Explicit argument always wins
    if explicit:
        return explicit

    # 2. OTAMAN_AGENT environment variable
    env_agent = os.environ.get("OTAMAN_AGENT", "").strip()
    if env_agent:
        return env_agent

    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()

    # 3a. .otaman agent: field — CWD ancestry walk (keeps walking past .otaman without agent:)
    dotoman_agent = _read_otaman_agent_field(cwd)
    if dotoman_agent:
        return dotoman_agent

    # 3b. CWD → platform.yaml → owner (backwards compat for repos not yet updated
    #     by `otaman init --update`; same logic as the 2026-04-29 fix)
    try:
        worktree_main = resolve_worktree_main(cwd)
    except Exception:
        worktree_main = None

    platform_yaml = root / "platform.yaml"
    if platform_yaml.is_file():
        data: dict = {}
        try:
            import yaml as _yaml
            data = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        repos = data.get("repos") or []
        if isinstance(repos, list):
            for r in repos:
                if not isinstance(r, dict):
                    continue
                rpath = r.get("path")
                owner = r.get("owner")
                if not rpath or not owner:
                    continue
                try:
                    resolved = (root / rpath).resolve()
                except (OSError, ValueError):
                    continue
                if cwd == resolved or cwd.is_relative_to(resolved):
                    return str(owner).strip()
                if worktree_main is not None and (
                    worktree_main == resolved or worktree_main.is_relative_to(resolved)
                ):
                    return str(owner).strip()

    # 4. .agents/current-agent — deprecated fallback
    agent_file = root / ".agents" / "current-agent"
    if agent_file.is_file():
        try:
            text = agent_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        # Skip deprecation-marker lines written during the transition
        lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        if lines:
            name = lines[0].strip()
            if name:
                print(
                    f"[otaman] DEPRECATED: identity resolved from .agents/current-agent ('{name}'). "
                    "Run 'otaman init --update' to migrate to per-repo .otaman agent: fields, "
                    "or set OTAMAN_AGENT in your launch config.",
                    file=sys.stderr,
                )
                return name

    # 5. Nothing found
    return None
