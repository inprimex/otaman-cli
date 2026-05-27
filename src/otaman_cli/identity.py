"""Per-tab agent identity resolution (carved from legacy cli/maestro.py).  # legacy: filename

Resolution priority chain (agent-identity-per-directory spec, D1):

1. ``OTAMAN_AGENT`` environment variable  (highest — automated session spawn)
2. ``~/.otaman-session`` file  (session-local; written by ``otaman set-agent``)
3. ``.otaman`` ``agent:`` field found by walking up from CWD  (per-repo)
4. ``.agents/current-agent`` file  (deprecated project-global fallback)
5. ``None`` / ERROR — no identity; caller must prompt user

The CWD ancestry walk (step 3) starts at the current working directory and
walks up parent directories until it finds a ``.otaman`` file that contains
an ``agent:`` line, or reaches the filesystem root.

Step 4 emits a DEPRECATED warning to stderr when it is the source.  Step 5
returns ``None``; callers that can't continue without an identity should exit
with an instructive message.

The earlier step-2 (CWD→platform.yaml→owner) from the 2026-04-29 fix is now
subsumed by step 3: ``otaman init`` writes ``agent: <owner>`` into each
repo's ``.otaman`` file, so the CWD walk finds the right identity without
needing to parse ``platform.yaml`` at runtime.  The ``platform.yaml`` lookup
is kept as a fallback within step 3 for repos that haven't been updated yet.
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
            # Found a .otaman file but no agent: field — stop walking
            # (this repo intentionally has no agent binding, e.g. otaman-meta)
            return None
    return None


def resolve_agent_identity(
    root: Path,
    cwd: Path | None = None,
    explicit: str | None = None,
) -> str | None:
    """Resolve which agent identity to act as.

    Priority chain (D1 of agent-identity-per-directory spec):
    1. explicit arg (CLI --agent / direct call)
    2. OTAMAN_AGENT environment variable
    3. ~/.otaman-session file (written by ``otaman set-agent``)
    4. .otaman ``agent:`` field found by CWD ancestry walk
       (falls back to platform.yaml CWD->owner for un-updated repos)
    5. .agents/current-agent (deprecated; emits warning)
    6. None (caller decides whether to error)
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

    # 3. ~/.otaman-session (session-local; set by `otaman set-agent`)
    session_file = Path.home() / ".otaman-session"
    if session_file.is_file():
        try:
            text = session_file.read_text(encoding="utf-8").strip()
            if text and not text.startswith("#"):
                return text
        except OSError:
            pass

    # 4a. .otaman agent: field — CWD ancestry walk
    dotoman_agent = _read_otaman_agent_field(cwd)
    if dotoman_agent:
        return dotoman_agent

    # 4b. CWD → platform.yaml → owner (for repos that haven't been updated
    #     by `otaman init --update` yet; same logic as the 2026-04-29 fix)
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

    # 5. .agents/current-agent — deprecated fallback
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

    # 6. Nothing found
    return None
