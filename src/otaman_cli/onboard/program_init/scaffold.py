"""In-process companion-repos invocation stub (tasks.md 3.1).

Interface contract for bridge-agent:

    otaman_bridge.scaffold.scaffold_companion_repos(
        program_slug: str,
        repos: list[str],          # ["business", "strategy"]
        answers: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> ScaffoldResult

This module wraps that call.  If the bridge package is not installed or
doesn't expose the function yet, it falls back to a human-readable
instruction so the user knows what to run manually.

Result contract::

    @dataclass
    class ScaffoldResult:
        scaffolded: list[str]   # repos actually created
        skipped: list[str]      # repos that already existed
        errors: list[str]       # error messages (empty == all ok)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScaffoldResult:
    scaffolded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def scaffold_companion_repos(
    program_slug: str,
    repos: list[str],
    answers: dict[str, Any],
    *,
    dry_run: bool = False,
) -> ScaffoldResult:
    """Invoke bridge-agent's scaffold logic in-process.

    Falls back gracefully when the bridge is not yet available.
    The interface is stable — bridge-agent implements the matching function.
    """
    if not repos:
        return ScaffoldResult()

    # Attempt in-process call
    try:
        from otaman_bridge.scaffold import (
            scaffold_companion_repos as _bridge_scaffold,  # type: ignore[import-not-found]
        )

        return _bridge_scaffold(program_slug, repos, answers, dry_run=dry_run)
    except ImportError:
        pass  # bridge not installed yet

    # Fallback — guide the user
    result = ScaffoldResult()
    for repo_kind in repos:
        repo_name = f"{program_slug}-{repo_kind}"
        result.errors.append(
            f"bridge not available — scaffold {repo_name!r} manually: "
            f"`otaman init companion-repos --program {program_slug} --repos {repo_kind}`"
        )
    return result


def compute_companion_repos(processes: list[str]) -> list[str]:
    """Compute which companion repos to scaffold based on opted-in processes.

    Logic (per program-companion-repos-scaffold proposal):
        business repo  — when any of: outcomes, solutions, personas,
                         vocabulary, flows, processes, risks, assumptions
        strategy repo  — when: strategy

    Returns a list of repo kinds, e.g. ["business", "strategy"].
    """
    repos: list[str] = []
    _business_triggers = {
        "outcomes",
        "solutions",
        "personas",
        "vocabulary",
        "flows",
        "processes",
        "risks",
        "assumptions",
    }
    if _business_triggers.intersection(processes):
        repos.append("business")
    if "strategy" in processes:
        repos.append("strategy")
    return repos
