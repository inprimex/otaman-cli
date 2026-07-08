"""Per-tab agent identity resolution (carved from legacy cli/maestro.py).  # legacy: filename

Resolution priority chain (agent-identity-per-directory spec D1, amended 2026-05-28;
R3 cross-check added 2026-07-08):

1. ``OTAMAN_AGENT`` environment variable  (highest — automated session spawn)
   — cross-checked against the CWD-resolved repo owner (see below); on
   disagreement the CWD-resolved owner wins, loudly.
2. ``.otaman`` ``agent:`` field found by walking up from CWD  (per-repo)
3. CWD → platform.yaml (+ ``owner-paths``) → owner, via the same resolver
   ``otaman whoami --for-path`` uses
4. ``.agents/current-agent`` file  (deprecated project-global fallback,
   validated against platform.yaml's declared agents)
5. ``None`` / ERROR — no identity; caller must prompt user

The CWD ancestry walk (step 2) starts at the current working directory and
walks up parent directories.  A ``.otaman`` file WITHOUT an ``agent:`` field
does NOT stop the walk — the walker continues up until an ``agent:`` value is
found or the filesystem root is reached.

Step 4 emits a DEPRECATED warning to stderr when it is the source, and is
skipped (falls through to step 5) if its value isn't a declared agent. Step 5
returns ``None``; callers that can't continue without an identity should exit
with an instructive message.

``~/.otaman-session`` is no longer read.  It was user-global state (not
session-local) and caused concurrent-session identity collisions (2026-05-28
watchtower incident).  Per-repo ``.otaman agent:`` fields written by
``otaman init`` replace it entirely.

R3 (security/correctness fix, 2026-07-08): step 1 used to be trusted
unconditionally, with no cross-check against the repo the caller is actually
sitting in. A stale/leaked ``OTAMAN_AGENT`` (e.g. a poisoned tmux
server-global environment — the 2026-07-08 greenbin incident, 7 of 8 agent
sessions misidentifying as one agent) then misattributed everything the
session did. The CWD-resolved owner is now computed first and used to
validate ``OTAMAN_AGENT``; on disagreement the CWD-resolved owner is
authoritative (``platform.yaml`` is the source of truth an env var can't
silently override) and a warning is printed so the underlying poisoning gets
noticed and fixed at its source. Step 3 (CWD→platform.yaml→owner) also now
delegates to ``owner_paths.resolve_owner_for_path()`` instead of a simpler,
independently-maintained duplicate — picks up ``owner-paths`` glob overrides
for free. Step 4 now validates its value against platform.yaml's declared
agents (``agents:`` list ∪ ``repos[].owner``) instead of trusting it blindly.
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
    """Walk up from *cwd* looking for agent identity in ``.otaman`` (file or dir shape).

    File shape: parse ``agent:`` field as before.
    Directory shape (otaman-meta legacy): read ``<dir>/.otaman/agent`` (single-line text).
    Missing ``agent:`` / missing ``agent`` file at an ancestor does NOT stop the walk.

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
            # No agent: field in this .otaman file — keep walking up
        elif marker.is_dir():
            agent_file = marker / "agent"
            if agent_file.is_file():
                try:
                    text = agent_file.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                # Take the first non-empty line (D6: extras silently ignored)
                for line in text.splitlines():
                    name = line.strip()
                    if name:
                        return name
            # No agent file in this .otaman dir — keep walking up
    return None


def _resolve_cwd_owner(root: Path, cwd: Path) -> str | None:
    """CWD → platform.yaml (+ ``owner-paths``) → owner.

    Delegates to ``owner_paths.resolve_owner_for_path()`` — the same
    resolver ``otaman whoami --for-path`` uses — instead of re-implementing
    a simpler, glob-unaware duplicate (R3). Falls back to the git worktree's
    main checkout (2026-04-29 fix) when the direct CWD lookup misses;
    ``resolve_owner_for_path()`` has no worktree-awareness of its own.
    """
    from otaman_cli.owner_paths import resolve_owner_for_path

    result = resolve_owner_for_path(cwd, project_root=root)
    if result is not None:
        return result.agent

    try:
        worktree_main = resolve_worktree_main(cwd)
    except Exception:
        worktree_main = None
    if worktree_main is not None:
        result = resolve_owner_for_path(worktree_main, project_root=root)
        if result is not None:
            return result.agent

    return None


def _declared_agents(root: Path) -> set[str]:
    """platform.yaml's declared-agents roster (R3), for validating
    ``.agents/current-agent`` instead of trusting it blindly. Reuses
    ``owner_paths.declared_agents_from_platform`` — one roster, not a
    second independently-maintained copy.
    """
    from otaman_cli.owner_paths import declared_agents_from_platform, load_platform_yaml

    platform = load_platform_yaml(root)
    if platform is None:
        return set()
    return declared_agents_from_platform(platform)


def resolve_agent_identity(
    root: Path,
    cwd: Path | None = None,
    explicit: str | None = None,
) -> str | None:
    """Resolve which agent identity to act as.

    Priority chain (D1, amended 2026-05-28 — no ~/.otaman-session; R3
    cross-check added 2026-07-08):
    1. explicit arg (CLI --agent / direct call)
    2. OTAMAN_AGENT environment variable — cross-checked against the
       CWD-resolved repo owner; disagreement means the CWD-resolved owner
       wins (see module docstring)
    3. .otaman ``agent:`` field found by CWD ancestry walk
    4. CWD → platform.yaml (+ owner-paths) → owner
    5. .agents/current-agent (deprecated; validated against declared
       agents; emits warning)
    6. None (caller decides whether to error)
    """
    # 1. Explicit argument always wins
    if explicit:
        return explicit

    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()

    # Resolved once, up front, so step 2 can cross-check against it and
    # step 4 can reuse it without a second lookup.
    cwd_owner = _resolve_cwd_owner(root, cwd)

    # 2. OTAMAN_AGENT environment variable
    env_agent = os.environ.get("OTAMAN_AGENT", "").strip()
    if env_agent:
        if cwd_owner and cwd_owner != env_agent:
            print(
                f"[otaman] WARNING: OTAMAN_AGENT={env_agent!r} disagrees with the "
                f"repo owner resolved from cwd ({cwd_owner!r}) — using {cwd_owner!r}. "
                f"This usually means a stale/leaked OTAMAN_AGENT (e.g. a poisoned "
                f"tmux server-global environment); fix the source rather than this "
                f"warning.",
                file=sys.stderr,
            )
            return cwd_owner
        return env_agent

    # 3. .otaman agent: field — CWD ancestry walk (keeps walking past .otaman without agent:)
    dotoman_agent = _read_otaman_agent_field(cwd)
    if dotoman_agent:
        return dotoman_agent

    # 4. CWD → platform.yaml (+ owner-paths) → owner — already resolved above
    if cwd_owner:
        return cwd_owner

    # 5. .agents/current-agent — deprecated fallback, validated (R3) against
    #    platform.yaml's declared agents before being trusted.
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
                declared = _declared_agents(root)
                if declared and name not in declared:
                    print(
                        f"[otaman] WARNING: .agents/current-agent contains {name!r}, "
                        f"which is not a declared agent in platform.yaml — ignoring.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[otaman] DEPRECATED: identity resolved from .agents/current-agent ('{name}'). "
                        "Run 'otaman init --update' to migrate to per-repo .otaman agent: fields, "
                        "or set OTAMAN_AGENT in your launch config.",
                        file=sys.stderr,
                    )
                    return name

    # 6. Nothing found
    return None
