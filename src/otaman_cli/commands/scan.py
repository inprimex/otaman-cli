"""`otaman scan` — migrated from main.py.

Previously relied on main()'s shared flag loop to pre-parse --update/
--maestro-dir (+legacy --otaman-dir/--target aliases)/--dry-run/--name
into keyword args before calling in. Folded that parsing into cmd_scan
itself so it takes raw argv like every other registry command (F021/
F022). The shared loop still parses --update/--dry-run too, since `init`
(not yet migrated) still depends on those two -- temporary duplication,
resolved once init migrates.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import C, UI, run_script


def _find_existing_otaman_project(scan_root: Path) -> Path | None:
    """Detect if scan_root is already an otaman project.

    Returns the existing otaman folder Path if found, else None.

    Checks in order:
      1. scan_root itself has platform.yaml (flat layout)
      2. Any child folder matching *-otaman/ or legacy: *-maestro/ with platform.yaml
      3. scan_root has .agents/ (legacy at-root layout)
    """
    if (scan_root / "platform.yaml").is_file():
        return scan_root
    if scan_root.is_dir():
        for child in scan_root.iterdir():
            if not child.is_dir():
                continue
            if child.name.endswith("-otaman") or child.name.endswith("-maestro"):  # legacy: -maestro suffix until otaman-core 1.0
                if (child / "platform.yaml").is_file():
                    return child
    if (scan_root / ".agents").is_dir():
        return scan_root
    return None


def cmd_scan(args: list[str]) -> int:
    """Scan repos and generate draft platform.yaml in a dedicated otaman folder."""
    update = False
    maestro_dir: str | None = None
    dry_run = False
    project_name_override: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--update":
            update = True
            i += 1
        elif args[i] in ("--maestro-dir", "--otaman-dir", "--target") and i + 1 < len(args):  # legacy: backward-compat arg
            maestro_dir = args[i + 1]
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif args[i] == "--name" and i + 1 < len(args):
            project_name_override = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    scan_path = positional[0] if positional else "."
    resolved = Path(scan_path).resolve()

    if dry_run and update:
        UI.header("Otaman Scan --update (dry-run)")
    elif dry_run:
        UI.header("Otaman Scan (dry-run)")
    elif update:
        UI.header("Otaman Scan --update")
        # In update mode, look for platform.yaml in otaman dir or scan dir
        search = Path(maestro_dir).resolve() if maestro_dir else resolved
        if not (search / "platform.yaml").exists():
            UI.error(f"No platform.yaml found at {search}")
            UI.muted("Run 'otaman scan' first (without --update) to create one.")
            return 1
        print(f"Re-scanning {C.BOLD}{resolved}{C.RESET} and merging with existing config...\n")
    else:
        UI.header("Otaman Scan")
        print(f"Scanning {C.BOLD}{resolved}{C.RESET} ...\n")

    # Detect already-scanned project (skip when --update opted in)
    if not update:
        existing = _find_existing_otaman_project(resolved)
        if existing:
            UI.warn(f"This directory already looks like an otaman project: {existing}")
            UI.muted("Existing platform.yaml found. Options:")
            UI.muted(f"  otaman scan {scan_path} --update --otaman-dir {existing}    # re-scan + merge")
            if dry_run:
                UI.muted("Continuing in dry-run mode for inspection only — no changes will be written.")
                print()
            else:
                UI.muted("  otaman scan {0} --update --dry-run --otaman-dir {1}    # preview a merge".format(scan_path, existing))
                return 1

    # Determine otaman folder
    if maestro_dir:
        maestro_path = Path(maestro_dir).resolve()
    else:
        # Project name: --name override, else sanitised scan-folder basename
        if project_name_override:
            project_name = project_name_override.lower().replace(" ", "-").replace("_", "-")
            project_name = "".join(c for c in project_name if c.isalnum() or c == "-") or "my-platform"
        else:
            project_name = resolved.name.lower().replace(" ", "-").replace("_", "-")
            project_name = "".join(c for c in project_name if c.isalnum() or c == "-") or "my-platform"
        # Default folder name: {project}-otaman/ (legacy: was {project}-maestro/ pre-rebrand;
        # back-compat handled by 2B.1-B existing-project detection).
        maestro_path = resolved / f"{project_name}-otaman"

    if not update:
        print(f"Otaman folder: {C.BOLD}{maestro_path}{C.RESET}\n")

        if dry_run:
            # Report what WOULD happen, no mutations
            if not maestro_path.exists():
                UI.muted(f"  [dry-run] would mkdir {maestro_path}/")
            else:
                UI.muted(f"  [dry-run] otaman folder already exists at {maestro_path}/")
            if not (maestro_path / ".git").exists():
                UI.muted(f"  [dry-run] would `git init` in {maestro_path.name}/")
            if not (maestro_path / ".gitignore").exists():
                UI.muted(f"  [dry-run] would create .gitignore (.agents/bus,blocked,queue,sessions,current-agent)")
            print()
        else:
            # Create otaman folder + git init
            maestro_path.mkdir(parents=True, exist_ok=True)
            git_dir = maestro_path / ".git"
            if not git_dir.exists():
                subprocess.run(["git", "init", str(maestro_path)], capture_output=True)
                UI.ok(f"Created {maestro_path.name}/ with git init")

            # Generate .gitignore
            gitignore_path = maestro_path / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text(
                    "# Runtime artifacts (not versioned)\n"
                    ".agents/bus/\n"
                    ".agents/blocked/\n"
                    ".agents/queue/\n"
                    ".agents/sessions/\n"
                    ".agents/current-agent\n",
                    encoding="utf-8",
                )
                UI.ok("Created .gitignore")
                print()

    script_args = [scan_path, "--maestro-dir", str(maestro_path)]  # legacy: plugin script arg
    if update:
        script_args.append("--update")
    if dry_run:
        script_args.append("--dry-run")

    result = run_script("discover-repos.py", *script_args, capture=True, stream_stderr=True)

    # Try to parse stdout as JSON regardless of returncode — discover_repos
    # returns rc=1 for no-repos (legitimate empty result), and we want to
    # surface friendly hints rather than the raw JSON dump.
    import json
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        # Stdout was NOT valid JSON — real error from the subprocess
        if result.returncode != 0:
            UI.error(result.stderr or result.stdout)
            return result.returncode
        print(result.stdout)
        return 0

    # Pretty-print the report
    repos = report.get("repos", [])
    if not repos:
        for w in report.get("warnings", []):
            UI.warn(w)
        UI.muted("")
        UI.muted("No git repositories found under the scan path. Common causes:")
        UI.muted("  - Wrong directory? Try: otaman scan /path/to/your/project")
        UI.muted("  - Repos not yet cloned locally? Clone them as siblings first.")
        UI.muted("  - Repos behind a non-default depth? Scan walks 2 levels by default.")
        UI.muted("  - All git repos got skipped as otaman folders? They are.")
        UI.muted("")
        UI.muted("If your project IS already an otaman project, use --update to re-scan.")
        return 1

    print(f"Found {C.BOLD}{len(repos)}{C.RESET} repositories:\n")

    headers = ["Repo", "Tech", "Suggested Owner", "Flags"]
    rows = []
    for repo in repos:
        name = repo["name"]
        tech = ", ".join(repo.get("tech", [])) or "-"
        owner = repo.get("suggested_owner", "-")
        flags = []
        if not repo.get("has_git", True):
            flags.append(UI.badge("no-git", C.YELLOW))
        if repo.get("has_claude_md"):
            flags.append("CLAUDE.md")
        if repo.get("has_existing_hooks"):
            flags.append("hooks")
        if repo.get("is_monorepo"):
            flags.append(UI.badge("monorepo", C.YELLOW))
        flag_str = ", ".join(flags) if flags else ""
        rows.append([UI.repo(name), tech, UI.agent(owner), flag_str])
    UI.table(headers, rows, col_widths=[25, 30, 20, 15])

    # Monorepo hint: surface advice if any repo flagged as such
    monorepo_repos = [r for r in repos if r.get("is_monorepo")]
    if monorepo_repos:
        print()
        UI.warn(f"{len(monorepo_repos)} repo(s) detected as monorepos (multiple package.json/pyproject at nested depths).")
        UI.muted("  Options for monorepos:")
        UI.muted("  - Treat each as one otaman repo with multiple tech tags (current default).")
        UI.muted("  - OR split into separate sub-repos before running otaman init (cleaner ownership).")
        UI.muted("  - See: https://github.com/inprimex/otaman-meta (polyrepo-structure.md) for guidance.")

    # OpenSpec detection
    openspec = report.get("openspec")
    if openspec:
        UI.ok(f"OpenSpec detected in {openspec.get('repo', openspec.get('path'))}")
        UI.muted("Spec operations will delegate to /opsx: commands")
    else:
        UI.muted("No OpenSpec detected - using fallback proposal workflow")

    # Contracts
    contracts = report.get("contracts_path")
    if contracts:
        UI.info(f"API contracts found: {contracts}")

    # Warnings
    for w in report.get("warnings", []):
        print()
        UI.warn(w)

    # Update mode: show changes summary
    changes = report.get("changes")
    if changes:
        UI.subheader("Changes vs existing config:")
        if changes.get("added"):
            for name in changes["added"]:
                UI.bullet(f"{name} (new repo, needs owner assignment)", icon="+", color=C.GREEN)
        if changes.get("updated"):
            for item in changes["updated"]:
                fields = ", ".join(item["fields"])
                UI.bullet(f"{item['name']} (updated: {fields})", icon="~", color=C.YELLOW)
        if changes.get("removed"):
            for name in changes["removed"]:
                UI.bullet(f"{name} (in config but not found on disk - kept, verify manually)", icon="?", color=C.RED)
        if changes.get("unchanged"):
            count = len(changes["unchanged"])
            UI.muted(f"{count} repo(s) unchanged")

        out = report.get("update_path", "")
        if out:
            UI.ok(f"Updated config written to: {out}")
            UI.muted("Review the changes, then:")
            UI.muted("  mv platform.yaml.updated platform.yaml")
            UI.muted("  otaman init")
    else:
        draft = report.get("draft_path", "")
        if draft and dry_run:
            UI.muted(f"[dry-run] would write draft config to: {draft}")
            UI.muted("Re-run without --dry-run to apply.")
        elif draft:
            # otaman-scan-ux-hardening (2026-06-03): post-process the draft to
            # fill the gaps discover-repos can't address on its own — missing
            # specs repo, missing launcher block, no OpenSpec scaffold.
            try:
                from otaman_cli.onboard.post_scan import run as _post_scan_run
                draft_path = Path(draft)
                m_dir_str = report.get("maestro_dir", "")
                otaman_dir_for_post = Path(m_dir_str) if m_dir_str else draft_path.parent
                program_slug_for_post = (
                    otaman_dir_for_post.name.removesuffix("-otaman").removesuffix("-maestro")
                    or resolved.name
                )
                _ps_result = _post_scan_run(
                    draft_path=draft_path,
                    scan_root=resolved,
                    otaman_dir=otaman_dir_for_post,
                    program_slug=program_slug_for_post,
                )
                if _ps_result.specs_repo_created:
                    UI.ok(f"Created specs repo: {_ps_result.specs_repo_created}")
                if _ps_result.specs_repo_lifted:
                    UI.ok(f"Lifted existing specs repo: {_ps_result.specs_repo_lifted}")
                if _ps_result.openspec_scaffolded:
                    UI.ok(f"Scaffolded OpenSpec: {_ps_result.openspec_scaffolded}")
                if _ps_result.launcher_block_added:
                    UI.ok("Added launcher block to platform.yaml.draft (review and customise)")
                for s in _ps_result.skipped:
                    UI.muted(f"  skipped: {s}")
            except Exception as _post_exc:
                UI.warn(f"Post-scan UX hardening skipped: {_post_exc}")

            UI.ok(f"Draft config written to: {draft}")
            m_dir = report.get("maestro_dir", "")
            if m_dir:
                UI.muted("Next steps:")
                UI.muted(f"  1. cd {m_dir}")
                UI.muted("  2. Review platform.yaml.draft, adjust owner assignments")
                UI.muted("  3. mv platform.yaml.draft platform.yaml")
                UI.muted("  4. otaman init")
            else:
                UI.muted("Review the draft, adjust owner assignments, then rename to platform.yaml")
                UI.muted("and run: otaman init")

    return 0


register(CommandSpec(
    name="scan",
    handler=cmd_scan,
    help="Scan repos, create otaman folder with draft config",
))
