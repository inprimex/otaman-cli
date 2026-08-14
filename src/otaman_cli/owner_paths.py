"""`owner-paths` resolver + validator (monorepo-path-ownership, JTBD-48 Phase 1).

The authoritative resolver lives in otaman-core (task 1.3 — `resolve_owner_for_path`).
This module duplicates the algorithm locally so `otaman whoami --for-path` and
`otaman owner-paths --validate` (cli-agent tasks 2.1, 2.2) can ship before
core's helper lands.

Both functions read `platform.yaml` directly from disk — no `RepoConfig`
dependency.  Once core's `resolve_owner_for_path` ships we can switch this
to a thin wrapper; the public CLI surface won't change.

Algorithm (per design.md §Layer 2):
    1. Find the repo whose name == enclosing dir name (walk up from <path>).
    2. Within that repo's `owner-paths:` dict, find every glob that matches.
    3. Tie-break by specificity = `len(pattern)` (longest wins, matches
       gitignore convention).
    4. If no glob matches → return the repo's root `owner`.
    5. If no repo encloses the path → return `None` (caller decides).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------- glob matcher
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a compiled regex anchored at both ends.

    Supported wildcards (Phase 1):
        **      — matches any sequence of characters, including slashes
        *       — matches anything except `/`
        ?       — matches a single character except `/`

    Other special regex characters are escaped.  Patterns are anchored;
    a trailing `**` is required to match subtrees.  Example:
        `apps/web/**` → `^apps/web/.*$` → matches `apps/web/src/App.tsx`
        `apps/web`    → `^apps/web$`    → matches only `apps/web` exactly
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _glob_matches(path: str, pattern: str) -> bool:
    """Check whether *path* matches *pattern* under gitignore-style semantics."""
    return bool(_compile_glob(pattern).match(path))


# ---------------------------------------------------------------- resolver
@dataclass
class OwnerResolution:
    """Result of resolving a path's owner."""

    agent: str
    matched_glob: str | None  # None when fallback to repo root owner
    fallback_reason: str | None  # set when matched_glob is None
    repo_name: str
    relative_path: str  # path relative to the repo root


def load_platform_yaml(root: Path) -> dict[str, Any] | None:
    """Read + parse `platform.yaml` from the project root; return None on error."""
    p = root / "platform.yaml"
    if not p.is_file():
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or None
    except yaml.YAMLError:
        return None


def _find_enclosing_repo(
    target_path: Path, platform: dict[str, Any], project_root: Path
) -> tuple[dict[str, Any], Path] | None:
    """Find the repo entry whose path-on-disk encloses *target_path*.

    Resolution: walk each `repos[]` entry, resolve its `path:` field relative
    to *project_root*, and check whether *target_path* is inside.  Returns the
    repo dict + the absolute repo root, or None if no enclosing repo found.
    """
    repos = platform.get("repos") or []
    if not isinstance(repos, list):
        return None
    target_abs = target_path.resolve()
    best: tuple[dict[str, Any], Path] | None = None
    best_depth = -1
    for r in repos:
        if not isinstance(r, dict):
            continue
        rel = r.get("path")
        if not isinstance(rel, str):
            continue
        repo_abs = (project_root / rel).resolve()
        try:
            target_abs.relative_to(repo_abs)
        except ValueError:
            continue
        # Prefer the deepest match (a nested repo wins over its parent).
        depth = len(repo_abs.parts)
        if depth > best_depth:
            best = (r, repo_abs)
            best_depth = depth
    return best


def resolve_owner_for_path(target_path: Path, *, project_root: Path) -> OwnerResolution | None:
    """Public: resolve the owning agent for *target_path*.

    *project_root* — directory containing `platform.yaml`.  *target_path*
    may be absolute or relative-to-CWD; will be resolved to an absolute
    path before matching.

    Returns None when:
      - `platform.yaml` is missing/unparseable
      - the path is not inside any registered repo
    """
    platform = load_platform_yaml(project_root)
    if platform is None:
        return None

    target_abs = target_path.resolve()
    enclosing = _find_enclosing_repo(target_abs, platform, project_root)
    if enclosing is None:
        return None
    repo, repo_abs = enclosing

    rel = target_abs.relative_to(repo_abs).as_posix()
    repo_name = str(repo.get("name") or "(unnamed)")
    root_owner = str(repo.get("owner") or "")

    owner_paths_raw = repo.get("owner-paths") or repo.get("owner_paths")
    owner_paths: dict[str, str]
    if isinstance(owner_paths_raw, dict):
        owner_paths = {str(k): str(v) for k, v in owner_paths_raw.items()}
    else:
        owner_paths = {}

    if not owner_paths:
        return OwnerResolution(
            agent=root_owner,
            matched_glob=None,
            fallback_reason="no owner-paths configured",
            repo_name=repo_name,
            relative_path=rel,
        )

    best_pat: str | None = None
    best_spec = -1
    for pat, _agent in owner_paths.items():
        if not _glob_matches(rel, pat):
            continue
        specificity = len(pat)
        if specificity > best_spec:
            best_spec = specificity
            best_pat = pat

    if best_pat is None:
        return OwnerResolution(
            agent=root_owner,
            matched_glob=None,
            fallback_reason="fallback — no glob matched",
            repo_name=repo_name,
            relative_path=rel,
        )

    return OwnerResolution(
        agent=str(owner_paths[best_pat]),
        matched_glob=best_pat,
        fallback_reason=None,
        repo_name=repo_name,
        relative_path=rel,
    )


# ---------------------------------------------------------------- validator
@dataclass
class ValidationFinding:
    """One result row in the validator's report."""

    severity: str  # "ok" | "warn" | "error"
    repo: str
    pattern: str
    agent: str
    note: str | None = None


def declared_agents_from_platform(platform: dict[str, Any]) -> set[str]:
    """Return the set of agent names `platform.yaml` declares.

    Sources: the explicit `agents:` list, plus every `repos[].owner` (a
    repo's owner is a valid agent name even without an explicit `agents:`
    list). Shared between `validate_owner_paths`'s ERROR check and
    `identity.py`'s R3 validation of `.agents/current-agent` (2026-07-08) —
    one roster, not two independently-maintained copies.
    """
    declared_agents: set[str] = set()
    agents_field = platform.get("agents")
    if isinstance(agents_field, list):
        for a in agents_field:
            if isinstance(a, dict):
                name = a.get("name")
                if isinstance(name, str) and name:
                    declared_agents.add(name)
    for r in platform.get("repos") or []:
        if isinstance(r, dict) and isinstance(r.get("owner"), str) and r["owner"]:
            declared_agents.add(r["owner"])
    return declared_agents


def validate_owner_paths(project_root: Path) -> list[ValidationFinding]:
    """Walk every repo's `owner-paths:` block + report issues.

    Rules (per design.md §Validation):
      ERROR — referenced agent not declared in `platform.yaml agents:`
              (an empty/missing agents list disables this check — repos
              still need to be ownable in the absence of a typed agent
              roster).
      WARN  — two patterns in the same repo match a sample path at equal
              specificity (genuine ambiguity; tiebreak by declared order).

    Returns ordered list — one ValidationFinding per (repo, pattern), plus
    one extra WARN entry per ambiguity pair.
    """
    findings: list[ValidationFinding] = []
    platform = load_platform_yaml(project_root)
    if platform is None:
        return findings

    declared_agents = declared_agents_from_platform(platform)

    for r in platform.get("repos") or []:
        if not isinstance(r, dict):
            continue
        repo_name = str(r.get("name") or "(unnamed)")
        owner_paths_raw = r.get("owner-paths") or r.get("owner_paths") or {}
        if not isinstance(owner_paths_raw, dict) or not owner_paths_raw:
            continue
        owner_paths = {str(k): str(v) for k, v in owner_paths_raw.items()}

        # Per-pattern checks
        for pat, agent in owner_paths.items():
            if declared_agents and agent not in declared_agents:
                findings.append(
                    ValidationFinding(
                        severity="error",
                        repo=repo_name,
                        pattern=pat,
                        agent=agent,
                        note=f"{agent} is not declared in platform.yaml agents:",
                    )
                )
            else:
                findings.append(
                    ValidationFinding(
                        severity="ok",
                        repo=repo_name,
                        pattern=pat,
                        agent=agent,
                    )
                )

        # Overlap detection: for each pair of patterns, check whether
        # there's a path that matches both at equal specificity.  The
        # cheap heuristic: equal-length patterns whose regex shapes can
        # share at least one example matching path.  We use a tiny
        # representative path derived from the first pattern.
        pats = list(owner_paths.keys())
        for i in range(len(pats)):
            for j in range(i + 1, len(pats)):
                p1, p2 = pats[i], pats[j]
                if len(p1) != len(p2):
                    continue  # different specificity — natural tiebreak
                # Build a candidate path from p1 by replacing wildcards
                # with safe stand-ins, then check whether p2 also matches.
                sample = _example_path_for(p1)
                if _glob_matches(sample, p1) and _glob_matches(sample, p2):
                    findings.append(
                        ValidationFinding(
                            severity="warn",
                            repo=repo_name,
                            pattern=p1,
                            agent=owner_paths[p1],
                            note=(
                                f'Overlap: "{sample}" matches both "{p1}" and "{p2}" '
                                f"at equal specificity. Tiebreak: declared order — "
                                f'"{p1}" wins.'
                            ),
                        )
                    )

    return findings


def _example_path_for(pattern: str) -> str:
    """Build a sample path that matches *pattern* — used for overlap detection."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and pattern[i : i + 2] == "**":
            out.append("any/path/here")
            i += 2
        elif c == "*":
            out.append("anyhere")
            i += 1
        elif c == "?":
            out.append("X")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


__all__ = [
    "OwnerResolution",
    "ValidationFinding",
    "load_platform_yaml",
    "resolve_owner_for_path",
    "validate_owner_paths",
]
