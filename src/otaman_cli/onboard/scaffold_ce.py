"""CE-mode in-process companion-repos scaffolder (ce-companion-repos-scaffold).

No bridge imports. No network calls. No git remote.

For each companion repo kind in `compute_companion_repos(processes)`:
  1. Determine target path: `meta_dir.parent / f"{program_slug}-{kind}"`
  2. If path exists and not `force`: skip with info log
  3. Create directory, render + write all templates for that kind
  4. `git init` + initial commit (always — per ce-companion-repos-scaffold proposal)
  5. Update `platform.yaml repos[]` via ruamel round-trip
  6. On failure: raise ScaffoldError with actionable message
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from ruamel.yaml import YAML



# Module-local ruamel YAML instance — round-trip preserves comments + key order
_YAML = YAML()
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# Per-kind metadata: owner agent + description for the platform.yaml repo entry
_KIND_META: dict[str, dict[str, str]] = {
    "business": {
        "owner": "cpo-agent",
        "description": "Business registry: outcomes, solutions, personas",
    },
    "strategy": {
        "owner": "cofounder-agent",
        "description": "Strategy artifacts: pitch deck, business plan, GTM, financials",
    },
}


class ScaffoldError(RuntimeError):
    """Raised when CE scaffold fails. Message must be actionable."""


@dataclass
class ScaffoldedRepo:
    """One companion repo created (or skipped) by the scaffolder."""
    kind: str
    path: Path
    owner: str
    skipped: bool = False   # True when path already exists and force=False
    skipped_reason: str = ""


@dataclass
class ScaffoldCEResult:
    """Outcome of a `scaffold_companion_repos_ce` invocation."""
    repos: list[ScaffoldedRepo] = field(default_factory=list)
    platform_yaml_updated: bool = False

    @property
    def created(self) -> list[ScaffoldedRepo]:
        return [r for r in self.repos if not r.skipped]

    @property
    def skipped(self) -> list[ScaffoldedRepo]:
        return [r for r in self.repos if r.skipped]


def scaffold_companion_repos_ce(
    program_slug: str,
    processes: list[str],
    meta_dir: Path,
    *,
    program_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    repo_kinds: list[str] | None = None,
) -> ScaffoldCEResult:
    """Scaffold companion repos for a CE-mode program. In-process; no bridge.

    Args:
        program_slug: kebab-case program identifier (e.g. "epicbridge")
        processes:    enabled program processes — drives which companion repos
                      are created (via compute_companion_repos). Ignored when
                      *repo_kinds* is explicitly provided.
        meta_dir:     absolute path to the program's meta/specs repo (where
                      platform.yaml lives). Companion repos are created as
                      siblings: meta_dir.parent / f"{program_slug}-{kind}".
        program_name: human-readable name for template rendering. Defaults to
                      *program_slug* if not provided.
        force:        when True, re-scaffold even if target dir exists
                      (destroys and recreates — caller is expected to prompt
                      the user before passing force=True).
        dry_run:      when True, plan only; no filesystem writes.
        repo_kinds:   explicit list of kinds to scaffold (overrides the
                      processes-based derivation). Useful for the
                      `otaman init companion-repos --repos KIND` flag.

    Returns:
        ScaffoldCEResult — list of created + skipped repos.

    Raises:
        ScaffoldError: with an actionable error message on any failure
                       (directory create, git invoke, template render,
                       platform.yaml round-trip).
    """
    program_name = program_name or program_slug

    if repo_kinds is None:
        from otaman_cli.onboard.program_init.scaffold import (
            compute_companion_repos,
        )
        repo_kinds = compute_companion_repos(processes)
    if not repo_kinds:
        return ScaffoldCEResult()

    # Validate kinds we know how to scaffold up front
    unknown = [k for k in repo_kinds if k not in _KIND_META]
    if unknown:
        raise ScaffoldError(
            f"Unknown companion repo kind(s): {unknown}. "
            f"Supported: {sorted(_KIND_META.keys())}"
        )

    result = ScaffoldCEResult()
    new_repo_entries: list[dict[str, Any]] = []

    for kind in repo_kinds:
        target = meta_dir.parent / f"{program_slug}-{kind}"
        owner = _KIND_META[kind]["owner"]

        if target.exists() and not force:
            result.repos.append(ScaffoldedRepo(
                kind=kind, path=target, owner=owner,
                skipped=True, skipped_reason="path already exists (use --force to recreate)",
            ))
            continue

        if dry_run:
            result.repos.append(ScaffoldedRepo(kind=kind, path=target, owner=owner))
            new_repo_entries.append(_build_repo_entry(program_slug, kind, owner))
            continue

        # Force-recreate: remove existing dir tree (after caller-side confirmation)
        if target.exists() and force:
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise ScaffoldError(
                    f"Cannot remove existing {target} for --force recreate: {exc}"
                ) from exc

        try:
            target.mkdir(parents=True, exist_ok=False)
            _render_templates(kind, target, program_slug, program_name)
            _git_init_and_commit(target, program_slug, kind)
        except ScaffoldError:
            raise
        except Exception as exc:
            raise ScaffoldError(
                f"Failed to scaffold {target}: {exc}. "
                f"Retry with: `otaman init companion-repos --repos {kind} --force`"
            ) from exc

        result.repos.append(ScaffoldedRepo(kind=kind, path=target, owner=owner))
        new_repo_entries.append(_build_repo_entry(program_slug, kind, owner))

    # platform.yaml round-trip update (skip in dry-run)
    if new_repo_entries and not dry_run:
        try:
            _append_to_platform_yaml(meta_dir / "platform.yaml", new_repo_entries)
            result.platform_yaml_updated = True
        except Exception as exc:
            raise ScaffoldError(
                f"Created companion repos but failed to update platform.yaml: {exc}. "
                f"Manually append the following entries under repos: "
                f"{new_repo_entries}"
            ) from exc

    return result


# ---------------------------------------------------------------------------
# Internal helpers


def _build_repo_entry(program_slug: str, kind: str, owner: str) -> dict[str, Any]:
    """Build a `repos[]` entry matching the existing platform.yaml convention."""
    return {
        "name": f"{program_slug}-{kind}",
        "path": f"../{program_slug}-{kind}",
        "owner": owner,
        "description": _KIND_META[kind]["description"],
    }


def _render_templates(
    kind: str, target: Path, program_slug: str, program_name: str,
) -> None:
    """Render every template file in templates/<kind>/ into *target*.

    Files ending in `.j2` are Jinja2-rendered and written without the suffix.
    Other files are copied verbatim.
    """
    kind_dir = _TEMPLATES_DIR / kind
    if not kind_dir.is_dir():
        raise ScaffoldError(f"No template directory for kind={kind!r} at {kind_dir}")

    env = Environment(
        loader=FileSystemLoader(str(kind_dir)),
        autoescape=select_autoescape(disabled_extensions=("md", "yaml", "yml")),
        keep_trailing_newline=True,
    )
    ctx = {"program_slug": program_slug, "program_name": program_name}

    # iterdir won't include nested directories by default — we don't need them
    # for v1, but if templates grow nested structure later, walk recursively.
    for src in sorted(kind_dir.iterdir()):
        if src.is_dir():
            continue
        rel = src.name
        if rel.endswith(".j2"):
            template = env.get_template(rel)
            rendered = template.render(**ctx)
            dest = target / rel[: -len(".j2")]
        else:
            rendered = src.read_text(encoding="utf-8")
            dest = target / rel
        dest.write_text(rendered, encoding="utf-8")


def _git_init_and_commit(target: Path, program_slug: str, kind: str) -> None:
    """Initialise a local git repo and make the initial scaffold commit."""
    def _run(cmd: list[str]) -> None:
        result = subprocess.run(
            cmd, cwd=str(target), capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise ScaffoldError(
                f"git command failed in {target}: {' '.join(cmd)}\n"
                f"  stderr: {result.stderr.strip() or '<empty>'}"
            )
    _run(["git", "init", "--quiet"])
    _run(["git", "add", "."])
    # Use --no-gpg-sign to avoid hanging on signing-required setups, and
    # explicit author args so commits work even when global git is unconfigured.
    _run([
        "git",
        "-c", "user.email=otaman@localhost",
        "-c", "user.name=otaman",
        "-c", "commit.gpgsign=false",
        "commit", "--quiet",
        "-m", f"scaffold: initialize {program_slug}-{kind} companion repo",
    ])


def _append_to_platform_yaml(
    platform_yaml_path: Path, new_entries: list[dict[str, Any]],
) -> None:
    """Append entries to platform.yaml `repos[]` via ruamel round-trip.

    Idempotent: if an entry with the same `name` already exists, it's skipped.
    """
    if not platform_yaml_path.is_file():
        raise ScaffoldError(f"platform.yaml not found at {platform_yaml_path}")

    with platform_yaml_path.open("r", encoding="utf-8") as f:
        doc = _YAML.load(f) or {}

    existing_repos = doc.get("repos") or []
    if not isinstance(existing_repos, list):
        raise ScaffoldError("platform.yaml `repos` must be a list")
    existing_names = {r.get("name") for r in existing_repos if isinstance(r, dict)}

    added_any = False
    for entry in new_entries:
        if entry["name"] in existing_names:
            continue
        existing_repos.append(entry)
        added_any = True

    if added_any:
        doc["repos"] = existing_repos
        with platform_yaml_path.open("w", encoding="utf-8") as f:
            _YAML.dump(doc, f)


__all__ = [
    "ScaffoldError",
    "ScaffoldedRepo",
    "ScaffoldCEResult",
    "scaffold_companion_repos_ce",
]
