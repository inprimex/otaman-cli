"""Per-tab agent identity resolution (carved from legacy cli/maestro.py).

Resolution priority (the 2026-04-29 cross-tab identity-leak fix):
1. ``explicit`` arg from the command line
2. Owner of the repo whose path covers ``cwd`` (from ``platform.yaml``)
3. ``.agents/current-agent`` file (project-global fallback)

Step 2 is the per-repo override that prevents every tab from reading the
same global identity from ``current-agent`` (set last by some other tab)
instead of the owner of the repo it was actually launched in.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core._resolve import find_maestro_root


def find_project_root(start: Path | None = None) -> Path | None:
    """Find the maestro root directory.

    Resolution chain:
    1. .maestro marker file (contains relative path to maestro folder)
    2. MAESTRO_ROOT environment variable
    3. Walk-up fallback (legacy: platform.yaml or .agents/ in parent)

    Thin wrapper over otaman_core._resolve.find_maestro_root for clarity at
    call sites that don't otherwise import from otaman_core directly.
    """
    return find_maestro_root(start)


def resolve_agent_identity(
    root: Path,
    cwd: Path | None = None,
    explicit: str | None = None,
) -> str | None:
    """Resolve which agent identity to act as, given a project root and cwd."""
    if explicit:
        return explicit
    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()
    # 2. CWD → repo → owner
    platform_yaml = root / "platform.yaml"
    if platform_yaml.is_file():
        data: dict = {}
        try:
            import yaml as _yaml
            data = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
        except (OSError, ImportError):
            data = {}
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
    # 3. Project-global fallback
    agent_file = root / ".agents" / "current-agent"
    if agent_file.is_file():
        text = agent_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None
