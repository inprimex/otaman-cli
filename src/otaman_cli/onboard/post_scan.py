"""Post-scan UX hardening for `otaman scan` (otaman-scan-ux-hardening).

Runs after `discover-repos.py` writes `platform.yaml.draft`. Detects gaps in
the draft, prompts the user (TTY only), scaffolds any locally-creatable
artifacts, and rewrites the draft so the resulting `otaman init` produces a
workable program without manual hand-editing.

Four gaps addressed:

1. **No `<program>-specs/` repo discovered**: prompt to create one and
   scaffold a local git repo with skeleton files; add to draft's `repos[]`.
2. **An empty `<program>-specs/` sibling git repo exists but wasn't
   recognised as the specs folder**: lift it into the draft's `repos[]`
   with `owner: spec-agent` and update `specs.path` to point at it.
3. **No `launcher:` block in the draft**: emit a sensible stub (local
   enabled + ssh placeholder) so remote workflows aren't dead on arrival.
4. **`specs.format: fallback` chosen because no OpenSpec detected**:
   prompt to scaffold `openspec/` in the specs repo + flip the draft
   to `format: openspec` + the new path.

All four fixes degrade to no-op cleanly when stdin is non-TTY (CI / piped
scripts), preserving the legacy scan behaviour for automation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from ruamel.yaml import YAML

_YAML = YAML()
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class PostScanGaps:
    """Result of `analyze_draft` — which UX gaps the draft has."""

    specs_repo_missing: bool = False
    # An empty `<program>-specs/` exists but isn't in the draft's repos[]
    specs_repo_unrecognised_path: Path | None = None
    launcher_block_missing: bool = False
    openspec_missing: bool = False

    def any(self) -> bool:
        return any([
            self.specs_repo_missing,
            self.specs_repo_unrecognised_path is not None,
            self.launcher_block_missing,
            self.openspec_missing,
        ])


@dataclass
class PostScanResult:
    """What was actually done by `interactive_fix`."""

    specs_repo_created: Path | None = None
    specs_repo_lifted: Path | None = None
    launcher_block_added: bool = False
    openspec_scaffolded: Path | None = None
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analysis


def _is_spec_repo_name(name: str) -> bool:
    n = name.lower()
    return (
        n.endswith("-specs")
        or n.endswith("-spec")
        or n in ("specs", "spec", "openspec")
    )


def analyze_draft(
    draft_path: Path,
    scan_root: Path,
    program_slug: str,
) -> PostScanGaps:
    """Read the draft + scan root; report what's missing."""
    gaps = PostScanGaps()
    if not draft_path.is_file():
        return gaps

    doc = _YAML.load(draft_path.read_text(encoding="utf-8")) or {}
    repos = doc.get("repos") or []
    repo_paths_in_draft = {
        (scan_root / (r.get("path") or "")).resolve()
        for r in repos
        if isinstance(r, dict)
    }
    has_specs_in_draft = any(
        isinstance(r, dict) and _is_spec_repo_name(r.get("name", ""))
        for r in repos
    )

    # #1 + #2: specs repo missing OR unrecognised
    if not has_specs_in_draft:
        # Maybe a sibling -specs/ dir exists that the scanner skipped/missed
        candidate = scan_root / f"{program_slug}-specs"
        if candidate.is_dir() and candidate.resolve() not in repo_paths_in_draft:
            gaps.specs_repo_unrecognised_path = candidate
        else:
            gaps.specs_repo_missing = True

    # #3: launcher block missing
    if "launcher" not in doc:
        gaps.launcher_block_missing = True

    # #4: OpenSpec missing (drafted as fallback)
    specs_block = doc.get("specs") or {}
    if isinstance(specs_block, dict) and specs_block.get("format") != "openspec":
        gaps.openspec_missing = True

    return gaps


# ---------------------------------------------------------------------------
# Interactive prompts


def _ask_yes_no(question: str, *, default: bool = True) -> bool:
    """Prompt-based yes/no, returns default on EOF / Ctrl-C."""
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  ? {question} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Scaffolders (in-process; no bridge dependency, no network)


def _render_templates(kind_dir: Path, target: Path, ctx: dict[str, Any]) -> None:
    """Render every file in kind_dir/ into target/, applying Jinja2 to .j2 files."""
    if not kind_dir.is_dir():
        raise FileNotFoundError(f"template dir missing: {kind_dir}")
    env = Environment(
        loader=FileSystemLoader(str(kind_dir)),
        autoescape=select_autoescape(disabled_extensions=("md", "yaml", "yml")),
        keep_trailing_newline=True,
    )
    for src in sorted(kind_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(kind_dir)
        dest_rel = rel.with_name(rel.name[: -len(".j2")]) if rel.name.endswith(".j2") else rel
        dest = target / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.name.endswith(".j2"):
            rendered = env.get_template(rel.as_posix()).render(**ctx)
        else:
            rendered = src.read_text(encoding="utf-8")
        dest.write_text(rendered, encoding="utf-8")


def _git_init_and_commit(target: Path, commit_msg: str) -> None:
    """git init + initial commit. Local-only; no remote."""
    def _run(cmd: list[str]) -> None:
        r = subprocess.run(cmd, cwd=str(target), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"git command failed in {target}: {' '.join(cmd)}: "
                f"{r.stderr.strip() or '<empty>'}"
            )
    _run(["git", "init", "--quiet"])
    _run(["git", "add", "."])
    _run([
        "git",
        "-c", "user.email=otaman@localhost",
        "-c", "user.name=otaman",
        "-c", "commit.gpgsign=false",
        "commit", "--quiet", "-m", commit_msg,
    ])


def scaffold_specs_repo(target: Path, program_slug: str, program_name: str) -> None:
    """Create a `<program>-specs/` sibling repo with skeleton + initial commit."""
    if target.exists():
        raise FileExistsError(f"specs repo already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    _render_templates(
        _TEMPLATES_DIR / "specs", target,
        {"program_slug": program_slug, "program_name": program_name},
    )
    _git_init_and_commit(target, f"scaffold: initialize {program_slug}-specs repo")


def scaffold_openspec(specs_repo: Path) -> Path:
    """Lay out a minimal `openspec/` directory inside the specs repo.

    Returns the absolute path to the scaffolded `openspec/` directory.
    """
    openspec_dir = specs_repo / "openspec"
    if openspec_dir.exists():
        return openspec_dir
    openspec_dir.mkdir(parents=True, exist_ok=True)
    _render_templates(_TEMPLATES_DIR / "openspec", openspec_dir, {})

    # If the specs repo is a git repo, commit the openspec scaffold separately
    if (specs_repo / ".git").exists():
        try:
            subprocess.run(
                ["git", "add", "openspec"], cwd=str(specs_repo),
                capture_output=True, text=True, check=False,
            )
            subprocess.run(
                [
                    "git",
                    "-c", "user.email=otaman@localhost",
                    "-c", "user.name=otaman",
                    "-c", "commit.gpgsign=false",
                    "commit", "--quiet", "-m", "scaffold: initialize OpenSpec layout",
                ],
                cwd=str(specs_repo), capture_output=True, text=True, check=False,
            )
        except OSError:
            pass  # best-effort; scaffold itself succeeded
    return openspec_dir


# ---------------------------------------------------------------------------
# Draft mutators


def _default_launcher_block() -> dict[str, Any]:
    """Return the stub launcher block emitted into the draft (fix #3)."""
    return {
        "local": {"enabled": True},
        "ssh": {
            "enabled": False,
            "host": "user@host.example.com",
            "repo_path": "/path/on/remote",
        },
    }


def _build_specs_repo_entry(
    scan_root: Path, otaman_dir: Path, specs_path: Path, program_slug: str,
) -> dict[str, Any]:
    """Build the `repos[]` entry for the new/lifted specs repo.

    `path` is written relative to *otaman_dir* (matches the existing repo entries
    written by discover-repos which are relative to the otaman folder).
    """
    try:
        rel = specs_path.relative_to(otaman_dir)
    except ValueError:
        # Different drive / parent — fall back to "../{name}" common case
        rel = Path("..") / specs_path.name
    return {
        "name": specs_path.name,
        "path": f"./{rel.as_posix()}" if not str(rel).startswith("..") else rel.as_posix(),
        "owner": "spec-agent",
        "description": "Specifications: OpenSpec changes + capability specs",
    }


def update_draft(
    draft_path: Path,
    *,
    add_specs_repo: dict[str, Any] | None = None,
    add_launcher: bool = False,
    set_specs_format_openspec: Path | None = None,
) -> None:
    """Mutate `platform.yaml.draft` per the diagnostics.

    *set_specs_format_openspec* is the absolute path to the scaffolded
    `openspec/` directory; the function rewrites the `specs:` block to point
    at it via a relative path from the draft's directory.
    """
    if not draft_path.is_file():
        return
    doc = _YAML.load(draft_path.read_text(encoding="utf-8")) or {}

    if add_specs_repo:
        repos = doc.get("repos") or []
        if not isinstance(repos, list):
            repos = []
        existing_names = {r.get("name") for r in repos if isinstance(r, dict)}
        if add_specs_repo["name"] not in existing_names:
            repos.append(add_specs_repo)
            doc["repos"] = repos
        # Point specs.path at the new repo (with format=fallback by default;
        # set_specs_format_openspec can override below)
        doc.setdefault("specs", {})["path"] = add_specs_repo["path"]

    if add_launcher and "launcher" not in doc:
        doc["launcher"] = _default_launcher_block()

    if set_specs_format_openspec is not None:
        otaman_dir = draft_path.parent
        try:
            rel = set_specs_format_openspec.relative_to(otaman_dir)
            specs_path = f"./{rel.as_posix()}"
        except ValueError:
            specs_path = str(set_specs_format_openspec)
        doc.setdefault("specs", {})
        doc["specs"]["path"] = specs_path
        doc["specs"]["format"] = "openspec"

    with draft_path.open("w", encoding="utf-8") as f:
        _YAML.dump(doc, f)


# ---------------------------------------------------------------------------
# Top-level orchestrator


def run(
    draft_path: Path,
    scan_root: Path,
    otaman_dir: Path,
    program_slug: str,
    *,
    program_name: str | None = None,
    interactive: bool | None = None,
) -> PostScanResult:
    """Run the full post-scan UX flow.

    Args:
        draft_path:   path to `platform.yaml.draft` (just written by discover-repos)
        scan_root:    the directory that was scanned
        otaman_dir:   the `<program>-otaman/` meta folder
        program_slug: kebab-case program identifier
        program_name: human-readable name (defaults to program_slug)
        interactive:  override TTY detection; True = ask prompts, False = skip
                      prompts (no scaffolding). Default: detect via sys.stdin.isatty().

    Returns:
        PostScanResult describing what was created/lifted/skipped.
    """
    program_name = program_name or program_slug
    if interactive is None:
        interactive = sys.stdin.isatty()

    result = PostScanResult()
    gaps = analyze_draft(draft_path, scan_root, program_slug)

    if not gaps.any():
        return result

    # Fix #1 + #2 — specs repo
    specs_repo_path: Path | None = None
    if gaps.specs_repo_unrecognised_path is not None:
        # User pre-created the dir; just lift it into the draft (no prompt)
        specs_repo_path = gaps.specs_repo_unrecognised_path
        result.specs_repo_lifted = specs_repo_path
    elif gaps.specs_repo_missing:
        if interactive:
            target = scan_root / f"{program_slug}-specs"
            if _ask_yes_no(
                f"No specs repo detected. Create '{target.name}/' as a sibling?",
                default=True,
            ):
                try:
                    scaffold_specs_repo(target, program_slug, program_name)
                    specs_repo_path = target
                    result.specs_repo_created = target
                except Exception as exc:
                    result.skipped.append(f"specs scaffold failed: {exc}")
            else:
                result.skipped.append("specs repo (declined)")
        else:
            result.skipped.append("specs repo (non-TTY)")

    # Fix #4 — OpenSpec scaffold (only if we have a specs repo to put it in)
    openspec_dir: Path | None = None
    if specs_repo_path is not None and gaps.openspec_missing:
        existing_openspec = specs_repo_path / "openspec"
        if existing_openspec.is_dir():
            openspec_dir = existing_openspec
            result.openspec_scaffolded = existing_openspec
        elif interactive:
            if _ask_yes_no(
                "Scaffold openspec/ in the specs repo (recommended)?",
                default=True,
            ):
                try:
                    openspec_dir = scaffold_openspec(specs_repo_path)
                    result.openspec_scaffolded = openspec_dir
                except Exception as exc:
                    result.skipped.append(f"openspec scaffold failed: {exc}")
            else:
                result.skipped.append("openspec (declined)")
        else:
            result.skipped.append("openspec (non-TTY)")

    # Mutate the draft
    add_specs_entry: dict[str, Any] | None = None
    if specs_repo_path is not None:
        add_specs_entry = _build_specs_repo_entry(
            scan_root, otaman_dir, specs_repo_path, program_slug,
        )

    update_draft(
        draft_path,
        add_specs_repo=add_specs_entry,
        add_launcher=gaps.launcher_block_missing,
        set_specs_format_openspec=openspec_dir,
    )
    if gaps.launcher_block_missing:
        result.launcher_block_added = True

    return result


__all__ = [
    "PostScanGaps",
    "PostScanResult",
    "analyze_draft",
    "scaffold_specs_repo",
    "scaffold_openspec",
    "update_draft",
    "run",
]
