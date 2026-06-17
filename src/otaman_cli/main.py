#!/usr/bin/env python3
"""Otaman CLI - human-facing wrapper for multi-repo agent orchestration.

Usage:
    otaman scan [<path>] [--dry-run] [--name N] [--otaman-dir P]   Scan repos + create otaman folder
    otaman init [<config>] [--dry-run] [--skip-doctor] [--update] [--shell]   Initialize an otaman project. Creates platform.yaml if none exists.
    otaman migrate [<name>]           Migrate to dedicated otaman folder
    otaman launcher <target>          Scaffold a launcher folder with connection profiles
    otaman install-cli [--apply]      Put `otaman` on PATH (symlink on POSIX, setx on Windows)
    otaman git-host [detect|list|check|add|pr|post-review]  Git host integration (PRs, comments)
    otaman models [--diff|--suggest]  Show model/effort defaults; --diff vs platform.yaml overrides
    otaman clone <source>             Clone all repos + init + doctor
    otaman doctor [--org <name>]      Check environment readiness; --org adds CE harness check
    otaman status [--blocked] [--agent NAME] [--json]   Fleet status (or --repos for cross-repo view)
    otaman set-status <state>         Update this agent's status (working|blocked|waiting|idle)
    otaman watchdog <action>          Query/control the runner watchdog (status|start|pause|resume)
    otaman check [<agent>]            Check messages for an agent
    otaman ack <msg> [--read|--resolved]   Acknowledge a bus message
    otaman cleanup [--dry-run]        Archive old bus messages
    otaman propose <title> [-d desc]  Propose a spec change (pending approval)
    otaman complete <change> --tasks T  Report task completion, update tasks.md
    otaman approve [list|approve|reject] [<id>]  Review/approve spec-change-requests
    otaman assign [<tasks.md>]        Map OpenSpec tasks to repo owners
    otaman review [--reviewer R]      Trigger observer review
    otaman validate [<config>]        Validate platform.yaml
    otaman validate-messages [<file>] Validate bus message files
    otaman compliance [--format F]    Generate compliance audit report
    otaman blocked --list              List blocked tasks for current agent
    otaman blocked --clear <slug>     Remove a blocked task entry
    otaman set-agent <name>           DEPRECATED — use 'export OTAMAN_AGENT=<name>' instead
    otaman presale [name domain client]  Initialize pre-sale estimation project
    otaman retrospective [project-code]  Post-project retrospective
    otaman onboard <sub> [args]        Onboard users / projects (add-user, list-users, whoami, doctor)
    otaman pm <init|configure|status> [args]  PM tool sync (Easy8 / Redmine)
    otaman mcp-config --bridge-url URL  Emit Claude Code .mcp.json for the bridge
    otaman session spawn --agent A --repo R  Spawn a session under the logged-in user
    otaman help                        Show this help

Options:
    -h, --help       Show help
    -v, --version    Show version
    --format FORMAT  Output format: json | markdown (for compliance)
    --reviewer NAME  Reviewer: cto | spec | security | all (for review)
    -d, --desc TEXT  Description (for propose)
    --dry-run        Preview cleanup without making changes
    --read           Mark message as read (for ack)
    --resolved       Mark message as resolved (for ack, default)
    --all            Ack all pending messages
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
# After Stage 4E carve, run_script() dispatches to scripts that live across
# multiple sibling repos via "python -m <module>". SCRIPT_MAP records the
# legacy filename → new module name lookup; PYTHONPATH for the subprocess
# is built from the dispatcher's own location.
SCRIPT_MAP = {
    # otaman-core: validators
    "validate-platform.py": "otaman_core.validate_platform",
    "validate-message.py": "otaman_core.validate_message",
    # otaman-cli: this very package
    "doctor.py": "otaman_cli.doctor",
    "cleanup-bus.py": "otaman_cli.cleanup_bus",
    "compliance-report.py": "otaman_cli.compliance_report",
    "status-report.py": "otaman_cli.status_report",
    "models-report.py": "otaman_cli.models_report",
    "accounts.py": "otaman_cli.accounts",
    "install_cli.py": "otaman_cli.install_cli",
    # otaman-bridge: server daemon scripts
    "ping.py": "otaman_bridge.ping",
    "afk.py": "otaman_bridge.afk",
    "bridge/cli.py": "otaman_bridge.cli",
    # otaman-plugin: proper Python package
    "actualize-tasks.py": "otaman_plugin.actualize_tasks",
    "clone-project.py": "otaman_plugin.clone_project",
    "discover-repos.py": "otaman_plugin.discover_repos",
    "generate-agent-config.py": "otaman_plugin.generate_agent_config",
    "init-presale.py": "otaman_plugin.init_presale",
    "map-tasks.py": "otaman_plugin.map_tasks",
    "scaffold-launcher.py": "otaman_plugin.scaffold_launcher",
}


# _ensure_sibling_paths removed: cross-repo imports now use proper package resolution
# (otaman-cli depends on otaman-core, otaman-bridge, otaman-plugin via pyproject deps)

# Fix Windows console encoding for Unicode output
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Colors for terminal output
class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        for attr in ("BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "CYAN", "RESET"):
            setattr(cls, attr, "")


# Disable colors if not a terminal or on Windows without ANSI support
if not sys.stdout.isatty() or (sys.platform == "win32" and "WT_SESSION" not in os.environ and "TERM" not in os.environ):
    C.disable()


# ---------------------------------------------------------------------------
# UI — standardized output templates
# ---------------------------------------------------------------------------

class UI:
    """Consistent output formatting for all otaman CLI commands.

    Semantic methods:
        error/warn/ok/info/muted — status messages
        blocked/action — workflow state
        header/subheader — section dividers
        table/kv/bullet — structured data
        agent/repo/path/priority — inline formatters
        badge — inline status tag
        confirm — yes/no prompt
    """

    @staticmethod
    def error(msg: str) -> None:
        print(f"  {C.RED}[!]{C.RESET} {msg}")

    @staticmethod
    def warn(msg: str) -> None:
        print(f"  {C.YELLOW}[!]{C.RESET} {msg}")

    @staticmethod
    def ok(msg: str) -> None:
        print(f"  {C.GREEN}[+]{C.RESET} {msg}")

    @staticmethod
    def info(msg: str) -> None:
        print(f"  {C.CYAN}[i]{C.RESET} {msg}")

    @staticmethod
    def muted(msg: str) -> None:
        print(f"      {C.DIM}{msg}{C.RESET}")

    @staticmethod
    def action(msg: str) -> None:
        print(f"  {C.YELLOW}->{C.RESET} {msg}")

    @staticmethod
    def blocked(msg: str) -> None:
        print(f"  {C.RED}[X]{C.RESET} {C.BOLD}{msg}{C.RESET}")

    @staticmethod
    def header(title: str) -> None:
        w = max(len(title) + 4, 50)
        print(f"\n  {C.BOLD}{C.CYAN}{'─' * w}{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}  {title}{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'─' * w}{C.RESET}\n")

    @staticmethod
    def subheader(title: str) -> None:
        print(f"\n  {C.BOLD}{title}{C.RESET}")

    @staticmethod
    def kv(key: str, value: str, value_color: str = "") -> None:
        vc = value_color or ""
        rst = C.RESET if vc else ""
        print(f"  {C.DIM}{key}:{C.RESET} {vc}{value}{rst}")

    @staticmethod
    def bullet(text: str, icon: str = "*", color: str = "") -> None:
        c = color or C.YELLOW
        print(f"    {c}{icon}{C.RESET} {text}")

    @staticmethod
    def table(headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None) -> None:
        if not col_widths:
            col_widths = []
            for i, h in enumerate(headers):
                max_w = len(h)
                for row in rows:
                    if i < len(row):
                        clean = re.sub(r"\033\[[0-9;]*m", "", str(row[i]))
                        max_w = max(max_w, len(clean))
                col_widths.append(min(max_w + 2, 40))

        hdr = "  "
        sep = "  "
        for i, h in enumerate(headers):
            w = col_widths[i] if i < len(col_widths) else 15
            hdr += f"{C.BOLD}{h:<{w}}{C.RESET}"
            sep += f"{'─' * (w - 1)} "
        print(hdr)
        print(f"  {C.DIM}{sep.strip()}{C.RESET}")

        for row in rows:
            line = "  "
            for i, cell in enumerate(row):
                w = col_widths[i] if i < len(col_widths) else 15
                clean = re.sub(r"\033\[[0-9;]*m", "", str(cell))
                padding = w - len(clean)
                line += f"{cell}{' ' * max(padding, 1)}"
            print(line)

    # --- Inline formatters (return strings, don't print) ---

    @staticmethod
    def agent(name: str) -> str:
        return f"{C.GREEN}{name}{C.RESET}"

    @staticmethod
    def repo(name: str) -> str:
        return f"{C.BOLD}{C.CYAN}{name}{C.RESET}"

    @staticmethod
    def path(p: str) -> str:
        return f"{C.DIM}{p}{C.RESET}"

    @staticmethod
    def priority(p: str) -> str:
        colors = {"urgent": C.RED + C.BOLD, "high": C.RED, "normal": "", "low": C.DIM}
        c = colors.get(p, "")
        return f"{c}{p}{C.RESET}" if c else p

    @staticmethod
    def badge(text: str, color: str = "") -> str:
        c = color or C.GREEN
        return f"{c}[{text}]{C.RESET}"

    @staticmethod
    def confirm(question: str) -> bool:
        resp = input(f"  {C.BOLD}? {question}{C.RESET} [Y/n] ").strip().lower()
        return resp != "n"


# find_project_root + resolve_agent_identity moved to identity.py (Stage 2A);
# re-export for backward compat with the rest of this module.
from otaman_cli.identity import find_project_root, resolve_agent_identity


def run_script(name: str, *args: str, capture: bool = False, stream_stderr: bool = False):
    """Run an otaman script via direct Python import (Roman option C).

    SCRIPT_MAP records legacy filename → fully-qualified module name. The
    target module is imported in-process; its main(argv) is called; the
    return value is shaped to match subprocess.CompletedProcess so existing
    call sites in this dispatcher don't need to change.

    Args:
        capture: Capture stdout (for JSON parsing).
        stream_stderr: If True and capture=True, let stderr pass through.

    Returns:
        SimpleNamespace with .returncode, .stdout (str if capture else None),
        .stderr (always None — pure imports don't separate stderr).
    """
    if name not in SCRIPT_MAP:
        UI.error(f"Script not in SCRIPT_MAP: {name}")
        sys.exit(2)
    module_name = SCRIPT_MAP[name]

    import importlib
    import io
    import contextlib
    from types import SimpleNamespace

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        UI.error(f"Failed to import {module_name}: {e}")
        return SimpleNamespace(returncode=2, stdout="", stderr=None)

    main_fn = getattr(module, "main", None)
    if main_fn is None:
        UI.error(f"Module {module_name} has no main() entry point")
        return SimpleNamespace(returncode=2, stdout="", stderr=None)

    argv = list(args)

    def _invoke() -> int:
        # Inspect main() signature: scripts either accept argv as a list
        # or take no args and read sys.argv directly.
        import inspect
        try:
            sig = inspect.signature(main_fn)
            takes_argv = len(sig.parameters) >= 1
        except (ValueError, TypeError):
            takes_argv = True  # safer default

        # Save and set sys.argv for scripts that read it directly.
        saved_argv = sys.argv
        sys.argv = [name, *argv]
        try:
            if takes_argv:
                rc = main_fn(argv)
            else:
                rc = main_fn()
        except SystemExit as e:
            sys.argv = saved_argv
            return int(e.code) if e.code is not None else 0
        finally:
            sys.argv = saved_argv
        return int(rc) if rc is not None else 0

    if capture:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _invoke()
        return SimpleNamespace(returncode=rc, stdout=buf.getvalue(), stderr=None)

    rc = _invoke()
    return SimpleNamespace(returncode=rc, stdout=None, stderr=None)



# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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


def cmd_scan(args: list[str], update: bool = False, maestro_dir: str | None = None, dry_run: bool = False, project_name_override: str | None = None) -> int:
    """Scan repos and generate draft platform.yaml in a dedicated otaman folder."""
    scan_path = args[0] if args else "."
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



def _ensure_settings_default_mode(root: Path, config: dict) -> None:
    """Ensure defaultMode: auto is in each repo's .claude/settings.local.json.

    Without this, a per-repo settings.local.json with only an allow list implicitly
    sets everything else to 'ask', overriding the user's global auto mode.
    """
    import json as _json
    for repo in config.get("repos", []):
        repo_path_rel = repo.get("path", "")
        if not repo_path_rel:
            continue
        repo_dir = (root / repo_path_rel).resolve()
        if not repo_dir.is_dir():
            continue
        settings_path = repo_dir / ".claude" / "settings.local.json"
        if not settings_path.is_file():
            continue
        try:
            data = _json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        perms = data.setdefault("permissions", {})
        if perms.get("defaultMode") == "auto":
            continue
        perms["defaultMode"] = "auto"
        settings_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _inject_agent_env_into_command(command: str, owner: str) -> str:
    """Prepend OTAMAN_AGENT=<owner> before the 'claude' invocation in a launch command.

    Idempotent: updates an existing OTAMAN_AGENT=<old> if present.
    Handles the common pattern: 'source ... && claude ...' or bare 'claude ...'.
    """
    if not owner:
        return command
    import re as _re
    # Match optional existing OTAMAN_AGENT=<something> + whitespace preceding 'claude'
    pattern = _re.compile(r"(OTAMAN_AGENT=\S+\s+)?claude\b")
    replacement = f"OTAMAN_AGENT={owner} claude"
    new_cmd, n = pattern.subn(replacement, command, count=1)
    if n == 0:
        return command
    return new_cmd


def _cmd_init_shell() -> int:
    """Install the otaman-agent shell function into ~/.bashrc / ~/.zshrc (D3a)."""
    import os as _os

    shell_bin = _os.environ.get("SHELL", "")
    if "zsh" in shell_bin:
        rc_file = Path.home() / ".zshrc"
    else:
        rc_file = Path.home() / ".bashrc"

    MARKER_START = "# >>> otaman-agent: added by `otaman init --shell` >>>"
    MARKER_END = "# <<< otaman-agent <<<"
    SNIPPET = (
        MARKER_START + "\n"
        "otaman-agent() {\n"
        "  if [ -z \"$1\" ]; then\n"
        "    echo \"OTAMAN_AGENT=${OTAMAN_AGENT:-(unset; resolving from .otaman or current-agent)}\"\n"
        "  else\n"
        "    export OTAMAN_AGENT=\"$1\"\n"
        "  fi\n"
        "}\n"
        + MARKER_END + "\n"
    )

    UI.header("Otaman Init --shell")
    UI.info(f"Shell config file: {rc_file}")
    print()

    # Check idempotency
    if rc_file.is_file():
        existing = rc_file.read_text(encoding="utf-8")
        if MARKER_START in existing:
            UI.ok("otaman-agent function already installed (idempotent).")
            return 0

    # Consent prompt
    try:
        answer = input(f"Append otaman-agent function to {rc_file}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        UI.muted("Aborted.")
        return 1

    if answer not in ("y", "yes"):
        UI.muted("Aborted — no changes made.")
        return 1

    with open(rc_file, "a", encoding="utf-8") as fh:
        fh.write("\n" + SNIPPET)

    UI.ok(f"Installed otaman-agent function in {rc_file}")
    UI.muted("Reload your shell or run: source " + str(rc_file))
    return 0


def _detect_strategic_agents(doc: dict) -> list[str]:
    """outcome-proposal-routing task 3.3 — detect CPO + cofounder agents.

    Two signals, in priority order (per spec):
      1. Explicit `role:` field on an `agents:[]` entry —
         `role: cpo` is the CPO; `role: cofounder` is the cofounder.
      2. Repo name suffix on `repos:[]` — `-business` repo's owner is the
         CPO; `-strategy` repo's owner is the cofounder.

    Returns ordered, deduplicated list: CPO first (if any), then cofounder
    (if any).  An agent that fills both roles appears once.
    """
    cpo_agent: str | None = None
    cofounder_agent: str | None = None

    # Pass 1 — explicit `role:` on agents[] entries
    agents_field = doc.get("agents")
    if isinstance(agents_field, list):
        for a in agents_field:
            if not isinstance(a, dict):
                continue
            name = a.get("name")
            role = a.get("role")
            if not isinstance(name, str) or not name:
                continue
            if role == "cpo" and cpo_agent is None:
                cpo_agent = name
            elif role == "cofounder" and cofounder_agent is None:
                cofounder_agent = name

    # Pass 2 — repo-name suffix (only fills gaps left by pass 1)
    for r in (doc.get("repos") or []):
        if not isinstance(r, dict):
            continue
        rname = r.get("name") or ""
        owner = r.get("owner")
        if not isinstance(rname, str) or not isinstance(owner, str) or not owner:
            continue
        if cpo_agent is None and rname.endswith("-business"):
            cpo_agent = owner
        elif cofounder_agent is None and rname.endswith("-strategy"):
            cofounder_agent = owner

    out: list[str] = []
    if cpo_agent:
        out.append(cpo_agent)
    if cofounder_agent and cofounder_agent not in out:
        out.append(cofounder_agent)
    return out


def _ensure_routing_rules(platform_yaml: Path) -> int:
    """bus-cc-routing task 2.5 — ensure `bus.routing_rules` defaults exist.

    Idempotent: returns 0 if no change was needed, 1 if any rule was added.

    Defaults:
        - `to: human` → cc: [spec-agent]                              (always)
        - `to: human, priority: [high, urgent]` → cc: [cpo-agent]    (only when
          cpo-agent owns any repo, or appears in an `agents:` list)

    Implementation note: uses ruamel.yaml for round-trip preservation when an
    `bus:` block already exists.  When the block is entirely absent, appends
    a plain-text block at end-of-file so existing comments/order/quoting are
    untouched.  Trade-off: when adding a single missing rule to an existing
    block we accept ruamel.yaml's modest re-flow.
    """
    if not platform_yaml.is_file():
        return 0
    text = platform_yaml.read_text(encoding="utf-8")
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(text) or {}
    except Exception:
        return 0
    if not isinstance(doc, dict):
        return 0

    # Determine whether cpo-agent is in scope (repos[].owner or agents[].name)
    has_cpo_agent = False
    for r in (doc.get("repos") or []):
        if isinstance(r, dict) and r.get("owner") == "cpo-agent":
            has_cpo_agent = True
            break
    if not has_cpo_agent and isinstance(doc.get("agents"), list):
        for a in doc["agents"]:
            if isinstance(a, dict) and a.get("name") == "cpo-agent":
                has_cpo_agent = True
                break

    spec_rule = {"when": {"to": "human"}, "cc": ["spec-agent"]}
    cpo_rule = {"when": {"to": "human", "priority": ["high", "urgent"]}, "cc": ["cpo-agent"]}
    desired: list[dict] = [spec_rule]
    if has_cpo_agent:
        desired.append(cpo_rule)

    bus_block = doc.get("bus") if isinstance(doc.get("bus"), dict) else {}
    existing_rules = bus_block.get("routing_rules") or []

    def _normalize(rule: object) -> tuple:
        """Canonical key for dedup: (sorted when items, sorted cc)."""
        if not isinstance(rule, dict):
            return ()
        when = rule.get("when") or {}
        cc = rule.get("cc") or []
        when_key = tuple(sorted(
            (k, tuple(v) if isinstance(v, list) else v) for k, v in when.items()
        )) if isinstance(when, dict) else ()
        cc_key = tuple(cc) if isinstance(cc, list) else ()
        return (when_key, cc_key)

    existing_keys = {_normalize(r) for r in existing_rules}
    missing = [r for r in desired if _normalize(r) not in existing_keys]

    # outcome-proposal-routing task 3.4 — upsert rule by `when.type` key.
    # Strategic agents (CPO, cofounder) trigger a `when: {type:
    # outcome-proposal}` rule.  Upsert semantics: if a rule with that
    # `when.type` already exists, REPLACE its `cc:` list to match currently
    # detected agents; otherwise append.  No strategic agents → skip
    # silently (do NOT remove an existing rule).
    strategic_agents = _detect_strategic_agents(doc)
    outcome_existing_idx: int | None = None
    outcome_existing_cc: list = []
    for i, r in enumerate(existing_rules):
        if isinstance(r, dict) and isinstance(r.get("when"), dict) \
                and r["when"].get("type") == "outcome-proposal":
            outcome_existing_idx = i
            outcome_existing_cc = list(r.get("cc") or [])
            break

    outcome_action: str | None = None
    if strategic_agents:
        if outcome_existing_idx is None:
            outcome_action = "append"
        elif outcome_existing_cc != strategic_agents:
            outcome_action = "replace"
        # else: rule exists and cc matches — no-op

    if not missing and outcome_action is None:
        return 0

    # Path A — no `bus:` block at all: append plain-text YAML at EOF for
    # zero formatting impact on the rest of the file.  Includes the
    # outcome-proposal rule when present (always an "append" here since
    # the block didn't exist).
    if "bus" not in doc:
        all_to_append = list(missing)
        if outcome_action == "append":
            all_to_append.append({
                "when": {"type": "outcome-proposal"},
                "cc": list(strategic_agents),
            })
        appended_lines = ["", "# bus-cc-routing — default routing rules generated by `otaman init`", "bus:", "  routing_rules:"]
        for r in all_to_append:
            when_keys = list(r["when"].items())
            appended_lines.append("    - when:")
            for k, v in when_keys:
                if isinstance(v, list):
                    appended_lines.append(f"        {k}: [{', '.join(str(x) for x in v)}]")
                else:
                    appended_lines.append(f"        {k}: {v}")
            appended_lines.append(f"      cc: [{', '.join(r['cc'])}]")
        suffix = "\n".join(appended_lines).rstrip() + "\n"
        if not text.endswith("\n"):
            text += "\n"
        platform_yaml.write_text(text + suffix, encoding="utf-8")
        return 1

    # Path B — `bus:` exists, need to add missing rule(s) and/or upsert
    # the outcome-proposal rule.  Round-trip via ruamel.yaml.  Acceptable
    # re-flow tradeoff (rare path).
    try:
        from ruamel.yaml import YAML as _RuamelYAML
        import io as _io
        rt = _RuamelYAML()
        rt.preserve_quotes = True
        rt.indent(mapping=2, sequence=4, offset=2)
        rt.width = 120
        doc_rt = rt.load(text) or {}
        if "bus" not in doc_rt:
            doc_rt["bus"] = {}
        if not isinstance(doc_rt["bus"].get("routing_rules"), list):
            doc_rt["bus"]["routing_rules"] = []
        rules_rt = doc_rt["bus"]["routing_rules"]
        # Append the bus-cc-routing defaults that were missing
        for r in missing:
            rules_rt.append(r)
        # Apply outcome-proposal upsert
        if outcome_action == "append":
            rules_rt.append({
                "when": {"type": "outcome-proposal"},
                "cc": list(strategic_agents),
            })
        elif outcome_action == "replace" and outcome_existing_idx is not None:
            # Replace the cc: list on the existing rule, leaving when:
            # intact (preserves any extra fields a future spec might add).
            rules_rt[outcome_existing_idx]["cc"] = list(strategic_agents)
        out = _io.StringIO()
        rt.dump(doc_rt, out)
        platform_yaml.write_text(out.getvalue(), encoding="utf-8")
        return 1
    except Exception:
        return 0


def _cmd_init_update() -> int:
    """Patch .otaman agent: fields + regenerate launch commands across all repos (--update, D5)."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        UI.error(f"platform.yaml not found at {platform_yaml}")
        return 2

    try:
        import yaml as _yaml
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as e:
        UI.error(f"Failed to read platform.yaml: {e}")
        return 2

    UI.header("Otaman Init --update")
    updated = 0
    skipped = 0
    launch_updated = 0

    for repo in config.get("repos", []):
        repo_path_rel = repo.get("path", "")
        owner = repo.get("owner", "")
        name = repo.get("name", repo_path_rel)
        if not repo_path_rel:
            continue
        repo_dir = (root / repo_path_rel).resolve()
        if not repo_dir.is_dir():
            UI.muted(f"  skip {name}: directory not found")
            skipped += 1
            continue

        # Patch .otaman agent: field
        marker = repo_dir / ".otaman"
        if marker.is_file():
            existing = marker.read_text(encoding="utf-8")
        else:
            import os as _os
            rel = _os.path.relpath(root.resolve(), repo_dir)
            rel_posix = Path(rel).as_posix()
            existing = "# Path to otaman folder" + chr(10) + rel_posix + chr(10)

        lines_c = existing.splitlines()
        has_agent = any(l.strip().startswith("agent:") for l in lines_c)
        if has_agent:
            new_l = []
            for l in lines_c:
                if l.strip().startswith("agent:") and owner:
                    new_l.append("agent: " + owner)
                else:
                    new_l.append(l)
            new_content = chr(10).join(new_l) + chr(10)
        else:
            agent_line = ("agent: " + owner + chr(10)) if owner else ""
            new_content = existing.rstrip(chr(10)) + chr(10) + agent_line

        marker.write_text(new_content, encoding="utf-8")
        UI.ok(name + "/.otaman updated" + ((" (agent: " + owner + ")") if owner else ""))
        updated += 1

        # Count repos whose launch commands would change (D4).
        # Mutation happens below via in-place text patching, not by mutating
        # the parsed `config` dict — yaml.dump would alphabetize keys, drop
        # comments, and break downstream parsers (notably the launcher).
        if owner and isinstance(repo.get("launch"), dict):
            cmds = repo["launch"].get("commands", [])
            if any(_inject_agent_env_into_command(c, owner) != c for c in cmds):
                launch_updated += 1
                UI.muted(f"  {name}: launch commands will be updated with OTAMAN_AGENT={owner}")

    # Write back platform.yaml via in-place text patch so original key order,
    # comments, and quoting style are preserved. We walk the raw lines and
    # track current repo's owner via `  owner: <name>` markers; any line in
    # the same block containing `claude` gets the OTAMAN_AGENT prefix applied.
    if launch_updated > 0:
        try:
            original_text = platform_yaml.read_text(encoding="utf-8")
            owner_line_pat = re.compile(r"^\s+owner:\s*(\S+)")
            current_owner = None
            new_lines = []
            for line in original_text.splitlines(keepends=True):
                m = owner_line_pat.match(line)
                if m:
                    current_owner = m.group(1)
                if current_owner and "claude" in line:
                    line = _inject_agent_env_into_command(line, current_owner)
                new_lines.append(line)
            platform_yaml.write_text("".join(new_lines), encoding="utf-8")
            UI.ok(f"platform.yaml updated ({launch_updated} repo(s) launch commands patched)")
        except Exception as e:
            UI.warn(f"Failed to write platform.yaml: {e}")

    meta_marker = root / ".otaman"
    if meta_marker.is_file():
        existing = meta_marker.read_text(encoding="utf-8")
        has_agent = any(l.strip().startswith("agent:") for l in existing.splitlines())
        if not has_agent:
            meta_marker.write_text(existing.rstrip(chr(10)) + chr(10) + "agent: human" + chr(10), encoding="utf-8")
            UI.ok("otaman-meta/.otaman updated (agent: human)")
            updated += 1
        else:
            UI.muted("otaman-meta/.otaman already has agent: field")
    elif meta_marker.is_dir():
        # Directory-shape .otaman (otaman-meta legacy case): write .otaman/agent
        agent_file = meta_marker / "agent"
        agent_file.write_text("human" + chr(10), encoding="utf-8")
        UI.ok("otaman-meta/.otaman/agent written (agent: human)")
        updated += 1
    else:
        UI.muted("otaman-meta/.otaman not found")

    # Ensure defaultMode: auto in each repo's settings.local.json
    _ensure_settings_default_mode(root, config)

    # bus-cc-routing task 2.5 — ensure routing_rules defaults exist in platform.yaml
    if _ensure_routing_rules(platform_yaml):
        UI.ok("platform.yaml: bus.routing_rules defaults added")

    print()
    UI.kv("Updated", str(updated))
    UI.kv("Skipped", str(skipped))
    return 0

def _detect_sibling_git_repos(cwd: Path) -> list[Path]:
    """Find git repos one level up from *cwd* (excluding cwd itself)."""
    parent = cwd.parent
    if parent == cwd:
        return []
    repos: list[Path] = []
    try:
        for child in parent.iterdir():
            if child == cwd or not child.is_dir():
                continue
            if (child / ".git").exists():
                repos.append(child)
    except OSError:
        return []
    return repos


def _detect_scan_draft(cwd: Path) -> list[Path]:
    """Find ``<subdir>/platform.yaml.draft`` files directly under *cwd*.

    Returned paths point at the draft file itself (not the parent dir).
    Drafts are produced by ``otaman scan`` and live in the
    ``<program>-otaman/`` subdir by convention.
    """
    drafts: list[Path] = []
    try:
        for child in cwd.iterdir():
            if not child.is_dir():
                continue
            candidate = child / "platform.yaml.draft"
            if candidate.is_file():
                drafts.append(candidate)
    except OSError:
        return []
    return drafts


def _init_preflight(args: list[str]) -> int | None:
    """Detect state and route bare `otaman init` to scan or program-init wizard.

    Returns:
        - None: pre-flight skipped or passed; cmd_init should continue normally
        - int: pre-flight handled the command; cmd_init should return this value
    """
    # Only pre-flight bare `otaman init` (no explicit config arg)
    if args:
        return None

    cwd = Path.cwd()
    if (cwd / "platform.yaml").exists():
        return None  # normal init path will pick it up

    # Smart pickup: an `otaman scan` left a draft in <subdir>/platform.yaml.draft.
    # Recognise it so the user doesn't have to manually `mv` before re-running.
    drafts = _detect_scan_draft(cwd)
    if len(drafts) == 1 and sys.stdin.isatty():
        draft_path = drafts[0]
        rel = draft_path.relative_to(cwd)
        print()
        print(f"  Found scan draft: ./{rel}")
        try:
            answer = input("  Promote to platform.yaml and finalize init? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if answer in ("", "y", "yes"):
            target = draft_path.with_name("platform.yaml")
            if target.exists():
                UI.error(f"{target} already exists; refusing to overwrite.")
                UI.muted("Resolve manually or delete the existing file and re-run.")
                return 1
            draft_path.rename(target)
            UI.ok(f"Promoted draft → {target}")
            # Smart-init was invoked from the parent dir (sibling to the meta
            # folder). Downstream steps (`_cmd_init_update`, generate-agent-
            # config) use cwd-walk to find the project root; chdir into the
            # meta folder so they resolve correctly.
            os.chdir(target.parent)
            return cmd_init([str(target)])
        # else: user declined; fall through to existing options
    elif len(drafts) > 1 and sys.stdin.isatty():
        # Multiple drafts — don't auto-pick. Surface them for the user.
        print()
        UI.warn(f"Found {len(drafts)} scan drafts; not sure which to use:")
        for d in drafts:
            UI.muted(f"  {d.relative_to(cwd)}")
        UI.muted("Run `otaman init <path-to-draft>` explicitly, or move one of ")
        UI.muted("them to ./platform.yaml first.")

    # Non-TTY: print improved error and exit
    if not sys.stdin.isatty():
        UI.error("No platform.yaml found.")
        UI.muted("Interactive setup unavailable (non-TTY). Create platform.yaml first:")
        UI.muted("  otaman scan .                  — detect existing repos + draft config")
        UI.muted("  otaman init                    — interactive wizard (run from a TTY)")
        if drafts:
            UI.muted("")
            UI.muted(f"  Existing scan draft(s) found:")
            for d in drafts:
                UI.muted(f"    {d.relative_to(cwd)}")
            UI.muted("  Finalize one with: mv <draft> <draft-dir>/platform.yaml")
        return 2

    sibling_repos = _detect_sibling_git_repos(cwd)
    cwd_is_git = (cwd / ".git").exists()

    print()
    if sibling_repos:
        n = len(sibling_repos)
        suffix = "" if n == 1 else "s"
        print(f"  Found {n} git repo{suffix} in parent directory.")
        try:
            answer = input(f"  Scan and generate platform.yaml from them? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if answer in ("", "y", "yes"):
            return cmd_scan([str(cwd)])
        # User declined scan; fall through to wizard prompt

    print("  No platform.yaml found.")
    try:
        answer = input("  Start a new project with the interactive wizard? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if answer not in ("", "y", "yes"):
        UI.muted("Run `otaman init` again when you're ready.")
        return 0

    # Build a Namespace matching the program-init parser's expectations
    import argparse as _argparse
    from otaman_cli.onboard.program_init import run_program_init
    ns = _argparse.Namespace(
        program=None,
        questions_yaml=None,
        mode=None,
        dry_run=False,
        output_dir=None,
    )

    # Pass single-repo hint to the wizard via env var (task 1.3)
    prior_hint = os.environ.get("OTAMAN_INIT_CWD_IS_GIT")
    if cwd_is_git:
        os.environ["OTAMAN_INIT_CWD_IS_GIT"] = "1"
    try:
        return run_program_init(ns)
    finally:
        if prior_hint is None:
            os.environ.pop("OTAMAN_INIT_CWD_IS_GIT", None)
        else:
            os.environ["OTAMAN_INIT_CWD_IS_GIT"] = prior_hint


def cmd_init_companion_repos(rest: list[str]) -> int:
    """`otaman init companion-repos` — CE-mode in-process scaffolder.

    Flags:
        --program SLUG         Program slug (default: from platform.yaml in cwd)
        --repos KIND[,KIND]    Kinds to scaffold (business, strategy);
                               default: derived from program.processes
        --dry-run              Print plan; no filesystem writes
        --force                Re-scaffold even if target exists (with confirmation)
    """
    from otaman_cli.identity import find_project_root
    from otaman_cli.onboard.scaffold_ce import (
        ScaffoldError,
        scaffold_companion_repos_ce,
    )

    # Parse flags from the raw rest
    program = None
    repos_arg: str | None = None
    dry_run = False
    force = False
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--program" and i + 1 < len(rest):
            program = rest[i + 1]; i += 2
        elif token == "--repos" and i + 1 < len(rest):
            repos_arg = rest[i + 1]; i += 2
        elif token in ("--dry-run", "--check"):
            dry_run = True; i += 1
        elif token == "--force":
            force = True; i += 1
        else:
            i += 1

    # Locate the meta dir (where platform.yaml lives)
    root = find_project_root()
    if root is None:
        UI.error("No platform.yaml found in cwd or any ancestor.")
        UI.muted("Run `otaman init` first to create the program.")
        return 2
    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        UI.error(f"platform.yaml not found at {platform_yaml}")
        return 2

    # Read program slug + processes from platform.yaml when --program not given
    try:
        import yaml as _yaml
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        UI.error(f"Failed to read platform.yaml: {exc}")
        return 2

    if program is None:
        program = config.get("project") or config.get("program", {}).get("slug")
        if not program:
            UI.error("Could not infer program slug from platform.yaml.")
            UI.muted("Pass --program SLUG explicitly.")
            return 2

    # Derive repo kinds: explicit --repos > processes-based default
    repo_kinds: list[str] | None = None
    if repos_arg:
        repo_kinds = [r.strip() for r in repos_arg.split(",") if r.strip()]
        if repo_kinds == ["all"]:
            repo_kinds = ["business", "strategy"]

    processes_raw = config.get("processes") or {}
    if isinstance(processes_raw, dict):
        processes = [k for k, v in processes_raw.items() if v]
    elif isinstance(processes_raw, list):
        processes = list(processes_raw)
    else:
        processes = []

    program_name = (
        config.get("description")
        or config.get("project")
        or program
    )

    # --force prompt (skipped on non-TTY or --dry-run)
    if force and not dry_run and sys.stdin.isatty():
        UI.warn(f"--force will REMOVE existing companion repo directories for {program}.")
        try:
            answer = input("  Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            UI.muted("Aborted.")
            return 0

    UI.header("Scaffold companion repos" + (" (dry-run)" if dry_run else ""))
    try:
        result = scaffold_companion_repos_ce(
            program_slug=program,
            processes=processes,
            meta_dir=root,
            program_name=program_name,
            force=force,
            dry_run=dry_run,
            repo_kinds=repo_kinds,
        )
    except ScaffoldError as exc:
        UI.error(str(exc))
        return 1

    if not result.repos:
        UI.muted("No companion repos to scaffold (no qualifying processes enabled).")
        return 0

    for repo in result.created:
        marker = "would create" if dry_run else "Scaffolded"
        UI.ok(f"{marker} {repo.kind} at {repo.path} (owner: {repo.owner})")
    for repo in result.skipped:
        UI.muted(f"Skipped {repo.path} — {repo.skipped_reason}")

    if dry_run:
        UI.muted("Dry-run: no files written. Re-run without --dry-run to apply.")
    elif result.platform_yaml_updated:
        UI.ok("platform.yaml repos[] updated")
    return 0


def _scaffold_launcher_after_init(platform_yaml: Path, *, yes: bool) -> None:
    """Run the launcher scaffolder (otaman-init-dev-scaffold spec) after the
    platform-init step succeeds.  Re-runs are idempotent — overwrites the
    generated files; the gitignored local-overrides file is preserved if it
    already has user content (the wizard's example is only emitted on first
    init when no local file exists).
    """
    import yaml as _yaml
    from otaman_cli.init.generator import generate as _generate
    from otaman_cli.init.wizard import run_wizard as _run_wizard

    output_dir = platform_yaml.parent / "launcher"

    # Derive project name + agent names from the just-validated platform.yaml
    try:
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        config = {}
    project_name = str(config.get("project") or "otaman-project")
    agent_names: list[str] = []
    for r in config.get("repos") or []:
        if isinstance(r, dict) and r.get("owner"):
            owner = str(r["owner"])
            if owner and owner not in agent_names:
                agent_names.append(owner)

    # otaman-init-dev-scaffold amendment #2: detect the orchestration
    # meta-agent declared in platform.yaml (agents[*].role == "orchestration")
    # and pre-populate it as a locked enabled entry alongside spec-agent.
    # Graceful no-op when platform.yaml has no `agents:` list yet (current
    # schema state — the field is on its way via a separate spec change).
    meta_agent_name: str | None = None
    agents_field = config.get("agents")
    if isinstance(agents_field, list):
        for a in agents_field:
            if isinstance(a, dict) and a.get("role") == "orchestration":
                name = a.get("name")
                if isinstance(name, str) and name:
                    meta_agent_name = name
                    break

    print()
    if yes:
        UI.muted("Generating launcher/ (--yes; all defaults)")
    else:
        UI.header("Launcher scaffold (otaman-init-dev-scaffold)")
    settings = _run_wizard(
        project_name=project_name,
        platform_agent_names=agent_names,
        meta_agent_name=meta_agent_name,
        yes=yes,
    )

    # Preserve an existing launch-settings.local.yaml (user may have customised it);
    # the generator always overwrites the commented example.
    local_path = output_dir / "launch-settings.local.yaml"
    preserved_local: str | None = None
    if local_path.is_file():
        text = local_path.read_text(encoding="utf-8")
        # If file has any non-comment content, preserve it
        live = any(
            line.strip() and not line.strip().startswith("#")
            for line in text.splitlines()
        )
        if live:
            preserved_local = text

    result = _generate(settings, output_dir, platform_yaml_source=platform_yaml)
    if preserved_local is not None:
        local_path.write_text(preserved_local, encoding="utf-8")
        UI.muted(f"  preserved existing {local_path.name} (had user content)")

    UI.ok(f"launch-settings.yaml      {result.settings_yaml.relative_to(output_dir.parent)}")
    UI.ok(f"launch-settings.local.yaml {result.local_example.relative_to(output_dir.parent)}" if preserved_local is None else "")
    UI.ok(f"launch.sh                  {result.launch_sh.relative_to(output_dir.parent)} (chmod +x)")
    UI.ok(f"launch.ps1                 {result.launch_ps1.relative_to(output_dir.parent)}")
    if result.platform_yaml_copy is not None:
        UI.ok(f"platform.yaml              {result.platform_yaml_copy.relative_to(output_dir.parent)}")
    UI.ok(f".gitignore                 {result.gitignore.relative_to(output_dir.parent)}")


def cmd_init(args: list[str], dry_run: bool = False, skip_doctor: bool = False, update: bool = False, shell: bool = False, yes: bool = False) -> int:
    """Initialize an otaman project. Creates platform.yaml if none exists.

    With no platform.yaml in cwd, detects context and routes to:
      - `otaman scan .` if git repos are detected one level up
      - the interactive program-init wizard otherwise
    Non-TTY stdin skips routing and prints an instructional error.

    With --update: patches existing .otaman files across all platform repos
    to write the agent: <owner> field and regenerates launch commands with
    OTAMAN_AGENT=<owner> prefix.  Safe to run multiple times (idempotent).

    With --shell: installs the otaman-agent shell function into ~/.bashrc or
    ~/.zshrc after explicit consent.
    """
    # --shell mode: install shell function and exit
    if shell:
        return _cmd_init_shell()

    # --update mode: patch .otaman agent: fields across all repos and exit
    if update:
        return _cmd_init_update()

    # Pre-flight: smart-init routing when no platform.yaml present (task 1.1, 1.2)
    preflight_rc = _init_preflight(args)
    if preflight_rc is not None:
        return preflight_rc

    config = args[0] if args else "platform.yaml"
    config_path = Path(config).resolve()

    if not config_path.exists():
        UI.error(f"Config not found: {config_path}")
        UI.muted("Run 'otaman scan' first to generate a config, or copy the template.")
        return 2

    if dry_run:
        UI.header("Otaman Init (dry-run)")
    else:
        UI.header("Otaman Init")

    # Validate first.
    # ce-org-agent-bootstrap task 4.1 — accept CE-shaped platform.yaml by
    # normalizing in-memory before validation (alias agent→owner; infer
    # project from parent dir; default version=1.0).  Hints printed but
    # the on-disk file is not rewritten.
    print(f"Validating {config_path.name}...")
    norm_path, hints = _normalize_ce_platform_yaml_for_validation(config_path)
    if hints:
        for h in hints:
            UI.muted(f"  hint: {h}")
    result = run_script("validate-platform.py", str(norm_path), capture=True)
    if norm_path != config_path:
        try:
            norm_path.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        UI.error((result.stdout or "") + (result.stderr or "") or "validate failed (no output)")
        return result.returncode
    UI.ok("Valid")
    print()

    # Generate
    if dry_run:
        print("Generating agent infrastructure [dry-run]...")
    else:
        print("Generating agent infrastructure...")
    script_args = [str(config_path)]
    if dry_run:
        script_args.append("--dry-run")
    result = run_script("generate-agent-config.py", *script_args)
    if result.returncode != 0:
        return result.returncode

    if dry_run:
        print()
        UI.muted("[dry-run] No files written. Re-run without --dry-run to apply.")
        return 0

    # Write agent: fields to each repo's .otaman (task 2.5 — same logic as --update)
    print()
    print("Writing agent: fields to repo .otaman files...")
    _cmd_init_update()

    # otaman-init-dev-scaffold: generate launcher/ folder alongside platform.yaml
    # (launch-settings.yaml + launch-settings.local.yaml + launch.sh + launch.ps1
    # + .gitignore).  Prompts for connection mode + agents + tmux layout unless
    # --yes is passed.
    try:
        _scaffold_launcher_after_init(config_path, yes=yes)
    except Exception as _scaffold_exc:
        UI.warn(f"Launcher scaffold skipped: {_scaffold_exc}")

    if skip_doctor:
        print()
        UI.muted("Skipped doctor check (--skip-doctor). Run `otaman doctor` to verify environment.")
        return 0

    # Run doctor check
    print()
    return cmd_doctor([str(config_path.parent)])


def cmd_clone(args: list[str], target: str = "") -> int:
    """Clone all project repos from a otaman configuration."""
    if not args:
        UI.error("Source required (local path, git URL, or user@host:path)")
        UI.muted("Usage: otaman clone <source> [--target <dir>]")
        UI.muted("  otaman clone git@github.com:org/project-otaman.git")
        UI.muted("  otaman clone user@server:/path/to/otaman/")
        UI.muted("  otaman clone /local/path/to/platform.yaml")
        return 1

    UI.header("Otaman Clone")

    source = args[0]
    UI.kv("Source", source)
    if target:
        UI.kv("Target", target)
    print()

    script_args = [source]
    if target:
        script_args.extend(["--target", target])

    result = run_script("clone-project.py", *script_args, capture=True, stream_stderr=True)

    import json
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        if result.returncode != 0:
            UI.error(result.stderr or result.stdout or "Clone failed")
        else:
            print(result.stdout)
        return result.returncode

    # Display results
    cloned = report.get("cloned", [])
    skipped = report.get("skipped", [])
    failed = report.get("failed", [])

    if cloned:
        UI.subheader(f"Cloned ({len(cloned)}):")
        for name in cloned:
            UI.ok(name)
    if skipped:
        UI.subheader(f"Already existed ({len(skipped)}):")
        for name in skipped:
            UI.info(name)
    if failed:
        UI.subheader(f"Failed ({len(failed)}):")
        for f_ in failed:
            UI.error(f'{f_["name"]}: {f_.get("error", "unknown")}')

    # Doctor summary
    doctor = report.get("doctor", {})
    if doctor:
        p, w, f_ = doctor.get("passed", 0), doctor.get("warned", 0), doctor.get("failed", 0)
        print()
        if f_ == 0:
            UI.ok(f"Environment: {p} checks passed, {w} warnings")
        else:
            UI.warn(f"Environment: {p} passed, {w} warnings, {f_} failed — run otaman doctor for details")

    maestro_dir = report.get("maestro_dir", "")
    print()
    UI.kv("Otaman folder", maestro_dir)
    UI.muted("Next: launch agents or run otaman doctor for full environment check")

    return 1 if failed else 0


def _parse_version_tuple(text: str) -> tuple[int, ...] | None:
    """Parse a version string into a tuple of ints; return None on failure.

    Strips a leading ``v`` and takes only the first whitespace-delimited token
    (handles outputs like ``v2.3.1 (Anthropic)``).  Stops at the first
    non-numeric segment so suffixes like ``-beta`` don't crash the comparison.
    """
    if not text:
        return None
    token = text.strip().split()[0] if text.strip() else ""
    if token.startswith("v") or token.startswith("V"):
        token = token[1:]
    parts: list[int] = []
    for seg in token.split("."):
        digits = ""
        had_non_digit = False
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                had_non_digit = True
                break
        if not digits:
            break
        parts.append(int(digits))
        if had_non_digit:
            # Stop at the first prerelease/build segment (e.g. "0-beta")
            break
    return tuple(parts) if parts else None


def _check_org_harnesses(root: Path, org_name: str) -> tuple[int, list[dict]]:
    """ce-bootstrap-harness-deps task 3.1 — verify harness binaries on an org user's PATH.

    Returns (rc, results) where rc is 0 if all harnesses pass, 1 if any fail or
    a precondition is unmet (org not declared, no system_user, no runner.harnesses).
    `results` is a list of dicts with keys: harness_id, binary, status (ok/missing/
    too_old), version, path, error.
    """
    import yaml as _yaml

    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        return 1, [{"status": "error", "error": f"platform.yaml not found at {platform_yaml}"}]

    try:
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return 1, [{"status": "error", "error": f"failed to parse platform.yaml: {exc}"}]

    orgs = config.get("orgs") or {}
    if not isinstance(orgs, dict) or org_name not in orgs:
        return 1, [{
            "status": "error",
            "error": f"org '{org_name}' not declared in platform.yaml `orgs:` block",
        }]
    org_entry = orgs[org_name]
    if not isinstance(org_entry, dict):
        return 1, [{"status": "error", "error": f"orgs.{org_name} must be a mapping"}]
    system_user = org_entry.get("system_user")
    if not system_user or not isinstance(system_user, str):
        return 1, [{
            "status": "error",
            "error": f"orgs.{org_name}.system_user is required (a Unix user name)",
        }]

    # Resolve the org user's home directory via pwd (more precise than expanduser,
    # which returns the literal ~name when the user is missing).
    import pwd as _pwd
    try:
        org_home = Path(_pwd.getpwnam(system_user).pw_dir)
    except KeyError:
        return 1, [{
            "status": "error",
            "error": f"system user '{system_user}' does not exist on this host",
        }]

    runner = config.get("runner") or {}
    harnesses = runner.get("harnesses") if isinstance(runner, dict) else None
    if not isinstance(harnesses, list) or not harnesses:
        return 1, [{
            "status": "error",
            "error": "no runner.harnesses declared in platform.yaml",
        }]

    results: list[dict] = []
    all_ok = True
    for h in harnesses:
        if not isinstance(h, dict):
            continue
        hid = h.get("id") or ""
        binary = h.get("binary") or ""
        min_version = h.get("min_version")
        if not hid or not binary:
            continue

        bin_path = org_home / ".local" / "bin" / binary
        entry = {
            "harness_id": hid,
            "binary": binary,
            "path": str(bin_path),
            "min_version": min_version,
        }

        if not bin_path.exists() or not os.access(bin_path, os.X_OK):
            entry["status"] = "missing"
            all_ok = False
            results.append(entry)
            continue

        if min_version:
            try:
                proc = subprocess.run(
                    [str(bin_path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                ver_text = (proc.stdout or proc.stderr or "").strip()
            except Exception as exc:
                entry["status"] = "version_check_failed"
                entry["error"] = str(exc)
                all_ok = False
                results.append(entry)
                continue

            actual = _parse_version_tuple(ver_text)
            required = _parse_version_tuple(str(min_version))
            entry["version"] = ver_text.splitlines()[0] if ver_text else ""
            if actual is None or required is None:
                entry["status"] = "version_unparseable"
                all_ok = False
            elif actual < required:
                entry["status"] = "too_old"
                all_ok = False
            else:
                entry["status"] = "ok"
        else:
            # No min_version pinned — presence is sufficient
            entry["status"] = "ok"

        results.append(entry)

    return (0 if all_ok else 1), results


def _print_org_harness_report(org_name: str, results: list[dict]) -> None:
    """Pretty-print the harness check results for `otaman doctor --org <name>`."""
    print()
    UI.header(f"Org Harness Check: {org_name}")
    for r in results:
        if r.get("status") == "error":
            UI.error(r.get("error", "unknown error"))
            continue
        hid = r.get("harness_id", "")
        binary = r.get("binary", "")
        status = r.get("status", "")
        version = r.get("version", "")
        if status == "ok":
            tail = f" {version}" if version else ""
            print(f"  {UI.badge('OK', C.GREEN)}  {hid}  {binary}{tail}")
        elif status == "missing":
            print(f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  NOT FOUND")
            print(f"        run: sudo bash ce-bootstrap.sh --org={org_name} --install-harness={hid}")
        elif status == "too_old":
            print(f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  {version} (min: {r.get('min_version')})")
            print(f"        run: sudo bash ce-bootstrap.sh --org={org_name} --upgrade-harness={hid}")
        else:
            print(f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  {status}: {r.get('error', '')}")


def cmd_doctor(args: list[str], *, org: str | None = None) -> int:
    """Check environment readiness — git, runtimes, CLI tools, MCP.

    When ``org`` is given (CLI ``--org <name>``), additionally verify that each
    binary declared in ``platform.yaml`` ``runner.harnesses`` is installed and
    executable for that org's system user (ce-bootstrap-harness-deps task 3.1).
    The harness check is additive — all existing checks still run.
    """
    root = Path(args[0]).resolve() if args else find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Environment Check")

    result = run_script("doctor.py", str(root), capture=True)
    if result.returncode == 2:
        UI.error(result.stderr or result.stdout)
        return 2

    import json
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        UI.error("Failed to parse doctor report")
        print(result.stdout)
        return 2

    if "error" in report:
        UI.error(report["error"])
        return 1

    # Display results
    summary = report.get("summary", {})
    checks = report.get("checks", [])

    status_icon = {
        "ok": UI.badge("OK", C.GREEN),
        "warn": UI.badge("WARN", C.YELLOW),
        "fail": UI.badge("FAIL", C.RED),
    }

    check_labels = {
        "git_identity": "Git Identity",
        "git_platform": "Git Platform & CLI",
        "runtimes": "Runtimes & SDKs",
        "claude_cli": "Claude CLI",
        "openspec": "OpenSpec CLI",
        "ssh_keys": "SSH Keys",
        "mcp_dependencies": "MCP Dependencies",
        "tmux": "tmux (connection resilience)",
        "maestro_plugin": "Otaman Setup",
        "secrets_leaks": "Secrets Hygiene",
        "git_host": "Git Host PAT",
    }

    for check in checks:
        name = check_labels.get(check["check"], check["check"])
        icon = status_icon.get(check["status"], "?")
        details = check.get("details", {})

        # Build detail string
        detail_parts = []
        if check["check"] == "git_identity":
            if details.get("user_name"):
                detail_parts.append(f'{details["user_name"]} <{details.get("user_email", "?")}>')
        elif check["check"] == "git_platform":
            if details.get("provider"):
                detail_parts.append(details["provider"])
                if details.get("cli_installed"):
                    detail_parts.append(f'{details["cli"]} CLI')
                if details.get("authenticated"):
                    detail_parts.append("authenticated")
                if details.get("pr_enabled"):
                    detail_parts.append("PR ready")
        elif check["check"] == "runtimes":
            for rt, info in details.items():
                if isinstance(info, dict) and info.get("version"):
                    detail_parts.append(f'{rt} {info["version"]}')
        elif check["check"] == "claude_cli":
            if details.get("version"):
                detail_parts.append(details["version"])
        elif check["check"] == "openspec":
            if details.get("skipped"):
                detail_parts.append("not required")
            elif details.get("version"):
                detail_parts.append(f'v{details["version"]}')
                if details.get("via_npx"):
                    detail_parts.append("via npx")
        elif check["check"] == "ssh_keys":
            if details.get("ssh_repos"):
                detail_parts.append(f'{details["ssh_repos"]} SSH repos')
            if details.get("https_repos"):
                detail_parts.append(f'{details["https_repos"]} HTTPS repos')
        elif check["check"] == "tmux":
            if details.get("version"):
                detail_parts.append(details["version"])

        detail_str = f" ({', '.join(detail_parts)})" if detail_parts else ""
        print(f"  {icon} {name}{C.DIM}{detail_str}{C.RESET}")

    # Issues
    issues = report.get("issues", [])
    if issues:
        UI.subheader(f"Issues ({len(issues)}):")
        for issue in issues:
            severity = issue.get("severity", "medium")
            if severity == "critical":
                UI.blocked(issue["issue"])
            elif severity == "high":
                UI.error(issue["issue"])
            else:
                UI.warn(issue["issue"])
            UI.muted(f"Fix: {issue['fix']}")

    # Summary line
    print()
    p, w, f_ = summary.get("passed", 0), summary.get("warned", 0), summary.get("failed", 0)
    total = summary.get("total", 0)
    if f_ == 0 and w == 0:
        UI.ok(f"All {total} checks passed — environment ready")
    elif f_ == 0:
        UI.warn(f"{p} passed, {w} warnings — mostly ready")
    else:
        UI.error(f"{p} passed, {w} warnings, {f_} failed — fix issues above")

    base_rc = 1 if report["summary"]["failed"] > 0 else 0

    # ce-bootstrap-harness-deps task 3.1 — additive `--org` harness check
    if org:
        org_rc, results = _check_org_harnesses(root, org)
        _print_org_harness_report(org, results)
        return 1 if (base_rc or org_rc) else 0

    return base_rc


def _cmd_watchdog_dispatch(args: list[str]) -> int:
    """Lazy-import wrapper for `otaman watchdog ...` so urllib + endpoint
    discovery don't load on every CLI invocation (the watchdog is a
    rarely-used surface; most operators never hit it).
    """
    from otaman_cli.watchdog import cmd_watchdog
    return cmd_watchdog(args)


def cmd_set_status(args: list[str]) -> int:
    """agent-status-presence task 1.5 — write a status record for the current agent.

    Usage:
      otaman set-status <state> [--task "..."] [--change <slug>] [--outcome <slug>]
                                [--blocked-by <agent|human>] [--agent <name>]

    States: working | blocked | waiting | idle.

    Heartbeat: re-calling with the same state preserves `since`; only
    `updated_at` advances.
    """
    import argparse
    parser = argparse.ArgumentParser(prog="otaman set-status", add_help=False)
    parser.add_argument("state", nargs="?")
    parser.add_argument("--task", default=None)
    parser.add_argument("--change", default=None)
    parser.add_argument("--outcome", default=None)
    parser.add_argument("--blocked-by", dest="blocked_by", default=None)
    parser.add_argument("--agent", dest="explicit_agent", default=None)
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        UI.muted("Usage: otaman set-status <working|blocked|waiting|idle> "
                 "[--task \"...\"] [--change SLUG] [--blocked-by NAME] [--outcome SLUG]")
        return 2

    if not ns.state:
        UI.error("set-status requires a state argument")
        UI.muted("Usage: otaman set-status <working|blocked|waiting|idle> [...]")
        return 2

    from otaman_cli.status import (
        AgentStatus, State, get_backend, is_agent_presence_enabled,
    )

    try:
        new_state = State(ns.state.lower())
    except ValueError:
        UI.error(f"Invalid state {ns.state!r}; expected one of: "
                 "working, blocked, waiting, idle")
        return 2

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    agent = resolve_agent_identity(root, explicit=ns.explicit_agent)
    if not agent:
        UI.error("Agent identity could not be resolved.")
        UI.muted("  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), .agents/current-agent")
        UI.muted("  Fix: pass --agent <name>, or set OTAMAN_AGENT, or run 'otaman init --update'")
        return 1

    if not is_agent_presence_enabled(root):
        UI.muted("Agent presence disabled (platform.agent_presence: false) — no-op")
        return 0

    backend = get_backend(root)
    existing = backend.read(agent)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    # Heartbeat: same state → preserve `since`; advance `updated_at` only.
    if existing is not None and existing.state == new_state:
        since = existing.since
    else:
        since = now_iso

    # Clearing fields when transitioning to idle (per spec: task/change null when idle)
    if new_state == State.IDLE:
        task = None
        change = None
        outcome = None
        blocked_by = None
    else:
        task = ns.task if ns.task is not None else (existing.task if existing else None)
        change = ns.change if ns.change is not None else (existing.change if existing else None)
        outcome = ns.outcome if ns.outcome is not None else (existing.outcome if existing else None)
        # blocked_by only meaningful for blocked state
        if new_state == State.BLOCKED:
            blocked_by = ns.blocked_by if ns.blocked_by is not None else (
                existing.blocked_by if existing else "human"
            )
        else:
            blocked_by = None

    status = AgentStatus(
        agent=agent,
        state=new_state,
        task=task,
        change=change,
        outcome=outcome,
        blocked_by=blocked_by,
        since=since,
        updated_at=now_iso,
    )
    try:
        backend.write(status)
    except Exception as exc:
        UI.error(f"Failed to write status: {exc}")
        return 1

    UI.ok(f"Status: {agent} → {new_state.value}")
    if task:
        UI.muted(f"  task:       {task}")
    if change:
        UI.muted(f"  change:     {change}")
    if blocked_by:
        UI.muted(f"  blocked_by: {blocked_by}")
    UI.muted(f"  since:      {since}")
    return 0


def cmd_fleet_status(args: list[str]) -> int:
    """agent-status-presence task 1.9 — fleet status table.

    Usage:
      otaman status [--blocked] [--agent NAME] [--json]

    Reads all status files via the configured backend, sorts by priority
    (blocked → working → waiting → idle), prints a table with summary counts.
    """
    import argparse, json
    parser = argparse.ArgumentParser(prog="otaman status", add_help=False)
    parser.add_argument("--blocked", action="store_true")
    parser.add_argument("--agent", dest="agent_filter", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    try:
        ns, _unknown = parser.parse_known_args(args)
    except SystemExit:
        UI.muted("Usage: otaman status [--blocked] [--agent NAME] [--json]")
        return 2

    from otaman_cli.status import State, get_backend, is_agent_presence_enabled
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    if not is_agent_presence_enabled(root):
        if ns.as_json:
            print(json.dumps({"enabled": False, "agents": []}))
        else:
            print("Agent presence disabled (platform.agent_presence: false)")
        return 0

    backend = get_backend(root)
    try:
        records = backend.read_all()
    except NotImplementedError as exc:
        UI.error(str(exc))
        return 2

    if ns.agent_filter:
        records = [r for r in records if r.agent == ns.agent_filter]
    if ns.blocked:
        records = [r for r in records if r.state == State.BLOCKED]

    # Priority sort: blocked, working, waiting, idle
    order = {State.BLOCKED: 0, State.WORKING: 1, State.WAITING: 2, State.IDLE: 3}
    records.sort(key=lambda r: (order.get(r.state, 99), r.agent))

    if ns.as_json:
        print(json.dumps({
            "enabled": True,
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "agents": [r.to_dict() for r in records],
        }, indent=2))
        return 0

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    print()
    UI.header(f"Fleet status  ({now_utc.strftime('%Y-%m-%d %H:%M UTC')})")
    if not records:
        UI.muted("  No agents reporting status yet.")
        return 0

    def _since_human(iso: str) -> str:
        try:
            t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return iso
        delta = now_utc - t
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"

    # Render compact table
    print(f"  {'AGENT':<18} {'STATE':<9} {'SINCE':<8} TASK / CHANGE")
    counts = {s: 0 for s in State}
    for r in records:
        counts[r.state] = counts.get(r.state, 0) + 1
        tail = ""
        if r.state != State.IDLE:
            parts: list[str] = []
            if r.task:
                parts.append(r.task)
            if r.change:
                parts.append(r.change)
            tail = "  ·  ".join(parts) if parts else "—"
        elif r.state == State.IDLE:
            tail = "—"
        if r.state == State.BLOCKED and r.blocked_by:
            tail = f"blocked by {r.blocked_by}  ·  {tail}".rstrip("  ·  ")
        print(f"  {r.agent:<18} {r.state.value:<9} {_since_human(r.since):<8} {tail}")

    print()
    UI.muted(
        f"Blocked: {counts.get(State.BLOCKED, 0)}   "
        f"Waiting: {counts.get(State.WAITING, 0)}   "
        f"Working: {counts.get(State.WORKING, 0)}   "
        f"Idle: {counts.get(State.IDLE, 0)}"
    )
    return 0


def cmd_status(args: list[str]) -> int:
    """Show fleet status (default) or cross-repo dashboard (--repos).

    Default: agent-status-presence fleet view (task 1.9).  Pass --repos to
    get the legacy cross-repo dashboard.
    """
    if "--repos" in args:
        args = [a for a in args if a != "--repos"]
        return _cmd_status_repos(args)
    return cmd_fleet_status(args)


def _cmd_status_repos(args: list[str]) -> int:
    """Show cross-repo status dashboard. Also runs silent bus cleanup."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project (no platform.yaml or .agents/ found)")
        return 1

    script_args = [str(root)]
    if args:
        script_args.append(args[0])  # repo filter

    result = run_script("status-report.py", *script_args, capture=True)
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    if "error" in report:
        UI.error(report['error'])
        return 1

    UI.header(f"Otaman Status: {report.get('project', '?')}")

    # Repos table
    repos = report.get("repos", [])
    headers = ["Repo", "Owner", "Branch", "State", "Messages"]
    rows = []

    for repo in repos:
        name = repo["name"]
        owner = repo.get("owner", "-")
        if not repo.get("exists"):
            rows.append([UI.repo(name), UI.agent(owner), f"{C.RED}NOT FOUND{C.RESET}", "", ""])
            continue
        if not repo.get("is_git"):
            rows.append([UI.repo(name), UI.agent(owner), UI.path("not a git repo"), "", ""])
            continue

        branch = repo.get("branch", "?")
        if len(branch) > 13:
            branch = branch[:12] + ".."

        clean = repo.get("clean", True)
        ahead = repo.get("ahead", 0)
        state_parts = []
        if clean:
            state_parts.append(UI.badge("clean", C.GREEN))
        else:
            state_parts.append(f"{C.YELLOW}{repo.get('modified_files', 0)} mod{C.RESET}")
        if ahead:
            state_parts.append(f"{ahead}^")
        behind = repo.get("behind", 0)
        if behind:
            state_parts.append(f"{behind}v")
        state = " ".join(state_parts)

        pending = repo.get("pending_messages", 0)
        msg_str = f"{C.YELLOW}{pending} pending{C.RESET}" if pending else UI.path("none")

        rows.append([UI.repo(name), UI.agent(owner), branch, state, msg_str])

    UI.table(headers, rows, col_widths=[20, 18, 15, 18, 12])

    # Messages summary
    msgs = report.get("messages", {})
    UI.kv("Messages", f"{msgs.get('pending', 0)} pending | {msgs.get('read', 0)} read | {msgs.get('resolved', 0)} resolved")

    # Pending reviews
    reviews = report.get("pending_reviews", [])
    if reviews:
        UI.subheader("Pending reviews:")
        for r in reviews:
            UI.bullet(f"{r.get('reviewer', '?')}: {r.get('scope', '?')} [{r.get('status', '?')}]")

    # Proposals
    proposals = report.get("proposals", [])
    if proposals:
        UI.subheader("Active proposals:")
        for p in proposals:
            UI.bullet(f"{p.get('id', '?')}: {p.get('title', '?')} [{p.get('status', '?')}]", color=C.BLUE)

    # Silent bus cleanup
    cleanup_result = run_script("cleanup-bus.py", str(root), capture=True)
    if cleanup_result.returncode == 0:
        try:
            import json
            cr = json.loads(cleanup_result.stdout)
            migrated = cr.get("migrated", 0)
            archived = len(cr.get("archived", []))
            if migrated or archived:
                parts = []
                if migrated:
                    parts.append(f"{migrated} migrated")
                if archived:
                    parts.append(f"{archived} archived")
                UI.muted(f"Bus cleanup: {', '.join(parts)}")
        except (json.JSONDecodeError, ImportError):
            pass

    print()
    return 0


def _resolve_bus_paths(root: Path) -> tuple[Path, Path]:
    """Resolve bus active dir and acks dir from project root."""
    try:
        import yaml as _yaml
        config_path = root / "platform.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = _yaml.safe_load(f)
            bus_rel = config.get("communication", {}).get("bus_path", ".agents/bus")
        else:
            bus_rel = ".agents/bus"
    except Exception:
        bus_rel = ".agents/bus"
    active_dir = root / bus_rel / "active"
    acks_dir = active_dir / "acks"
    return active_dir, acks_dir


def _get_agent_ack_status(msg_stem: str, agent: str, acks_dir: Path) -> str:
    """Get ack status for a specific agent+message. Returns 'pending', 'read', or 'resolved'."""
    for status in ("resolved", "read"):
        ack_file = acks_dir / f"{msg_stem}.{agent}.ack"
        if ack_file.exists():
            content = ack_file.read_text(encoding="utf-8").strip()
            if content == status or status in content:
                return status
    # Check if any ack file exists at all
    ack_file = acks_dir / f"{msg_stem}.{agent}.ack"
    if ack_file.exists():
        return ack_file.read_text(encoding="utf-8").strip() or "read"
    return "pending"


def cmd_whoami(args: list[str]) -> int:
    """Print current agent identity + project + routing + bus state.

    Usage:
      otaman whoami [--json]

    Useful for confirming which agent / project / routing identity is
    loaded in this tab, especially when terminal tab titles get
    overwritten by claude or tmux.
    """
    import json
    import os
    import yaml
    from datetime import datetime, timezone

    json_mode = "--json" in args

    root = find_project_root()
    agent = resolve_agent_identity(root) if root else None

    # Routing: read via the same env-var chain the hooks use.
    try:
        from otaman_core._resolve import active_routing_env
        routing = active_routing_env()
    except ImportError:
        routing = (os.environ.get("OTAMAN_ACTIVE_ROUTING")
                   or os.environ.get("OTAMAN_ACTIVE_ACCOUNT")
                   or os.environ.get("MAESTRO_ACTIVE_ACCOUNT"))

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or "<default ~/.claude>"
    tmux_env = os.environ.get("TMUX", "")
    # TMUX env is "/path/to/socket,pid,session-id"; session name needs `tmux display`,
    # but the env var alone is enough to flag "yes, inside tmux".
    in_tmux = bool(tmux_env)
    tmux_session = None
    if in_tmux:
        try:
            import subprocess
            res = subprocess.run(
                ["tmux", "display-message", "-p", "#S"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if res.returncode == 0:
                tmux_session = res.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

    project_name = None
    if root and (root / "platform.yaml").is_file():
        try:
            cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
            project_name = cfg.get("project")
        except (yaml.YAMLError, OSError):
            pass

    # Bus state: pending/read/resolved counts for this agent.
    counts = {"pending": 0, "read": 0, "resolved": 0}
    if root and agent:
        active_dir, acks_dir = _resolve_bus_paths(root)
        if active_dir.is_dir():
            import re
            for f in sorted(active_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8")
                    m = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
                    if not m:
                        continue
                    fm = yaml.safe_load(m.group(1))
                    if not isinstance(fm, dict):
                        continue
                    to = fm.get("to", "")
                    if to != agent and to != "all":
                        continue
                    status = _get_agent_ack_status(f.stem, agent, acks_dir)
                    counts[status] = counts.get(status, 0) + 1
                except (OSError, yaml.YAMLError):
                    continue

    if json_mode:
        print(json.dumps({
            "agent": agent,
            "project": project_name,
            "project_root": str(root) if root else None,
            "cwd": str(Path.cwd()),
            "routing": routing,
            "config_dir": config_dir,
            "in_tmux": in_tmux,
            "tmux_session": tmux_session,
            "bus_counts": counts,
        }, indent=2))
        return 0

    # Pretty output
    UI.header(f"Otaman: {agent or '<unknown agent>'}")
    if project_name:
        UI.kv("  Project", project_name)
    if root:
        UI.kv("  Project root", str(root))
    else:
        UI.muted("  (not inside a otaman project)")
    UI.kv("  Cwd", str(Path.cwd()))
    UI.kv("  Routing", routing or "<not set>")
    UI.kv("  Config dir", config_dir)
    if in_tmux:
        UI.kv("  Tmux", tmux_session or "<unknown session>")
    if root and agent:
        UI.kv("  Bus", f"{counts['pending']} pending | {counts['read']} read | {counts['resolved']} resolved")
    return 0


# outcome-proposal-routing task 3.1 — message-type registry for `otaman send`
# validation.  Keep this list lean: deliberately limited to types that have
# bus-server / CLI / downstream-agent semantics today.  Adding a new type is
# a spec-level change.
MESSAGE_TYPES: frozenset[str] = frozenset({
    "info",
    "question",
    "task-assignment",
    "task-complete",
    "spec-change",
    "spec-change-request",
    "spec-change-approved",
    "spec-change-rejected",
    "contract-change",
    "review-request",
    "proposal",
    "outcome-proposal",
})

# outcome-proposal-routing task 3.2 — subject-line keywords that suggest
# the operator probably meant `--type outcome-proposal` instead of the
# default `--type info`.
_OUTCOME_SUBJECT_RE = re.compile(r"outcome|proposal|business impact", re.IGNORECASE)


def cmd_send(args: list[str]) -> int:
    """Send a bus message to another agent (mirrors otaman_send MCP tool).

    Usage:
      otaman send <to> --subject "..." --body "..." [--type TYPE] [--priority P]

    `to` is the recipient agent name, "all" for broadcast, or "human".
    """
    import argparse
    parser = argparse.ArgumentParser(prog="otaman send", add_help=False)
    parser.add_argument("to", nargs="?")
    parser.add_argument("--subject", required=False)
    parser.add_argument("--body", required=False)
    parser.add_argument("--type", dest="msg_type", default="info")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--from", dest="explicit_from")
    parser.add_argument(
        "--cc", action="append", default=None, metavar="AGENT",
        help="add a CC recipient; repeat for multiple (bus-cc-routing task 2.1)",
    )
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        UI.muted("Usage: otaman send <to> --subject \"...\" --body \"...\" "
                 "[--type info|question|task-assignment|...] [--priority low|normal|high|urgent]")
        return 2

    if not ns.to or not ns.subject or not ns.body:
        UI.error("send requires <to>, --subject, and --body")
        UI.muted("Usage: otaman send <to> --subject \"...\" --body \"...\" "
                 "[--type ...] [--priority ...]")
        return 2

    # outcome-proposal-routing task 3.1 — validate message type against the
    # registry.  Unknown types are rejected outright (typo guard); the
    # spec-listed types are accepted.
    if ns.msg_type not in MESSAGE_TYPES:
        UI.error(f"Unknown message type {ns.msg_type!r}.")
        UI.muted("  Allowed types: " + ", ".join(sorted(MESSAGE_TYPES)))
        return 2

    # outcome-proposal-routing task 3.2 — subject-pattern nudge.  When
    # --type info is used with a subject that looks like an outcome
    # statement, emit a non-blocking warning suggesting --type
    # outcome-proposal.  Do NOT block the send.
    if ns.msg_type == "info" and _OUTCOME_SUBJECT_RE.search(ns.subject or ""):
        UI.warn(
            "Subject looks like an outcome/proposal — consider "
            "`--type outcome-proposal` so strategic agents (CPO, cofounder) "
            "are auto-CC'd via routing rules."
        )

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    agent = resolve_agent_identity(root, explicit=ns.explicit_from)
    if not agent:
        UI.error("Agent identity could not be resolved.")
        UI.muted("  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), .agents/current-agent")
        UI.muted("  Fix: set OTAMAN_AGENT env var, or run 'otaman init --update' to write per-repo .otaman files")
        UI.muted("Tip: run from inside a managed repo, or pass --from <agent>")
        return 1

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")
    ts_iso = now.isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", ns.subject.lower())[:40].strip("-")
    filename = f"{ts}-{agent}-to-{ns.to}-{slug}.md"

    # bus-cc-routing task 2.1 — emit `cc:` field when --cc is given.
    # De-duplicate, drop the primary recipient if accidentally included,
    # and drop empty / whitespace-only entries.
    cc_list: list[str] = []
    if ns.cc:
        seen: set[str] = set()
        for raw in ns.cc:
            name = (raw or "").strip()
            if not name or name == ns.to or name in seen:
                continue
            cc_list.append(name)
            seen.add(name)

    cc_line = f"cc: [{', '.join(cc_list)}]\n" if cc_list else ""
    content = (
        f"---\n"
        f"id: {ts}-{agent[:8]}\n"
        f"from: {agent}\n"
        f"to: {ns.to}\n"
        f"{cc_line}"
        f"priority: {ns.priority}\n"
        f"type: {ns.msg_type}\n"
        f"timestamp: {ts_iso}\n"
        f"status: pending\n"
        f"---\n"
        f"\n"
        f"## Subject: {ns.subject}\n"
        f"\n"
        f"{ns.body}\n"
    )

    active_dir, _acks_dir = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    msg_path = active_dir / filename
    msg_path.write_text(content, encoding="utf-8")

    UI.ok(f"Sent: {filename}")
    UI.kv("  From", agent)
    UI.kv("  To", ns.to)
    if cc_list:
        UI.kv("  CC", ", ".join(cc_list))
    UI.kv("  Type", ns.msg_type)
    UI.kv("  Priority", ns.priority)
    UI.muted(f"  Path: {msg_path.relative_to(root)}")
    if cc_list:
        UI.muted("  Note: bus-server fan-out (per-recipient copies) requires send via MCP otaman_send; cmd_send writes the cc: field only.")
    return 0


def cmd_read(args: list[str]) -> int:
    """Read the full content of a specific bus message.

    Usage:
      otaman read <message-stem>

    The <message-stem> is the filename without .md (as shown in
    `otaman check` output). Substring match accepted when unambiguous.
    Searches active/ first, then archive/YYYY-MM/.
    """
    if not args:
        UI.error("read requires a message stem")
        UI.muted("Usage: otaman read <message-stem>")
        UI.muted("Tip: get the stem from `otaman check` output")
        return 2

    stem = args[0]
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    active_dir, _acks_dir = _resolve_bus_paths(root)

    # 1. Exact match in active/
    candidate = active_dir / f"{stem}.md"
    if candidate.is_file():
        msg_file = candidate
    else:
        # 2. Substring match in active/
        matches = list(active_dir.glob(f"*{stem}*.md"))
        if not matches:
            # 2b. Token-based fallback: agents sometimes pass partial stems
            # like "20260426T15164601-tasks-gitlab-cicd-pipeline" when the
            # legacy: real file may have -maestro-to-backend-agent- in the middle. Match
            # each dash-separated token with wildcards between them.
            tokens = [tok for tok in stem.split("-") if tok]
            if len(tokens) >= 2:
                pattern = "*" + "*".join(tokens) + "*.md"
                matches = list(active_dir.glob(pattern))
        if len(matches) == 1:
            msg_file = matches[0]
        elif len(matches) > 1:
            UI.error(f"Ambiguous stem '{stem}'. Matches:")
            for m in matches[:5]:
                UI.muted(f"  - {m.stem}")
            if len(matches) > 5:
                UI.muted(f"  ... and {len(matches) - 5} more")
            return 1
        else:
            # 3. Try archive/YYYY-MM/
            archive_root = active_dir.parent / "archive"
            archive_matches: list[Path] = []
            if archive_root.is_dir():
                for month_dir in archive_root.iterdir():
                    if month_dir.is_dir():
                        archive_matches.extend(month_dir.glob(f"*{stem}*.md"))
            if len(archive_matches) == 1:
                msg_file = archive_matches[0]
                UI.muted(f"  (found in archive: {msg_file.parent.name})")
            elif len(archive_matches) > 1:
                UI.error(f"Ambiguous in archive: {[m.stem for m in archive_matches[:5]]}")
                return 1
            else:
                UI.error(f"Message not found: {stem}")
                UI.muted(f"  Searched: {active_dir.relative_to(root)} + archive/*/")
                return 1

    # Print the full message content as-is (frontmatter + body)
    print(msg_file.read_text(encoding="utf-8"))
    return 0


def cmd_check(args: list[str], hide_broadcast_hours: int | None = None) -> int:
    """Check messages for an agent."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # Determine agent: explicit arg → CWD→repo→owner → .agents/current-agent
    agent = resolve_agent_identity(root, explicit=args[0] if args else None)
    if not agent:
        UI.error("No agent specified and identity could not be resolved.")
        UI.muted("  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), .agents/current-agent")
        UI.muted("  Fix: set OTAMAN_AGENT env var, or run 'otaman init --update' to write per-repo .otaman files")
        UI.muted("Usage: otaman check <agent-name>")
        return 1

    try:
        import yaml
    except ImportError:
        UI.error("PyYAML required. Install with: pip install pyyaml")
        return 2

    active_dir, acks_dir = _resolve_bus_paths(root)

    if not active_dir.is_dir():
        print(f"No messages - bus directory doesn't exist yet.")
        return 0

    UI.header(f"Messages for: {agent}")

    # Parse messages
    messages = []
    total = {"pending": 0, "read": 0, "resolved": 0}

    for f in sorted(active_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue

            to = fm.get("to", "")
            # bus-cc-routing task 2.2 — also pick up CC copies addressed to
            # someone else but with this agent in the `cc:` list (and the
            # `x-cc: true` marker indicating the bus_server wrote this copy
            # for a CC recipient).
            cc_field = fm.get("cc") or []
            is_cc_copy = bool(fm.get("x-cc")) and isinstance(cc_field, list) and (agent in cc_field)
            if to != agent and to != "all" and not is_cc_copy:
                continue

            # Per-agent status from ack files
            status = _get_agent_ack_status(f.stem, agent, acks_dir)
            total[status] = total.get(status, 0) + 1

            # Extract subject
            subject = ""
            body_start = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            for line in body_start.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break

            messages.append({
                "id": fm.get("id", "?"),
                "from": fm.get("from", "?"),
                "to": str(fm.get("to", "")),
                "priority": fm.get("priority", "normal"),
                "type": fm.get("type", "?"),
                "status": status,
                "timestamp": str(fm.get("timestamp", "")),
                "subject": subject,
                "file": f.name,
                "stem": f.stem,
                # inter-agent-request-response-contract (tasks 2.1, 2.2)
                "expects_response": bool(fm.get("expects-response")),
                "response_effort": fm.get("response-effort"),
                "response_deadline": fm.get("response-deadline"),
                "reply_to": fm.get("reply-to"),
                # bus-cc-routing task 2.2 — `x-cc: true` marks a CC copy
                "is_cc": bool(fm.get("x-cc")),
            })
        except (OSError, yaml.YAMLError):
            continue

    # bus-cc-routing task 2.2 — partition CC copies into their own bucket so
    # the primary-messages section stays focused. CC copies still respect the
    # status filter (pending vs read/resolved).
    primary_messages = [m for m in messages if not m.get("is_cc")]
    cc_messages = [m for m in messages if m.get("is_cc")]

    # Display pending first, then others (primary only — CC has its own section)
    pending = [m for m in primary_messages if m["status"] == "pending"]
    other = [m for m in primary_messages if m["status"] != "pending"]
    cc_pending = [m for m in cc_messages if m["status"] == "pending"]
    cc_other = [m for m in cc_messages if m["status"] != "pending"]

    # Apply --hide-broadcast-older-than filter (D4)
    if hide_broadcast_hours is not None and hide_broadcast_hours > 0:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hide_broadcast_hours)
        def _is_old_broadcast(m: dict) -> bool:
            if m.get("to") != "all":
                return False
            ts_str = m.get("timestamp", "")
            if not ts_str:
                return False
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                return ts < cutoff
            except ValueError:
                return False
        pending = [m for m in pending if not _is_old_broadcast(m)]

    # Task 2.1: tiebreaker sort within priority band — expects-response,
    # response-effort, timestamp. See response_contract.make_sort_key.
    from otaman_cli.response_contract import (
        deadline_is_imminent as _deadline_imminent,
        make_sort_key as _sort_key,
    )
    pending.sort(key=_sort_key)

    if pending:
        for m in pending:
            broadcast_label = " (broadcast)" if m.get("to") == "all" else ""
            # Task 2.2: surface [DEADLINE] indicator for imminent response-deadline
            deadline_label = ""
            if _deadline_imminent(m.get("response_deadline")):
                deadline_label = f" {C.RED}[DEADLINE {m['response_deadline']}]{C.RESET}"
            UI.bullet(
                f"{m['id']} from {UI.agent(m['from'])} "
                f"[{UI.priority(m['priority'])}]{broadcast_label}{deadline_label}"
            )
            print(f"    {m['subject']}")
            UI.muted(f"{m['type']} | {m['timestamp']} | {m['stem']}")
            print()
    else:
        UI.muted("No pending messages.")
        print()

    if other:
        read_count = sum(1 for m in other if m["status"] == "read")
        resolved_count = sum(1 for m in other if m["status"] == "resolved")
        UI.muted(f"Also: {read_count} read, {resolved_count} resolved")

    # bus-cc-routing task 2.2 — CC (copies) section, ONLY when present.
    # Visually lighter than primary: `·` bullet instead of `*`, includes
    # the `to` field so the reader sees who the primary recipient was.
    if cc_pending or cc_other:
        print()
        UI.muted("CC (copies):")
        for m in cc_pending:
            broadcast_label = " (broadcast)" if m.get("to") == "all" else ""
            print(
                f"  · {m['id']} to {UI.agent(m['to'])} from "
                f"{UI.agent(m['from'])} [{UI.priority(m['priority'])}]{broadcast_label}"
            )
            if m.get("subject"):
                print(f"      {m['subject']}")
            UI.muted(f"    {m['type']} | {m['timestamp']} | {m['stem']}")
        if cc_other:
            cc_read = sum(1 for m in cc_other if m["status"] == "read")
            cc_resolved = sum(1 for m in cc_other if m["status"] == "resolved")
            UI.muted(f"  Also (CC): {cc_read} read, {cc_resolved} resolved")

    # Show blocked tasks
    blocked_file = root / ".agents" / "blocked" / f"{agent}.md"
    if blocked_file.exists():
        blocked_content = blocked_file.read_text(encoding="utf-8").strip()
        if blocked_content:
            print()
            UI.blocked("BLOCKED TASKS:")
            # Parse blocked entries and check if any are now unblocked
            for block in blocked_content.split("\n## Blocked: "):
                block = block.strip()
                if not block:
                    continue
                lines = block.splitlines()
                task_title = lines[0] if lines else "?"
                # Find the proposal stem
                proposal_stem = ""
                for line in lines:
                    if line.strip().startswith("- **Proposal**:"):
                        proposal_stem = line.split(":", 1)[1].strip()
                        break

                # Check if approval + spec-change arrived
                has_approval = any(
                    m["type"] == "spec-change-approved" and proposal_stem and proposal_stem in m.get("subject", "")
                    for m in messages
                )
                has_spec_change = any(m["type"] == "spec-change" for m in messages)

                if has_approval and has_spec_change:
                    UI.ok(f"READY TO RESUME: {task_title}")
                    UI.ok("Specs updated — read them and continue implementation")
                elif has_approval:
                    UI.bullet(f"{task_title} — approved, waiting for spec commit...")
                else:
                    # Check for rejection
                    has_rejection = any(
                        m["type"] == "spec-change-rejected" and proposal_stem and proposal_stem in m.get("subject", "")
                        for m in messages
                    )
                    if has_rejection:
                        UI.error(f"REJECTED: {task_title} — read rejection reason and adapt")
                    else:
                        UI.bullet(f"{task_title} — waiting for human approval")
                if proposal_stem:
                    UI.muted(f"Proposal: {proposal_stem}")

    UI.kv("Summary", f"{total.get('pending', 0)} pending | {total.get('read', 0)} read | {total.get('resolved', 0)} resolved")
    if pending:
        UI.muted("Use `otaman read <msg-stem>` to read a message")
        UI.muted("Use `otaman ack <msg-stem>` to acknowledge a message")

    # agent-status-presence task 1.10 — fleet section.
    _check_render_fleet(root)

    return 0


def _check_render_fleet(root: Path) -> None:
    """Append fleet summary to `otaman check` output.

    Per design Q4:
      - Omit section entirely when all agents idle OR agent_presence is false
      - One-line compact summary when any agent is non-idle but none blocked
      - Full table when any agent is blocked
    """
    try:
        from otaman_cli.status import State, get_backend, is_agent_presence_enabled
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return
    try:
        records = get_backend(root).read_all()
    except NotImplementedError:
        return
    except Exception:
        return
    non_idle = [r for r in records if r.state != State.IDLE]
    if not non_idle:
        return

    has_blocked = any(r.state == State.BLOCKED for r in records)
    if has_blocked:
        # Full table — reuse the fleet command for consistency
        print()
        cmd_fleet_status([])
        return

    # Compact one-liner
    parts: list[str] = []
    for r in non_idle:
        tag = r.task or r.change or "—"
        parts.append(f"{r.agent} {r.state.value} ({tag})")
    print()
    UI.muted(f"Fleet: {' · '.join(parts)}")


def cmd_ack(args: list[str], status: str = "resolved") -> int:
    """Acknowledge a bus message for the current agent."""
    if not args:
        UI.error("Message identifier required")
        UI.muted("Usage: otaman ack <msg-stem-or-partial> [--read | --resolved]")
        UI.muted("  msg-stem: filename without .md (shown in 'otaman check' output)")
        UI.muted("  --read: mark as read (will still show in pending-ish view)")
        UI.muted("  --resolved: mark as resolved (default)")
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # Determine agent: CWD→repo→owner → .agents/current-agent
    agent = resolve_agent_identity(root)
    if not agent:
        UI.error("No agent identity set.")
        UI.muted("  Set OTAMAN_AGENT env var, or run 'otaman init --update' to write per-repo .otaman agent: fields")
        return 1

    active_dir, acks_dir = _resolve_bus_paths(root)
    acks_dir.mkdir(parents=True, exist_ok=True)

    pattern = args[0]
    # Find matching message(s) - support partial match
    matches = []
    if active_dir.is_dir():
        for f in active_dir.glob("*.md"):
            if pattern in f.stem or pattern == f.stem:
                matches.append(f)

    # Token-based fallback: split input by dashes and glob between tokens.
    # Handles the "logical reconstruction" stem form
    # (e.g. "20260426T15164601-tasks-gitlab-cicd-pipeline" when the real
    # legacy: filename may have "-maestro-to-backend-agent-" in the middle).
    if not matches and "-" in pattern and active_dir.is_dir():
        tokens = [tok for tok in pattern.split("-") if tok]
        if len(tokens) >= 2:
            glob_pattern = "*" + "*".join(tokens) + "*.md"
            matches = list(active_dir.glob(glob_pattern))

    # Frontmatter-id fallback: scan every .md file's YAML frontmatter and
    # match against the `id:` field. The id field is what's shown at the
    # top of each `otaman check` entry, so agents that copy from there
    # arrive with this form (e.g. "20260409T224058-3aeed02" where 3aeed02
    # is a short hash that doesn't appear in the filename).
    if not matches and active_dir.is_dir():
        import re as _re
        for f in active_dir.glob("*.md"):
            try:
                head = f.read_text(encoding="utf-8")[:512]
                fm_id_match = _re.search(r"^id:\s*(\S+)", head, _re.MULTILINE)
                if fm_id_match and (fm_id_match.group(1) == pattern or pattern in fm_id_match.group(1)):
                    matches.append(f)
            except (OSError, UnicodeDecodeError):
                continue

    if not matches:
        UI.error(f"No message matching '{pattern}' in active bus")
        UI.muted("Tip: paste the full file stem from the bottom line of each `otaman check` entry,")
        UI.muted("     OR the frontmatter `id:` value from the top line.")
        return 1

    if len(matches) > 5:
        UI.warn(f"{len(matches)} messages match '{pattern}'.")
        UI.muted("Be more specific, or use 'otaman ack --all' to ack all pending.")
        return 1

    # Task 2.3 advisory: when resolving a message that expects a response,
    # warn if no outbound reply with reply-to: <this-id> exists.  Do not block.
    if status == "resolved":
        from otaman_cli.response_contract import has_outbound_reply as _has_reply
        import yaml as _yaml
        for msg_file in matches:
            try:
                head = msg_file.read_text(encoding="utf-8")[:2048]
            except OSError:
                continue
            fm_match = re.match(r"^---\n(.+?)\n---", head, re.DOTALL)
            if not fm_match:
                continue
            try:
                fm = _yaml.safe_load(fm_match.group(1))
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            if not fm.get("expects-response"):
                continue
            msg_id = str(fm.get("id") or msg_file.stem)
            if _has_reply(active_dir, in_reply_to_id=msg_id, from_agent=agent):
                continue
            UI.warn(
                "this message expects a response but no reply has been sent. "
                "Ack as 'read' instead, or send a reply first."
            )
            UI.muted(f"  message: {msg_file.stem}")

    for msg_file in matches:
        ack_file = acks_dir / f"{msg_file.stem}.{agent}.ack"
        ack_file.write_text(status + "\n", encoding="utf-8")
        UI.ok(f"Acked: {msg_file.stem} -> {status}")

    # agent-status-presence task 1.6 — when a `task-assignment` is acked,
    # auto-write `working` status with task/change parsed from the message.
    # Best-effort: any parsing failure leaves task/change null.
    _status_hook_after_ack(root, agent, matches)

    return 0


def _status_hook_after_ack(root: Path, agent: str, msg_files: list[Path]) -> None:
    """agent-status-presence task 1.6 — set `working` after acking a task-assignment.

    For each acked message: if `type: task-assignment`, parse task + change from
    the body (best-effort) and write a `working` status record.  Multiple
    task-assignments in one ack call → first one wins (rare path; matches
    spec's "fires when acked message is type: task-assignment" wording).
    """
    try:
        from otaman_cli.status import (
            AgentStatus, State, get_backend, is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    import yaml as _yaml
    for f in msg_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
        if not fm_match:
            continue
        try:
            fm = _yaml.safe_load(fm_match.group(1))
        except Exception:
            continue
        if not isinstance(fm, dict) or fm.get("type") != "task-assignment":
            continue

        body = text[fm_match.end():] if fm_match else ""
        task, change = _parse_task_and_change_from_body(body)
        backend = get_backend(root)
        existing = backend.read(agent)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        # State change → reset since; same state → preserve.
        since = existing.since if (existing and existing.state == State.WORKING) else now_iso
        try:
            backend.write(AgentStatus(
                agent=agent, state=State.WORKING,
                task=task, change=change,
                since=since, updated_at=now_iso,
            ))
        except Exception:
            pass
        return  # first task-assignment in this ack batch is enough


def _parse_task_and_change_from_body(body: str) -> tuple[str | None, str | None]:
    """Best-effort: pull task + change from a task-assignment body.

    Heuristic order (matches plugin-agent's task-assignment templates):
      1. `**Task:** <N.M ...>` or `**Tasks:** <N.M ...>`
      2. `**Change:** <slug>` (sometimes appears in design / task assignment)
      3. `### N.M — <text>` heading (first occurrence) → use heading text
      4. Change slug: a line starting with `**Spec:**` or path hint
    Returns (task, change), either may be None.
    """
    task: str | None = None
    change: str | None = None
    if not body:
        return task, change

    for line in body.splitlines()[:80]:
        s = line.strip()
        if not s:
            continue
        if task is None:
            m = re.match(r"^\*\*Tasks?:\*\*\s+(.+)$", s)
            if m:
                task = m.group(1).strip()[:120]
                continue
        if task is None:
            m = re.match(r"^###\s+(\d+(?:\.\d+)+)\s+[—-]?\s*(.+)$", s)
            if m:
                task = f"{m.group(1)} {m.group(2)}"[:120]
                continue
        if change is None:
            m = re.match(r"^\*\*Change:\*\*\s+(.+)$", s)
            if m:
                change = m.group(1).strip().split()[0]
                continue
        if change is None:
            m = re.match(r"^\*\*Spec:\*\*\s+`?([\w/.\-]+)`?", s)
            if m:
                raw = m.group(1)
                # If it's a path like openspec/changes/<slug>/..., extract the slug
                parts = raw.split("/")
                if "changes" in parts:
                    idx = parts.index("changes")
                    if idx + 1 < len(parts):
                        change = parts[idx + 1]
                        continue
                change = raw
                continue
        if task is not None and change is not None:
            break
    return task, change


def cmd_cleanup(args: list[str], dry_run: bool = False) -> int:
    """Archive old bus messages and clean up."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Otaman Bus Cleanup")

    result = run_script("cleanup-bus.py", str(root), *( ["--dry-run"] if dry_run else []),
                        capture=True)
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    if report.get("migrated"):
        UI.ok(f"Migrated: {report['migrated']} message(s) from flat bus/ to bus/active/")

    archived = report.get("archived", [])
    if archived:
        UI.ok(f"Archived: {len(archived)} message(s)")
        for name in archived[:10]:
            UI.muted(name)
        if len(archived) > 10:
            UI.muted(f"... and {len(archived) - 10} more")

    deleted = report.get("deleted", [])
    if deleted:
        UI.error(f"Deleted: {len(deleted)} archive(s)")
        for d in deleted:
            UI.muted(d)

    if not archived and not deleted and not report.get("migrated"):
        UI.muted("Nothing to clean up.")

    UI.kv("Active", str(active := report.get("active_count", 0)))
    UI.kv("Archived", str(report.get("archive_count", 0)))

    if report.get("errors"):
        for e in report["errors"]:
            UI.error(e)

    if dry_run:
        UI.warn("(dry run — no changes made)")

    return 0


def cmd_propose(args: list[str], desc: str = "") -> int:
    """Create a spec-change-request on the bus for human approval."""
    if not args:
        UI.error("Title required")
        UI.muted("Usage: otaman propose \"add user pagination\" [-d \"Detailed description\"]")
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Spec Change Request")

    title = " ".join(args)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # Get agent: CWD→repo→owner → .agents/current-agent → "human"
    agent = resolve_agent_identity(root) or "human"

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    msg_id = f"{now_ts}-scr-{slug}"
    filename = f"{now_ts}-{agent}-to-human-spec-change-request.md"

    active_dir, _ = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "acks").mkdir(exist_ok=True)

    content = f"""---
id: {msg_id}
from: {agent}
to: human
priority: high
type: spec-change-request
timestamp: {now_iso}
status: pending
---

## Subject: Spec change request: {title}

### What needs to change
{desc or "TODO: Describe the proposed spec change."}

### Why this is needed
TODO: What was discovered during implementation that triggered this.

### Affected specs
TODO: Which spec files/areas need updating.

### Affected repos
TODO: Which repos will need implementation changes after the spec updates.

### Suggested spec changes
TODO: Concrete suggestions for what the spec should say.
"""

    filepath = active_dir / filename
    filepath.write_text(content, encoding="utf-8")

    # Record blocked task
    msg_stem = filepath.stem
    blocked_dir = root / ".agents" / "blocked"
    blocked_dir.mkdir(parents=True, exist_ok=True)
    blocked_file = blocked_dir / f"{agent}.md"
    blocked_entry = f"""
## Blocked: {title}
- **Proposal**: {msg_stem}
- **Blocked since**: {now_iso}
- **Depends on**: spec-change-approved + spec-change notification
- **Task to resume**: Implement feature after spec is committed
"""
    with open(blocked_file, "a", encoding="utf-8") as f:
        f.write(blocked_entry)

    UI.ok(f"Created: {filepath.relative_to(root)}")
    UI.kv("From", UI.agent(agent))
    UI.kv("Type", "spec-change-request (pending human approval)", C.YELLOW)
    UI.kv("Blocked", str(blocked_file.relative_to(root)), C.YELLOW)
    print()
    UI.blocked("STOP: Do NOT implement features that depend on this spec change.")
    UI.action(f"Switch to other tasks. Run {C.BOLD}otaman check{C.RESET} to poll for approval.")
    print()
    UI.muted("A human must review and approve this via: otaman approve")
    UI.muted("Edit the message file to fill in details if needed.")
    return 0



def _read_platform_specs_path(root: "Path") -> str:
    """Return the specs.path value from platform.yaml, or '' if absent."""
    try:
        import yaml as _yaml
        config_path = root / "platform.yaml"
        if not config_path.is_file():
            return ""
        with open(config_path, encoding="utf-8") as f:
            config = _yaml.safe_load(f) or {}
        return config.get("specs", {}).get("path", "")
    except Exception:
        return ""


def _read_spec_owner(root: "Path", change_name: str) -> "str | None":
    """Return spec_owner from <change>/.openspec.yaml, or None if absent/unresolvable."""
    try:
        import yaml as _yaml
        specs_rel = _read_platform_specs_path(root)
        if not specs_rel:
            return None
        openspec_yaml = (root / specs_rel / "openspec" / "changes" / change_name / ".openspec.yaml").resolve()
        if not openspec_yaml.is_file():
            return None
        with open(openspec_yaml, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        owner = data.get("spec_owner", "")
        return str(owner).strip() or None
    except Exception:
        return None


def _write_spec_owner(root: "Path", change_name: str, agent: str) -> None:
    """Write or update spec_owner field in <change>/.openspec.yaml. Silent no-op on any error."""
    try:
        specs_rel = _read_platform_specs_path(root)
        if not specs_rel:
            return
        openspec_yaml = (root / specs_rel / "openspec" / "changes" / change_name / ".openspec.yaml").resolve()
        if not openspec_yaml.parent.is_dir():
            return
        lines = openspec_yaml.read_text(encoding="utf-8").splitlines() if openspec_yaml.is_file() else []
        new_lines = [l for l in lines if not l.strip().startswith("spec_owner:")]
        new_lines.append(f"spec_owner: {agent}")
        openspec_yaml.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _find_task_assignment_sender(active_dir: "Path", change_name: str, root: "Path") -> str:
    """Locate the agent who sent the task-assignment for *change_name*.

    Scans bus active/ for messages with type: task-assignment whose
    body or change: frontmatter field matches *change_name*.
    Returns the from: agent name, or human if not found (D2 fallback).
    """
    try:
        import yaml as _yaml
    except ImportError:
        return "human"

    try:
        candidates = sorted(active_dir.glob("*.md"), reverse=True)
    except OSError:
        return "human"

    for f in candidates:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        import re as _re
        fm_match = _re.match(r"^---\n(.+?)\n---", text, _re.DOTALL)
        if not fm_match:
            continue
        try:
            fm = _yaml.safe_load(fm_match.group(1))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        if fm.get("type") != "task-assignment":
            continue
        # Match by explicit change: field or by change_name in subject/body
        fm_change = fm.get("change", "")
        if fm_change and fm_change == change_name:
            return str(fm.get("reply-to") or fm.get("from") or "human").strip()
        # Fallback: check if change_name appears in the message subject
        subject_match = _re.search(r"## Subject:.*" + _re.escape(change_name), text)
        if subject_match:
            return str(fm.get("reply-to") or fm.get("from") or "human").strip()

    return "human"


def cmd_complete(args: list[str], tasks_spec: str = "", mark_all: bool = False) -> int:
    """Report task completion: update tasks.md and send bus notification."""
    if not args:
        UI.error("Change name required")
        UI.muted("Usage: otaman complete <change-name> --tasks \"2.1,3.1-3.5\"")
        UI.muted("       otaman complete <change-name> --all")
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    change_name = args[0]

    if not tasks_spec and not mark_all:
        UI.error("Specify --tasks or --all")
        UI.muted("Examples:")
        UI.muted(f"  otaman complete {change_name} --tasks \"2.1, 2.3\"")
        UI.muted(f"  otaman complete {change_name} --tasks \"3.1-3.5\"")
        UI.muted(f"  otaman complete {change_name} --all")
        return 1

    UI.header("Task Completion")

    # Get agent identity: CWD→repo→owner → .agents/current-agent → "unknown-agent"
    agent = resolve_agent_identity(root) or "unknown-agent"

    # Step 1: Update tasks.md via actualize-tasks.py
    script_args = ["--change", change_name, "--agent", agent, "--project-root", str(root)]
    if mark_all:
        script_args.append("--all")
    elif tasks_spec:
        script_args.extend(["--tasks", tasks_spec])

    result = run_script("actualize-tasks.py", *script_args, capture=True)

    if result.returncode == 2:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json as _json
        report = _json.loads(result.stdout)
    except Exception:
        print(result.stdout)
        report = {}

    updated = report.get("updated", 0)
    already = report.get("already_done", 0)
    not_found = report.get("not_found", [])
    tasks_file = report.get("tasks_file", "")

    if updated > 0:
        UI.ok(f"Updated: {updated} task(s) marked complete in tasks.md")
    if already > 0:
        UI.muted(f"Already done: {already} task(s)")
    if not_found:
        UI.warn(f"Not found: {', '.join(not_found)}")
    if tasks_file:
        UI.muted(f"File: {tasks_file}")

    # Step 2: Create task-complete bus message
    # D1: locate originating task-assignment to route reply to assigner only
    # D2: fall back to 'human' if no task-assignment found (not 'all')
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    active_dir, _ = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "acks").mkdir(exist_ok=True)

    recipient = _find_task_assignment_sender(active_dir, change_name, root)

    slug = re.sub(r"[^a-z0-9]+", "-", change_name.lower()).strip("-")[:30]
    msg_id = f"{now_ts}-complete-{slug}"
    filename = f"{now_ts}-{agent}-to-{recipient.replace('/', '-')}-task-complete.md"

    task_label = "all tasks" if mark_all else f"tasks {tasks_spec}"

    content = f"""---
id: {msg_id}
from: {agent}
to: {recipient}
priority: normal
type: task-complete
change: {change_name}
timestamp: {now_iso}
status: pending
---

## Subject: Tasks complete: {change_name}

**Agent**: {agent}
**Change**: {change_name}
**Completed**: {task_label}
**Updated**: {updated} task(s) in tasks.md
**Timestamp**: {now_iso}
"""

    filepath = active_dir / filename
    filepath.write_text(content, encoding="utf-8")

    print()
    UI.ok(f"Bus notification: {filepath.relative_to(root)}")
    UI.muted(f"Type: task-complete | To: {recipient} | Change: {change_name}")

    # Step 2b: Fanout to spec_owner if set and different from primary recipient
    spec_owner = _read_spec_owner(root, change_name)
    if spec_owner and spec_owner != recipient:
        fanout_filename = f"{now_ts}-{agent}-to-{spec_owner.replace('/', '-')}-task-complete.md"
        fanout_content = content.replace(f"\nto: {recipient}\n", f"\nto: {spec_owner}\n", 1)
        fanout_path = active_dir / fanout_filename
        fanout_path.write_text(fanout_content, encoding="utf-8")
        UI.ok(f"Bus notification: {fanout_path.relative_to(root)}")
        UI.muted(f"Type: task-complete | To: {spec_owner} (spec_owner) | Change: {change_name}")

    # Step 3: Clear blocked entry if all tasks are done
    if mark_all:
        blocked_file = root / ".agents" / "blocked" / f"{agent}.md"
        if blocked_file.exists():
            blocked_content = blocked_file.read_text(encoding="utf-8")
            # Remove blocked entries for this change
            pattern = re.compile(
                rf"## Blocked:.*?{re.escape(change_name)}.*?(?=\n## |\Z)",
                re.DOTALL | re.IGNORECASE,
            )
            new_blocked = pattern.sub("", blocked_content).strip()
            if new_blocked:
                blocked_file.write_text(new_blocked + "\n", encoding="utf-8")
            else:
                blocked_file.unlink()
            UI.ok(f"Unblocked: Removed blocked entry for {change_name}")

    # agent-status-presence task 1.7 — write idle if all this agent's tasks
    # for the change are complete; otherwise write working (task=null).
    _status_hook_after_complete(root, agent, change_name)

    return 0


def _status_hook_after_complete(root: Path, agent: str, change_name: str) -> None:
    """agent-status-presence task 1.7 — refresh agent status after `complete`.

    Inspect the change's tasks.md for unchecked items assigned to this agent.
    - any unchecked → write `working` with task=null, change=<this change>
    - none unchecked → write `idle`

    Best-effort: silently no-op on any failure (file missing, parse error, etc.).
    """
    try:
        from otaman_cli.status import (
            AgentStatus, State, get_backend, is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    tasks_md = _find_tasks_md_for_change(root, change_name)
    if not tasks_md or not tasks_md.is_file():
        return

    try:
        text = tasks_md.read_text(encoding="utf-8")
    except OSError:
        return

    # An "unchecked task for this agent" looks like one of:
    #   - [ ] 1.2 @otaman-cli ...
    #   - [ ] 1.2 ... (under a "## @otaman-cli" header)
    # Be conservative: count anything `- [ ]` whose line OR enclosing section
    # mentions this agent's @-handle.
    handle = f"@otaman-{agent.replace('-agent', '')}" if agent.endswith("-agent") else f"@{agent}"
    current_section_handle: str | None = None
    has_unchecked = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Section header: `## @otaman-cli` or `## @<handle>`
        if line.startswith("## ") and "@" in line:
            current_section_handle = line.split("@", 1)[1].split()[0]
            current_section_handle = "@" + current_section_handle.rstrip()
        if line.startswith("- [ ]"):
            mine = (
                handle in line
                or (current_section_handle and current_section_handle.lower() == handle.lower())
                or f"@{agent}" in line
            )
            if mine:
                has_unchecked = True
                break

    backend = get_backend(root)
    existing = backend.read(agent)
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if has_unchecked:
        # working with task=null (per spec); preserve since if already working
        since = existing.since if (existing and existing.state == State.WORKING) else now_iso
        new_status = AgentStatus(
            agent=agent, state=State.WORKING,
            task=None, change=change_name,
            since=since, updated_at=now_iso,
        )
    else:
        # idle clears everything
        since = existing.since if (existing and existing.state == State.IDLE) else now_iso
        new_status = AgentStatus(
            agent=agent, state=State.IDLE,
            task=None, change=None, outcome=None, blocked_by=None,
            since=since, updated_at=now_iso,
        )

    try:
        backend.write(new_status)
    except Exception:
        pass


def _find_tasks_md_for_change(root: Path, change_name: str) -> Path | None:
    """Locate `openspec/changes/<change>/tasks.md` in the specs repo.

    Resolution order:
      1. ../<root>-specs/openspec/changes/<change>/tasks.md
      2. Any directory `repos[].path` with name ending `-specs`
      3. Sibling sister directory `<project>-specs` of the meta repo
      4. Direct path: ../otaman-specs/openspec/changes/<change>/tasks.md
    """
    import yaml as _yaml
    candidates: list[Path] = []
    pyaml = root / "platform.yaml"
    if pyaml.is_file():
        try:
            doc = _yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        except Exception:
            doc = {}
        repos = doc.get("repos") if isinstance(doc, dict) else None
        if isinstance(repos, list):
            for r in repos:
                if not isinstance(r, dict):
                    continue
                p = r.get("path")
                name = r.get("name") or ""
                if not p:
                    continue
                if "specs" in str(name):
                    abs_p = (root / str(p)).resolve()
                    candidates.append(abs_p / "openspec" / "changes" / change_name / "tasks.md")
    # Fallback: well-known sibling `otaman-specs`
    candidates.append((root.parent / "otaman-specs" / "openspec" / "changes" / change_name / "tasks.md").resolve())
    # Project-specs sibling
    candidates.append((root.parent / f"{root.name}-specs" / "openspec" / "changes" / change_name / "tasks.md").resolve())
    for c in candidates:
        if c.is_file():
            return c
    return None


def cmd_approve(args: list[str], action: str = "list", comment: str = "") -> int:
    """Review and approve/reject pending spec-change-requests."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    try:
        import yaml
    except ImportError:
        UI.error("PyYAML required")
        return 2

    active_dir, acks_dir = _resolve_bus_paths(root)
    acks_dir.mkdir(parents=True, exist_ok=True)

    if not active_dir.is_dir():
        print(f"No bus directory found.")
        return 1

    # Find pending spec-change-requests (no human.ack file)
    pending = []
    for f in sorted(active_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue
            if fm.get("type") != "spec-change-request":
                continue
            # Check if already approved/rejected
            ack_file = acks_dir / f"{f.stem}.human.ack"
            if ack_file.exists():
                continue
            # Extract subject
            subject = ""
            body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            for line in body.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break
            pending.append({
                "file": f,
                "stem": f.stem,
                "fm": fm,
                "subject": subject,
                "body": body,
            })
        except (OSError, yaml.YAMLError):
            continue

    # Determine action from args if not explicit
    if args and action == "list":
        first = args[0].lower()
        if first in ("approve", "reject"):
            action = first
            args = args[1:]
        elif first != "list":
            # Treat as message ID for approval
            action = "approve"

    # LIST
    if action == "list":
        UI.header("Pending Spec Change Requests")
        if not pending:
            UI.muted("No pending spec-change-requests.")
            return 0
        for p in pending:
            fm = p["fm"]
            UI.bullet(f"from {UI.agent(fm.get('from', '?'))} [{UI.priority(fm.get('priority', 'normal'))}]")
            print(f"    {p['subject']}")
            UI.muted(f"{fm.get('timestamp', '')} | {p['stem']}")
            print()
        UI.muted("To approve: otaman approve approve <stem-or-partial>")
        UI.muted("To reject:  otaman approve reject <stem-or-partial> [-d \"reason\"]")
        return 0

    # APPROVE or REJECT — need a target
    if not args:
        if len(pending) == 1:
            target = pending[0]
        elif not pending:
            UI.error("No pending spec-change-requests")
            return 1
        else:
            UI.error("Multiple pending requests. Specify which one:")
            for p in pending:
                UI.muted(p['stem'])
            return 1
    else:
        pattern = args[0]
        # Tier 1: substring match on file stem
        matches = [p for p in pending if pattern in p["stem"]]
        # Tier 2: token-based fallback (covers logical reconstruction stems)
        if not matches and "-" in pattern:
            import fnmatch
            tokens = [tok for tok in pattern.split("-") if tok]
            if len(tokens) >= 2:
                glob_pat = "*" + "*".join(tokens) + "*"
                matches = [p for p in pending if fnmatch.fnmatch(p["stem"], glob_pat)]
        # Tier 3: frontmatter id field match (agents copy from top line of check)
        if not matches:
            for p_ in pending:
                fm_id = str(p_.get("fm", {}).get("id", ""))
                if fm_id and (fm_id == pattern or pattern in fm_id):
                    matches.append(p_)
        if not matches:
            UI.error(f"No pending request matching '{pattern}'")
            UI.muted("Tip: paste either the full file stem OR the frontmatter id: value from `otaman approve list`.")
            return 1
        if len(matches) > 1:
            UI.error(f"Multiple matches for '{pattern}':")
            for m in matches:
                UI.muted(m['stem'])
            return 1
        target = matches[0]

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    if action == "approve":
        # Create approval ack
        ack_file = acks_dir / f"{target['stem']}.human.ack"
        ack_file.write_text("approved\n", encoding="utf-8")

        # Broadcast approval
        slug = re.sub(r"[^a-z0-9]+", "-", target["subject"].lower()).strip("-")[:30]
        broadcast_file = active_dir / f"{now_ts}-human-to-all-spec-change-approved.md"
        comment_section = f"\n### Human comments\n{comment}\n" if comment else ""

        broadcast = f"""---
id: {now_ts}-approved-{slug}
from: human
to: all
priority: high
type: spec-change-approved
timestamp: {now_iso}
status: pending
---

## Subject: Approved: {target['subject'].replace('Spec change request: ', '')}

The spec-change-request from **{target['fm'].get('from', '?')}** has been **approved**.

**Original proposal**: {target['stem']}
{comment_section}
### Next steps
1. Specs will be created/updated in the specs repo (via OpenSpec or manually)
2. All agents will be notified when specs are committed (via post-commit hook)
3. Affected agents should review updated specs and adapt implementation

Use `/otaman:check` to track updates.
"""
        broadcast_file.write_text(broadcast, encoding="utf-8")

        UI.header("Proposal Approved")
        UI.ok(f"Approved: {target['subject']}")
        UI.kv("From", UI.agent(target['fm'].get('from', '?')))
        UI.kv("Ack", str(ack_file.relative_to(root)))
        UI.kv("Broadcast", str(broadcast_file.relative_to(root)))

        # Check if OpenSpec is available
        config_path = root / "platform.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            specs_format = config.get("specs", {}).get("format", "fallback")
            specs_path = config.get("specs", {}).get("path", "")
            if specs_format == "openspec" and specs_path:
                proposal_title = target["subject"].replace("Spec change request: ", "")
                print()
                UI.info("OpenSpec mode: To create the spec, run in the specs repo:")
                UI.action(f"cd {specs_path} && openspec new change \"{proposal_title}\"")
                UI.muted(f"Or use /opsx:new \"{proposal_title}\" in the specs repo Claude session")
                UI.muted(f"Then work on artifacts: openspec instructions <artifact> --change \"{proposal_title}\"")

        return 0

    elif action == "reject":
        # Create rejection ack
        ack_file = acks_dir / f"{target['stem']}.human.ack"
        ack_file.write_text("rejected\n", encoding="utf-8")

        # Notify the proposing agent
        proposer = target["fm"].get("from", "all")
        reject_file = active_dir / f"{now_ts}-human-to-{proposer}-spec-change-rejected.md"
        reason = comment or "No reason provided."

        reject_msg = f"""---
id: {now_ts}-rejected
from: human
to: {proposer}
priority: normal
type: spec-change-rejected
timestamp: {now_iso}
status: pending
---

## Subject: Rejected: {target['subject'].replace('Spec change request: ', '')}

The spec-change-request has been **rejected**.

**Reason**: {reason}

**Original proposal**: {target['stem']}
"""
        reject_file.write_text(reject_msg, encoding="utf-8")

        UI.header("Proposal Rejected")
        UI.error(f"Rejected: {target['subject']}")
        UI.kv("From", UI.agent(target['fm'].get('from', '?')))
        UI.kv("Reason", reason)
        UI.kv("Notification sent to", UI.agent(proposer))
        return 0

    return 0


def cmd_assign(args: list[str]) -> int:
    """Map tasks from OpenSpec tasks.md to repo owners and notify agents."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Task Assignment")

    if not args:
        # Auto-detect: scan OpenSpec changes/ for tasks.md files
        try:
            import yaml
            config_path = root / "platform.yaml"
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                specs_path = config.get("specs", {}).get("path", "")
                if specs_path:
                    changes_dir = root / specs_path / "openspec" / "changes"
                    if changes_dir.is_dir():
                        tasks_files = list(changes_dir.glob("*/tasks.md"))
                        # Exclude archived
                        tasks_files = [t for t in tasks_files if "archive" not in str(t)]
                        if tasks_files:
                            UI.info(f"Found {len(tasks_files)} tasks.md file(s):")
                            print()
                            for t in tasks_files:
                                rel = t.relative_to(root)
                                UI.muted(str(rel))
                            print()
                            UI.muted("Run: otaman assign <path-to-tasks.md-or-feature-dir>")
                            return 0
        except Exception:
            pass
        UI.error("Path to tasks.md or OpenSpec feature directory required")
        UI.muted("Usage: otaman assign <path-to-tasks.md>")
        UI.muted("       otaman assign openspec/changes/my-feature")
        return 1

    target = args[0]
    result = run_script("map-tasks.py", target, capture=True)
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    feature = report.get("feature", "?")
    total = report.get("total_tasks", 0)
    assigned = report.get("assigned", 0)
    unassigned = report.get("unassigned", 0)
    done = report.get("done", 0)
    pending = report.get("pending", 0)

    if feature and feature != "?":
        assign_agent = resolve_agent_identity(root) or "unknown-agent"
        _write_spec_owner(root, feature, assign_agent)

    UI.kv("Feature", feature, C.BOLD)
    UI.kv("Tasks", f"{total} total ({done} done, {pending} pending)")
    UI.kv("Assigned", f"{C.GREEN}{assigned}{C.RESET} | Unassigned: {C.YELLOW}{unassigned}{C.RESET}")

    by_owner = report.get("by_owner", {})
    if by_owner:
        UI.subheader("Assignments:")
        for owner, tasks in sorted(by_owner.items()):
            UI.bullet(f"{UI.agent(owner)}: {len(tasks)} task(s)")
            for t in tasks[:3]:
                UI.muted(f"- {t}")
            if len(tasks) > 3:
                UI.muted(f"... and {len(tasks) - 3} more")

    unassigned_tasks = report.get("unassigned_tasks", [])
    if unassigned_tasks:
        UI.subheader("Unassigned tasks:")
        for t in unassigned_tasks:
            UI.bullet(t, icon="-", color=C.YELLOW)
        UI.muted("Add @repo-name or **repo-name**: prefix to tasks.md to assign")

    created = report.get("bus_messages_created", [])
    if created:
        print()
        UI.ok("Bus messages created:")
        for c in created:
            UI.muted(c)

    # Task 4.2: @solution:<id> annotation scan + validation against solutions.yaml
    from otaman_cli.registries.assign_annotations import (
        resolve_tasks_md_path,
        scan_tasks_md,
    )
    tasks_md = resolve_tasks_md_path(target)
    if tasks_md is not None:
        findings = scan_tasks_md(tasks_md, root)
        if findings.has_findings:
            print()
            UI.subheader("Solution annotations (@solution:)")
            by_id: dict[str, list] = {}
            for ann in findings.annotations:
                by_id.setdefault(ann.solution_id, []).append(ann)
            for sol_id, anns in sorted(by_id.items()):
                marker = "✓" if sol_id in findings.valid_ids else "✗"
                UI.bullet(f"{marker} {sol_id} — {len(anns)} task(s)")
                if sol_id in findings.missing_ids:
                    UI.muted(f"    not found in solutions.yaml")
            if findings.missing_ids:
                print()
                UI.warn(
                    f"{len(findings.missing_ids)} unknown solution id(s) referenced. "
                    "Either add via `otaman solution add` or fix the annotation."
                )

        # Task 3.1 (auto-session-spawn): mode annotations [headless]/[interactive]
        # in task lines. Report counts in the assign summary so the user can
        # eyeball how many tasks the spawn-decision component will pick up
        # vs. how many still need explicit annotation.
        try:
            from otaman_cli.hitl.mode_annotations import (
                ModeAnnotationError,
                ModeSummary,
                scan_tasks_md as _mode_scan,
            )
            try:
                _mode_result = _mode_scan(tasks_md)
            except ModeAnnotationError as exc:
                print()
                UI.error(f"Mode annotation error in tasks.md: {exc}")
                return 1
            if _mode_result is not None:
                _tasks_mode, _summary = _mode_result
                if _summary.headless or _summary.explicit_count or _summary.default_count:
                    print()
                    UI.subheader("Task modes ([headless] / [interactive])")
                    UI.bullet(f"[headless]     {_summary.headless} task(s)")
                    UI.bullet(f"[interactive]  {_summary.interactive} task(s)")
                    if _summary.default_count:
                        UI.muted(
                            f"  ({_summary.default_count} defaulted to [interactive] "
                            "— add explicit annotations to silence)"
                        )
        except Exception as _mode_exc:
            UI.warn(f"Mode annotation scan skipped: {_mode_exc}")

    return 0


def cmd_review(args: list[str], reviewer: str = "all") -> int:
    """Trigger a review."""
    UI.info("Observer reviews are designed to run inside Claude Code sessions")
    print(f"  where the observer agents have access to Read/Glob/Grep/Bash tools.")
    print()
    UI.action(f"Run in your Claude Code session:")
    UI.muted(f"/otaman:review --reviewer {reviewer}")
    if args:
        UI.kv("Scope", " ".join(args))
    return 0


def _normalize_ce_platform_yaml_for_validation(config_path: Path) -> tuple[Path, list[str]]:
    """ce-org-agent-bootstrap task 4.1 — normalize CE-shaped platform.yaml in-memory.

    The schema in otaman-core requires `project`, `version`, and per-repo
    `owner`.  CE bootstrap historically wrote `agent:` per repo and
    sometimes omitted `project:` / `version:`.  This helper:

      - Treats `agent:` as alias for `owner:` on each repo entry (and
        strips `agent:` from the validation copy since repo items have
        `additionalProperties: false` in the schema)
      - Infers `project:` from the parent dir name when absent
      - Defaults `version:` to "1.0" when absent
      - Injects a synthetic placeholder repo into the validation copy
        when `repos:` is empty AND a CE-scaffold marker (`runner:` or
        `terminal:`) is present, so the schema's `repos: minItems: 1`
        check passes
      - Returns a path to a tmp file holding the normalized YAML when any
        change was made; otherwise returns the original path
      - Returns a list of human-readable hints for the caller to surface

    The on-disk source file is NEVER modified by this helper.  When changes
    were applied, the caller is responsible for unlinking the returned tmp
    path after the validator runs.

    History:
      - 2026-06-09 (PR #54): original 3 normalizations (agent→owner,
        project, version)
      - 2026-06-09 (PR #55): stripped `runner:` / `terminal:` /
        `agent_bootstrap:` as pass-through pending schema extension
      - 2026-06-10: otaman-core commit 27f2c7c allowlisted those root
        keys (and `bus:`, `agents:`, `orgs:`, `agent_presence:`, plus the
        program-init wizard set).  Pass-through stripping was removed in
        the follow-up cleanup; only the empty-repos placeholder remains
        for the CE org-dir scaffold use case.
    """
    import yaml as _yaml
    import tempfile as _tmp

    hints: list[str] = []
    if not config_path.is_file():
        # Defer to the validator; it will report the missing-file error itself
        return config_path, hints

    try:
        text = config_path.read_text(encoding="utf-8")
        doc = _yaml.safe_load(text) or {}
    except Exception:
        return config_path, hints

    if not isinstance(doc, dict):
        return config_path, hints

    changed = False

    # 1. version default
    if "version" not in doc:
        doc["version"] = "1.0"
        hints.append(
            "platform.yaml: `version:` field missing — defaulted to \"1.0\" for validation. "
            "Add `version: \"1.0\"` to the canonical file to silence this hint."
        )
        changed = True

    # 2. project inferred from parent dir
    if "project" not in doc or not doc.get("project"):
        try:
            parent = config_path.resolve().parent
            inferred = parent.name or "ce-org"
            # Sanitize: lowercase, replace anything non-[a-z0-9-] with '-'
            import re as _re
            inferred = _re.sub(r"[^a-z0-9-]+", "-", inferred.lower()).strip("-") or "ce-org"
        except Exception:
            inferred = "ce-org"
        doc["project"] = inferred
        hints.append(
            f"platform.yaml: `project:` field missing — inferred {inferred!r} from "
            "parent directory name. Add `project: <slug>` to the canonical file."
        )
        changed = True

    # 3. repos: agent → owner alias.  The schema has additionalProperties:
    # false, so we must DROP `agent:` from the validation-time copy after
    # promoting it.  The user's on-disk file is untouched.
    repos = doc.get("repos")
    if isinstance(repos, list):
        promoted = 0
        for r in repos:
            if not isinstance(r, dict):
                continue
            agent_val = r.get("agent")
            owner_val = r.get("owner")
            if agent_val and not owner_val:
                r["owner"] = agent_val
                promoted += 1
            # Strip `agent:` from the validation copy whether or not we
            # promoted (e.g. if both were set, agent is still unknown).
            if "agent" in r:
                r.pop("agent", None)
                changed = True
        if promoted:
            hints.append(
                f"platform.yaml: {promoted} repo entry(ies) use `agent:` field — "
                "aliased to `owner:` for validation. Add `owner:` alongside "
                "`agent:` (same value) in the canonical file."
            )
            changed = True

    # 4. Empty / missing `repos:` for fresh CE org-dir scaffolds.  The
    # schema's `repos: minItems: 1` constraint remains even after the
    # 2026-06-10 schema extension (otaman-core commit 27f2c7c) that
    # allowlisted `runner:` / `terminal:` / `bus:` / etc. as native root
    # keys.  Detect "CE org-dir scaffold" mode by the presence of a
    # `runner:` or `terminal:` block (both first-class in the schema now,
    # so we read them without stripping).  Inject a single synthetic
    # placeholder repo into the validation copy so the `minItems: 1`
    # check passes; the on-disk file's empty `repos:` is preserved.
    _CE_SCAFFOLD_MARKERS = ("runner", "terminal")
    has_ce_marker = any(k in doc for k in _CE_SCAFFOLD_MARKERS)
    if (not isinstance(doc.get("repos"), list) or len(doc.get("repos") or []) == 0) \
            and has_ce_marker:
        # Schema requires name to match ^[A-Za-z][A-Za-z0-9._-]{1,63}$
        # and owner to match ^[a-z][a-z0-9-]{1,63}$.
        doc["repos"] = [{
            "name": "ce-org-placeholder",
            "path": ".",
            "owner": "ops-agent",
        }]
        hints.append(
            "platform.yaml: empty/missing `repos:` accepted for fresh "
            "CE org-dir scaffold (detected via runner:/terminal: marker). "
            "Programs will populate `repos:` as they are added; canonical "
            "files should list at least one repo."
        )
        changed = True

    if not changed:
        return config_path, hints

    # Write the normalized doc to a tmp file in the same directory so the
    # validator's relative-path resolution behaves the same way.
    parent_dir = config_path.parent
    try:
        fd, tmp_name = _tmp.mkstemp(
            prefix=".otaman-ce-norm-", suffix=".yaml", dir=str(parent_dir),
        )
    except OSError:
        # Fall back to system tmp if the parent dir isn't writable
        fd, tmp_name = _tmp.mkstemp(prefix=".otaman-ce-norm-", suffix=".yaml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return config_path, hints
    return tmp_path, hints


def cmd_validate(args: list[str]) -> int:
    """Validate platform.yaml.

    ce-org-agent-bootstrap task 4.1 — accepts the CE platform.yaml shape
    by normalizing in-memory before validation (agent→owner alias; project
    inferred from parent dir; version default "1.0").  Deprecation hints
    are printed; the on-disk file is not rewritten.
    """
    config = args[0] if args else "platform.yaml"
    config_path = Path(config)
    norm_path, hints = _normalize_ce_platform_yaml_for_validation(config_path)
    if hints:
        for h in hints:
            UI.muted(f"hint: {h}")
    try:
        result = run_script("validate-platform.py", str(norm_path))
    finally:
        if norm_path != config_path and norm_path.exists():
            try:
                norm_path.unlink()
            except OSError:
                pass
    return result.returncode


def cmd_compliance(args: list[str], fmt: str = "markdown") -> int:
    """Generate compliance report."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1
    result = run_script("compliance-report.py", str(root), "--format", fmt)
    return result.returncode


def cmd_validate_messages(args: list[str]) -> int:
    """Validate bus message files."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Bus Message Validation")

    if args:
        # Validate specific file
        result = run_script("validate-message.py", args[0])
    else:
        # Validate all active messages
        result = run_script("validate-message.py", str(root), "--all")
    return result.returncode


def cmd_migrate(args: list[str]) -> int:
    """Migrate existing otaman deployment to a dedicated otaman folder."""
    UI.header("Otaman Migrate")

    # Find existing project root (old layout: platform.yaml in a non-git parent)
    root = find_project_root()
    if not root:
        UI.error("No platform.yaml or .agents/ found")
        UI.muted("Run from within an existing otaman-managed project.")
        return 1

    # Check if already in a git repo (might already be migrated)
    git_dir = root / ".git"
    if git_dir.is_dir():
        UI.warn(f"{root} already has a .git/ directory")
        print(f"  This may already be a dedicated otaman folder. Proceed with caution.\n")

    # Determine otaman folder name
    config_path = root / "platform.yaml"
    if not config_path.exists():
        UI.error(f"platform.yaml not found at {root}")
        return 1

    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        project_name = config.get("project", root.name)
    except Exception:
        project_name = root.name

    if args:
        maestro_name = args[0]
    else:
        maestro_name = f"{project_name}-otaman"

    maestro_dir = root / maestro_name
    if maestro_dir.exists() and any(maestro_dir.iterdir()):
        UI.error(f"{maestro_dir} already exists and is not empty")
        return 1

    UI.kv("Migrating from", str(root), C.BOLD)
    UI.kv("Otaman folder", str(maestro_dir), C.BOLD)
    print()

    # Create otaman folder
    maestro_dir.mkdir(parents=True, exist_ok=True)

    # Move artifacts
    moved: list[str] = []
    for item_name in ("platform.yaml", ".agents", ".claude"):
        src = root / item_name
        dst = maestro_dir / item_name
        if src.exists():
            import shutil
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
                shutil.rmtree(str(src))
            else:
                shutil.copy2(str(src), str(dst))
                src.unlink()
            moved.append(item_name)
            UI.ok(f"Moved {item_name}")

    # Move launch scripts
    for pattern in ("launch-agents.ps1", "launch-agents.sh", "LAUNCH-AGENTS.md"):
        src = root / pattern
        dst = maestro_dir / pattern
        if src.exists():
            import shutil
            shutil.copy2(str(src), str(dst))
            src.unlink()
            moved.append(pattern)
            UI.ok(f"Moved {pattern}")

    if not moved:
        UI.warn(f"Nothing to migrate — no otaman artifacts found at {root}")
        return 1

    # Rewrite repo paths in platform.yaml: ./repo -> ../repo
    new_config_path = maestro_dir / "platform.yaml"
    content = new_config_path.read_text(encoding="utf-8")
    # Replace ./repo paths with ../repo (otaman folder is now one level deeper)
    import re
    content = re.sub(r'path:\s*\./([^\s]+)', r'path: ../\1', content)
    # Also fix specs.path if it points to a sibling
    content = re.sub(r'(specs:\s*\n\s*path:\s*)\./([^\s]+)', r'\1../\2', content)
    new_config_path.write_text(content, encoding="utf-8")
    UI.ok("Rewrote repo paths in platform.yaml (./repo -> ../repo)")

    # Git init
    subprocess.run(["git", "init", str(maestro_dir)], capture_output=True)
    UI.ok(f"Initialized git repo in {maestro_name}/")

    # Generate .gitignore
    gitignore = maestro_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Runtime artifacts (not versioned)\n"
            ".agents/bus/\n"
            ".agents/blocked/\n"
            ".agents/queue/\n"
            ".agents/sessions/\n"
            ".agents/current-agent\n",
            encoding="utf-8",
        )
        UI.ok("Created .gitignore")

    # Write .otaman markers in each repo (includes agent: <owner> field per D2)
    try:
        with open(new_config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for repo in config.get("repos", []):
            repo_dir = (maestro_dir / repo["path"]).resolve()
            owner = repo.get("owner", "")
            if repo_dir.is_dir():
                rel = os.path.relpath(maestro_dir.resolve(), repo_dir)
                rel_posix = Path(rel).as_posix()
                marker = repo_dir / ".otaman"
                agent_line = ("agent: " + owner + chr(10)) if owner else ""
                marker.write_text(
                    f"# Path to otaman folder\n{rel_posix}\n{agent_line}",
                    encoding="utf-8",
                )
                # Append to repo .gitignore
                gi = repo_dir / ".gitignore"
                needs_entry = True
                if gi.exists():
                    gi_content = gi.read_text(encoding="utf-8")
                    if ".otaman" in gi_content.splitlines():
                        needs_entry = False
                if needs_entry:

                    with open(gi, "a", encoding="utf-8") as f:
                        f.write(chr(10) + ".otaman" + chr(10))

                label = f" (agent: {owner})" if owner else ""
                UI.ok(f"Marker {repo['name']}/.otaman -> {rel_posix}{label}")
        # Also write agent: human to otaman-meta itself (D5)
        meta_marker = maestro_dir / ".otaman"
        if meta_marker.exists():
            existing = meta_marker.read_text(encoding="utf-8")
            if "agent:" not in existing:
                meta_marker.write_text(existing.rstrip() + chr(10) + "agent: human" + chr(10), encoding="utf-8")


    except Exception as e:
        UI.warn(f"Could not write .otaman markers: {e}")

    # Initial commit
    subprocess.run(
        ["git", "-C", str(maestro_dir), "add", "-A"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(maestro_dir), "commit", "-m", "Initial otaman migration"],
        capture_output=True,
    )
    UI.ok("Committed initial state")

    print()
    UI.ok("Migration complete!")
    UI.muted("Next steps:")
    UI.muted(f"  1. cd {maestro_dir}")
    UI.muted("  2. Review platform.yaml (verify repo paths)")
    UI.muted("  3. otaman init  (reinstall hooks with new paths)")
    UI.muted("  4. Launch agents from the otaman folder")
    return 0


def cmd_models(args: list[str]) -> int:
    """Show model/effort defaults shipped with the plugin and diff vs platform.yaml overrides."""
    UI.header("Otaman Model/Effort Report")
    try:
        result = run_script("models-report.py", *args)
        return result.returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_accounts(args: list[str]) -> int:
    """Manage Claude Code account definitions in launch-settings.yaml.

    Subcommands: add, list, remove. Forwards to scripts/accounts.py with
    its own argparse-based arg handling.
    """
    UI.header("Otaman Accounts")
    try:
        result = run_script("accounts.py", *args)
        return result.returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_ping(args: list[str]) -> int:
    """Post a Telegram / bridge notification immediately.

    Forwards to scripts/ping.py. Unlike the automatic Stop-hook
    notification, this is always delivered regardless of AFK state
    and without debounce — it's an explicit user/agent-invoked call.
    """
    UI.header("Otaman Ping")
    try:
        result = run_script("ping.py", *args)
        return result.returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_afk(args: list[str]) -> int:
    """Toggle remote-approval AFK mode (.otaman/afk flag file).

    Subcommands: on [DURATION], off, status. Forwards to scripts/afk.py.
    """
    UI.header("Otaman AFK")
    try:
        result = run_script("afk.py", *args)
        return result.returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_bridge(args: list[str]) -> int:
    """Run / status / stop the remote-approval bridge daemon.

    Forwards to bridge/cli.py. T2a ships null transport only; T2b adds
    Telegram. The daemon runs one per account; it listens on loopback
    HTTP for PreToolUse hook requests and surfaces them via the
    configured transport.
    """
    UI.header("Otaman Bridge")
    try:
        result = run_script("bridge/cli.py", *args)
        return result.returncode
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_mcp_config(args: list[str]) -> int:
    """Emit a Claude Code .mcp.json snippet pointing at the bridge.

    Forwards to otaman_cli.mcp_config. Reads the cached OIDC token
    from `otaman login` and prints (or writes) the JSON block Claude
    Code needs to connect to the bridge's MCP endpoint with the right
    bearer auth.
    """
    UI.header("Otaman MCP Config")
    try:
        from otaman_cli.mcp_config import main as mcp_main
        return mcp_main(args)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_session(args: list[str]) -> int:
    """Manage otaman sessions: spawn (more to come).

    Subcommands:
        spawn   Spawn a session via the local runner under the
                logged-in user's identity (reads token from
                `otaman login` cache).
    """
    UI.header("Otaman Session")
    if not args:
        UI.error("Missing subcommand")
        UI.muted("Usage: otaman session <spawn> [args...]")
        return 1
    sub, rest = args[0], args[1:]
    try:
        if sub == "spawn":
            from otaman_cli.session_spawn import main as spawn_main
            return spawn_main(rest)
        UI.error(f"Unknown session subcommand: {sub}")
        UI.muted("Usage: otaman session <spawn> [args...]")
        return 1
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_git_host(args: list[str]) -> int:
    """Manage git host (GitHub / GitLab / Bitbucket / Azure DevOps) integration.

    Subcommands:
      detect [REPO]     Classify origin's git remote and print provider/slug.
      add               Interactive: walk the user through wiring a PAT.
      check             Load platform.yaml git_host:, resolve token, validate.
      list              Show git_host: config + origin-remote summary per repo.
    """
    UI.header("Otaman Git Host")
    sub = (args[0] if args else "list").lower()
    rest = args[1:]

    try:
        from otaman_core import git_host as gh
    except ImportError as e:
        UI.error(f"Failed to import git_host module: {e}")
        return 1

    if sub == "detect":
        target = Path(rest[0] if rest else ".").resolve()
        info = gh.detect_remote_for_repo(target)
        if info is None:
            UI.error(f"No parsable git remote found in {target}")
            return 1
        UI.kv("Repo", str(target))
        UI.kv("Provider", info.provider)
        UI.kv("Host", info.host)
        UI.kv("Slug", info.slug)
        if info.is_self_hosted:
            UI.muted("(self-hosted — host alone doesn't identify provider; "
                     "set `git_host.provider` explicitly)")
        return 0 if info.provider != "unknown" else 2

    if sub == "list":
        root = find_project_root()
        if not root:
            UI.error("Not in an otaman project")
            return 1
        cfg = gh.load_git_host_config(root)
        if cfg:
            UI.info("Configured git_host:")
            UI.kv("  Provider", cfg.provider)
            UI.kv("  Host", cfg.host)
            sources = ", ".join(
                s.get("type", "?") + ":" + str(s.get("name") or s.get("account") or "?")
                for s in cfg.token_ref.sources
            )
            UI.kv("  Token source chain", sources or "(empty)")
        else:
            UI.muted("No `git_host:` block in platform.yaml (run "
                     "`otaman git-host add` to wire one).")
        UI.info("Detected origin remotes:")
        remotes = gh.detect_remotes_for_maestro(root)
        if not remotes:
            UI.muted("  (no repos in platform.yaml)")
            return 0
        for name, info in remotes:
            if info is None:
                UI.muted(f"  {name:<25}  (no remote / not a git repo)")
            else:
                UI.kv(f"  {name}", f"{info.provider} · {info.slug}  [{info.host}]")
        return 0

    if sub == "check":
        root = find_project_root()
        if not root:
            UI.error("Not in an otaman project")
            return 1
        cfg = gh.load_git_host_config(root)
        if cfg is None:
            UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
            return 1
        result = gh.resolve_and_validate(cfg, maestro_root=root)
        if result.ok:
            UI.ok(f"{cfg.provider} token valid "
                  f"(authenticated as {result.identity or '?'})")
            if result.scopes:
                UI.kv("  Scopes", ", ".join(result.scopes))
            return 0
        UI.error(f"Token validation failed: {result.error}")
        return 2

    if sub == "add":
        return _git_host_add_interactive(gh, rest)

    if sub == "pr":
        return _git_host_pr(gh, rest)

    if sub == "post-review":
        return _git_host_post_review(gh, rest)

    UI.error(f"Unknown subcommand: {sub}")
    UI.muted("Usage: otaman git-host [detect|list|check|add|pr|post-review] [args...]")
    return 1


def _git_host_current_branch(repo_dir: Path) -> str | None:
    """Best-effort ``git rev-parse --abbrev-ref HEAD`` in repo_dir."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name if name and name != "HEAD" else None


def _git_host_resolve_repo(gh, root: Path, repo_arg: str | None):
    """Pick which managed repo the `pr` subcommand applies to.

    - If --repo=<name> given, use that entry from platform.yaml.
    - Else if running inside a managed repo, use it.
    - Else if only one repo is configured, use it.
    - Else error out listing the choices.

    Returns (repo_dir, RemoteInfo) or raises UserError via UI.error.
    """
    import yaml
    platform_yaml = root / "platform.yaml"
    data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {} \
        if platform_yaml.is_file() else {}
    repos = [r for r in (data.get("repos") or []) if isinstance(r, dict)]

    cwd = Path.cwd().resolve()
    chosen = None
    if repo_arg:
        chosen = next((r for r in repos if r.get("name") == repo_arg), None)
        if chosen is None:
            return None, None
    else:
        # Prefer repo whose path contains cwd.
        for r in repos:
            path = r.get("path")
            if not path:
                continue
            resolved = (root / path).resolve()
            if cwd == resolved or cwd.is_relative_to(resolved):
                chosen = r
                break
        if chosen is None and len(repos) == 1:
            chosen = repos[0]

    if chosen is None:
        return None, None
    repo_dir = (root / chosen["path"]).resolve()
    info = gh.detect_remote_for_repo(repo_dir)
    return repo_dir, info


def _git_host_pr(gh, args: list[str]) -> int:
    """`otaman git-host pr list|get|for-branch|comment`"""
    if not args:
        UI.error("Missing subcommand")
        UI.muted("Usage: otaman git-host pr [list|get|for-branch|comment] [args...]")
        return 1

    action = args[0].lower()
    rest = args[1:]

    # Parse --repo NAME and --body TEXT out of rest.
    repo_arg: str | None = None
    body_arg: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--repo" and i + 1 < len(rest):
            repo_arg = rest[i + 1]
            i += 2
        elif rest[i] == "--body" and i + 1 < len(rest):
            body_arg = rest[i + 1]
            i += 2
        else:
            positional.append(rest[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    cfg = gh.load_git_host_config(root)
    if cfg is None:
        UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
        return 1

    repo_dir, info = _git_host_resolve_repo(gh, root, repo_arg)
    if info is None or info.provider == "unknown":
        UI.error(
            f"Can't determine repo slug. "
            f"Pass --repo <name> or run inside a managed repo with a parseable origin."
        )
        return 1

    try:
        adapter = gh.get_adapter(cfg, maestro_root=root)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    slug = info.slug
    try:
        if action == "list":
            prs = adapter.list_open_prs(slug)
            if not prs:
                UI.muted(f"No open PRs in {slug}")
                return 0
            UI.info(f"Open PRs in {slug}:")
            for pr in prs:
                draft = " [DRAFT]" if pr.draft else ""
                UI.kv(f"  #{pr.number}",
                      f"{pr.title}{draft}  by {pr.author}  ({pr.head_ref} → {pr.base_ref})")
            return 0

        if action == "get":
            if not positional:
                UI.error("Missing PR number: otaman git-host pr get <number>")
                return 1
            try:
                number = int(positional[0])
            except ValueError:
                UI.error(f"Invalid PR number: {positional[0]!r}")
                return 1
            pr = adapter.get_pr(slug, number)
            UI.kv("Number", f"#{pr.number}")
            UI.kv("Title", pr.title)
            UI.kv("State", pr.state + (" (draft)" if pr.draft else ""))
            UI.kv("Author", pr.author)
            UI.kv("Branches", f"{pr.head_ref} → {pr.base_ref}")
            UI.kv("SHA", pr.head_sha[:12])
            UI.kv("URL", pr.url)
            return 0

        if action == "for-branch":
            branch = positional[0] if positional else None
            if branch is None:
                branch = _git_host_current_branch(repo_dir or Path.cwd())
            if not branch:
                UI.error("Can't determine branch name (pass it as argument)")
                return 1
            pr = adapter.get_pr_for_branch(slug, branch)
            if pr is None:
                UI.muted(f"No open PR for {slug}:{branch}")
                return 0
            UI.kv("PR", f"#{pr.number} — {pr.title}")
            UI.kv("URL", pr.url)
            return 0

        if action == "comment":
            if not positional:
                UI.error("Missing PR number: otaman git-host pr comment <number> "
                         "[--body TEXT | via stdin]")
                return 1
            try:
                number = int(positional[0])
            except ValueError:
                UI.error(f"Invalid PR number: {positional[0]!r}")
                return 1
            body = body_arg
            if body is None:
                # Read from stdin if not given.
                if sys.stdin.isatty():
                    UI.error("--body TEXT required (or pipe body on stdin)")
                    return 1
                body = sys.stdin.read()
            if not body.strip():
                UI.error("Comment body is empty")
                return 1
            c = adapter.post_comment(slug, number, body)
            UI.ok(f"Posted comment #{c.id}")
            UI.kv("URL", c.url)
            return 0

        UI.error(f"Unknown pr subcommand: {action}")
        return 1
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2
    except ValueError as e:
        UI.error(str(e))
        return 1


def _git_host_post_review(gh, args: list[str]) -> int:
    """`otaman git-host post-review [REVIEW_FILE] [--pr N] [--repo NAME]`

    Reads a review artifact from .agents/reviews/ (or the explicit path
    given) and posts it as a PR comment. Uses the current branch's PR
    if --pr isn't given. Prints a link to the posted comment.
    """
    pr_number: int | None = None
    repo_arg: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--pr" and i + 1 < len(args):
            try:
                pr_number = int(args[i + 1])
            except ValueError:
                UI.error(f"Invalid --pr value: {args[i + 1]!r}")
                return 1
            i += 2
        elif args[i] == "--repo" and i + 1 < len(args):
            repo_arg = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    cfg = gh.load_git_host_config(root)
    if cfg is None:
        UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
        return 1

    # Find the review file.
    review_path: Path | None = None
    if positional:
        candidate = Path(positional[0])
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not candidate.is_file():
            UI.error(f"Review file not found: {candidate}")
            return 1
        review_path = candidate
    else:
        pending_dir = root / ".agents" / "reviews" / "pending"
        if not pending_dir.is_dir():
            UI.error(f"No .agents/reviews/pending/ directory at {root}")
            return 1
        reviews = sorted(pending_dir.glob("*.md"))
        if not reviews:
            UI.error(
                "No review files in .agents/reviews/pending/ — "
                "run /otaman:review first, or pass a path explicitly."
            )
            return 1
        review_path = reviews[-1]  # most recent
        UI.muted(f"Using latest review: {review_path.name}")

    body = review_path.read_text(encoding="utf-8")
    if not body.strip():
        UI.error(f"Review file is empty: {review_path}")
        return 1

    # Resolve repo + PR.
    repo_dir, info = _git_host_resolve_repo(gh, root, repo_arg)
    if info is None or info.provider == "unknown":
        UI.error(
            "Can't determine repo slug. "
            "Pass --repo <name> or run inside a managed repo."
        )
        return 1

    try:
        adapter = gh.get_adapter(cfg, maestro_root=root)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    if pr_number is None:
        branch = _git_host_current_branch(repo_dir or Path.cwd())
        if not branch:
            UI.error("Can't determine current branch — pass --pr N")
            return 1
        try:
            pr = adapter.get_pr_for_branch(info.slug, branch)
        except gh.GitHostError as e:
            UI.error(str(e))
            return 2
        if pr is None:
            UI.error(f"No open PR for {info.slug}:{branch} (pass --pr N explicitly)")
            return 1
        pr_number = pr.number
        UI.muted(f"Resolved PR: #{pr_number} ({pr.title})")

    # legacy: wrap with plugin attribution; repo still named maestro-plugin on GitHub
    wrapped = (
        f"> _Posted by [otaman-plugin](https://github.com/inprimex/maestro-plugin) "  # legacy: GitHub repo not yet renamed
        f"from `{review_path.name}`_\n\n"
        f"{body.rstrip()}\n"
    )

    try:
        c = adapter.post_comment(info.slug, pr_number, wrapped)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    UI.ok(f"Posted review as comment on {info.slug}#{pr_number}")
    UI.kv("Comment", f"#{c.id}")
    UI.kv("URL", c.url)
    return 0


def _git_host_add_interactive(gh, args: list[str]) -> int:
    """Walk the user through wiring a PAT: detect, confirm, print
    exactly the lines to add to platform.yaml + .otaman/secrets.env."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # Try to auto-detect from the first repo that has a remote.
    remotes = gh.detect_remotes_for_maestro(root)
    detected = next((info for _name, info in remotes if info is not None), None)

    if detected and detected.provider != "unknown":
        UI.ok(f"Detected {detected.provider} at {detected.host} "
              f"(from {detected.slug})")
        provider = detected.provider
        host = detected.host
    else:
        if detected:
            UI.muted(f"Remote host {detected.host} is self-hosted — pick a provider.")
        UI.info("Supported: github / gitlab / bitbucket / azure-devops")
        provider = input("Provider: ").strip().lower()
        if provider not in ("github", "gitlab", "bitbucket", "azure-devops"):
            UI.error(f"Unknown provider: {provider!r}")
            return 1
        default_host = gh.default_host_for(provider)
        host_input = input(f"Host [{default_host}]: ").strip()
        host = host_input or default_host

    # Token env var name.
    default_env = f"OTAMAN_{provider.upper().replace('-', '_')}_TOKEN"
    env_name = input(f"Env var name for the PAT [{default_env}]: ").strip() or default_env

    UI.info("")
    UI.info("To finish setup:")
    UI.info("")
    UI.action(f"1. Generate a PAT on {host} with the scopes you need "
              f"(read-only is enough for Phase 1).")
    UI.info("")
    UI.action(f"2. Add the token to .otaman/secrets.env "
              f"(gitignored, mode 0600):")
    UI.muted(f"   echo '{env_name}=<paste-token-here>' >> .otaman/secrets.env")
    UI.muted(f"   chmod 600 .otaman/secrets.env")
    UI.info("")
    UI.action(f"3. Add this block to platform.yaml:")
    UI.muted("")
    UI.muted(f"   git_host:")
    UI.muted(f"     provider: {provider}")
    UI.muted(f"     host: {host}")
    UI.muted(f"     token:")
    UI.muted(f"       sources:")
    UI.muted(f"         - {{ type: env,    name: {env_name} }}")
    UI.muted(f"         - {{ type: dotenv, name: {env_name} }}")
    UI.muted("")
    UI.action(f"4. Verify: `otaman git-host check`")
    return 0


def cmd_install_cli(args: list[str]) -> int:
    """Put the `otaman` command on PATH (POSIX symlink or Windows setx).

    Delegates to scripts/install_cli.py. Default mode is dry-run: the
    command prints what it *would* change. Pass ``--apply`` to actually
    edit PATH / create the symlink.
    """
    UI.header("Otaman Install CLI")
    try:
        return run_script("install_cli.py", *args).returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def cmd_launcher(args: list[str]) -> int:
    """Launcher management: scaffold, list, add, remove, register.

    Subcommands:
      otaman launcher list              -- show registered launchers
      otaman launcher add <path>         -- register manually
      otaman launcher remove <path>      -- unregister
      otaman launcher register <path>    -- silent register (used by launcher hooks)
      otaman launcher <target> [opts]    -- scaffold a new launcher folder
    """
    if not args:
        UI.error("subcommand or target folder required")
        UI.muted("Usage:")
        UI.muted("  otaman launcher list                    -- show registered launchers")
        UI.muted("  otaman launcher add <path>              -- register manually")
        UI.muted("  otaman launcher remove <path>           -- unregister")
        UI.muted("  otaman launcher register <path>         -- silent register (hook use)")
        UI.muted("  otaman launcher <target> [...flags]      -- scaffold a new launcher folder")
        return 1

    sub = args[0].lower()

    # Registry-management subcommands
    if sub in ("list", "add", "remove", "register"):
        try:
            from otaman_cli import _launchers_registry as reg  # type: ignore
        except ImportError as e:
            UI.error(f"Failed to load registry helper: {e}")
            return 1

        if sub == "list":
            entries = reg.list_entries()
            if not entries:
                UI.muted("No launchers registered. They auto-register on first launch, or:")
                UI.muted("  otaman launcher add <path>")
                return 0
            UI.header(f"Registered Launchers ({len(entries)})")
            for entry in entries:
                exists = Path(entry["path"]).is_dir()
                marker = "" if exists else f" {C.RED}[missing]{C.RESET}"
                last_used = entry.get("last_used", "?")
                print(f"  {entry['path']}{marker}")
                UI.muted(f"    last used: {last_used}")
            return 0

        if sub in ("add", "register"):
            if len(args) < 2:
                UI.error("path required")
                UI.muted(f"Usage: otaman launcher {sub} <path>")
                return 1
            path = Path(args[1]).expanduser()
            if sub == "add" and not path.is_dir():
                UI.error(f"Not a directory: {path}")
                return 1
            try:
                was_new, entry = reg.register(path)
            except Exception as e:
                UI.error(f"Failed to register: {e}")
                return 1
            if sub == "register":
                # Silent mode for launcher-hook use; emit nothing on success.
                return 0
            if was_new:
                UI.ok(f"Registered: {entry['path']}")
            else:
                UI.muted(f"Already registered (last_used updated): {entry['path']}")
            return 0

        if sub == "remove":
            if len(args) < 2:
                UI.error("path required")
                UI.muted("Usage: otaman launcher remove <path>")
                return 1
            try:
                removed = reg.unregister(args[1])
            except Exception as e:
                UI.error(f"Failed to unregister: {e}")
                return 1
            if removed:
                UI.ok(f"Unregistered: {args[1]}")
                return 0
            UI.warn(f"Not in registry: {args[1]}")
            return 1

    # Default: scaffold a new launcher folder.
    UI.header("Otaman Launcher Scaffold")
    try:
        result = run_script("scaffold-launcher.py", *args)
        return result.returncode
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1


def _resolve_connection(
    connections: dict[str, Any],
    name: str,
    depth: int = 0,
) -> dict[str, Any]:
    """Walk an ``extends:`` chain in launch-settings.yaml connections.

    Mirrors Resolve-Connection in scripts/launch-agents.ps1. Parent fields
    are loaded first, the named connection's fields overlay on top. Cycle
    guard at depth=10. Returns an empty dict if ``name`` isn't in
    ``connections``.
    """
    if depth > 10:
        raise ValueError(f"extends: cycle detected at '{name}'")
    raw = connections.get(name)
    if not isinstance(raw, dict):
        return {}
    parent_name = raw.get("extends")
    out: dict[str, Any] = {}
    if parent_name:
        out.update(_resolve_connection(connections, parent_name, depth + 1))
    for k, v in raw.items():
        if k == "extends":
            continue
        out[k] = v
    return out


def cmd_upgrade(args: list[str]) -> int:
    """Walk the launcher registry and refresh each entry.

    For each registered launcher:
      1. Read its launch-settings.yaml + active connection
      2. If remote (ssh/mesh): SSH to the host, run
         ``cd <ssh_plugin_path> && git pull`` then ``cd <ssh_remote_root>
         && bash -l -c 'otaman init'`` (login shell loads ~/.local/bin)
      3. If local: run the same commands locally
      4. Report success / failure per launcher

    Flags:
      --dry-run             show what would run, don't execute
      --launcher <path>     refresh just one launcher
      --skip-pull           don't ``git pull`` (init refresh only)
      --skip-init           don't ``otaman init`` (pull only)
    """
    dry_run = False
    only_launcher: str | None = None
    skip_pull = False
    skip_init = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run":
            dry_run = True
        elif a == "--skip-pull":
            skip_pull = True
        elif a == "--skip-init":
            skip_init = True
        elif a == "--launcher":
            if i + 1 >= len(args):
                UI.error("--launcher requires a path"); return 1
            only_launcher = args[i + 1]
            i += 1
        else:
            UI.error(f"Unknown flag: {a}")
            return 1
        i += 1

    try:
        from otaman_cli import _launchers_registry as reg  # type: ignore
        import yaml  # type: ignore
    except ImportError as e:
        UI.error(f"Missing dependency: {e}")
        return 1

    entries = reg.list_entries()
    if only_launcher:
        normalised = str(Path(only_launcher).expanduser().resolve()) if Path(only_launcher).exists() else only_launcher
        entries = [e for e in entries if str(Path(e["path"]).resolve()) == normalised or e["path"] == only_launcher]
        if not entries:
            UI.error(f"Launcher not in registry: {only_launcher}")
            UI.muted("Run `otaman launcher add <path>` to register it first.")
            return 1

    if not entries:
        UI.warn("No launchers registered.")
        UI.muted("They auto-register on first launch via the launcher script,")
        UI.muted("or you can register manually: otaman launcher add <path>")
        return 0

    UI.header(f"Otaman Upgrade ({len(entries)} launcher{'s' if len(entries) != 1 else ''})")
    if dry_run:
        UI.muted("DRY RUN -- preview only, nothing will execute")
        print()

    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    for entry in entries:
        launcher_path = Path(entry["path"])
        UI.subheader(launcher_path.name)
        UI.muted(f"  Path: {launcher_path}")

        if not launcher_path.is_dir():
            failures.append((str(launcher_path), "launcher folder no longer exists"))
            UI.warn("  Skipped: folder no longer exists")
            UI.muted("  Run `otaman launcher remove <path>` to clean up the registry")
            continue

        ls_path = launcher_path / "launch-settings.yaml"
        if not ls_path.is_file():
            failures.append((str(launcher_path), "no launch-settings.yaml"))
            UI.warn("  Skipped: no launch-settings.yaml")
            continue

        try:
            settings = yaml.safe_load(ls_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            failures.append((str(launcher_path), f"settings parse error: {e}"))
            UI.warn(f"  Skipped: failed to parse launch-settings.yaml ({e})")
            continue

        active_name = settings.get("active_connection")
        connections = settings.get("connections") or {}
        if not active_name or active_name not in connections:
            failures.append((str(launcher_path), "no active_connection or unknown connection"))
            UI.warn("  Skipped: no active_connection or active connection not in connections list")
            continue

        # Resolve extends: chain (mirrors Resolve-Connection in the PS launcher).
        # mesh connections in greenbin / watchtower set extends: lan, so most
        # of their SSH fields (key, remote_root, plugin_path) live in the parent.
        conn = _resolve_connection(connections, active_name)
        ctype = (conn.get("type") or "ssh").lower()

        rc = _upgrade_one(
            launcher_path=launcher_path,
            connection=conn,
            ctype=ctype,
            skip_pull=skip_pull,
            skip_init=skip_init,
            dry_run=dry_run,
        )
        if rc == 0:
            successes.append(str(launcher_path))
            UI.ok("  Upgrade complete")
        else:
            failures.append((str(launcher_path), f"upgrade returned exit {rc}"))
            UI.error(f"  Upgrade failed (exit {rc})")

    print()
    UI.header("Summary")
    UI.ok(f"  {len(successes)} succeeded")
    if failures:
        UI.error(f"  {len(failures)} failed")
        for path, reason in failures:
            UI.muted(f"    - {path}: {reason}")
        return 1
    if not dry_run:
        UI.muted("Restart launcher tabs to pick up launcher-script changes.")
    return 0


def _upgrade_one(
    *,
    launcher_path: Path,
    connection: dict[str, Any],
    ctype: str,
    skip_pull: bool,
    skip_init: bool,
    dry_run: bool,
) -> int:
    """Upgrade a single launcher. Returns 0 on success."""
    if ctype in ("ssh", "mesh"):
        host = connection.get("ssh_default_host")
        plugin_path = connection.get("ssh_plugin_path")
        maestro_root = connection.get("ssh_remote_root")
        ssh_key = connection.get("ssh_key")
        if not host or not maestro_root:
            UI.error("    Missing ssh_default_host or ssh_remote_root")
            return 2

        ssh_cmd = ["ssh"]
        if ssh_key:
            ssh_cmd += ["-i", ssh_key]
        ssh_cmd += [host]

        if not skip_pull and plugin_path:
            remote = f"cd {plugin_path} && git pull --ff-only"
            full = ssh_cmd + [remote]
            UI.muted(f"    Run: {' '.join(full)}")
            if not dry_run:
                rc = subprocess.run(full).returncode
                if rc != 0:
                    return rc
        elif not skip_pull and not plugin_path:
            UI.muted("    (skipping git pull -- no ssh_plugin_path configured)")

        if not skip_init:
            # Non-interactive SSH does NOT load ~/.bashrc, so `~/.local/bin`
            # (where pip --user installs otaman) is not on PATH. `bash -l`
            # loads the login profile (~/.bash_profile / ~/.profile), which
            # does include ~/.local/bin. Earlier attempt used
            # `python3 <plugin>/cli/maestro.py init` but that entry-point
            # never existed (legacy reference), so the upgrade silently
            # failed on every SSH launcher. Reported by plugin-agent 2026-06-08.
            if not plugin_path:
                UI.warn("    Cannot run otaman init -- no ssh_plugin_path configured")
                return 3
            remote = (
                f"cd {maestro_root} && "
                f"bash -l -c 'otaman init'"
            )
            full = ssh_cmd + [remote]
            UI.muted(f"    Run: {' '.join(full)}")
            if not dry_run:
                rc = subprocess.run(full).returncode
                if rc != 0:
                    return rc
        return 0

    # Local connection
    if ctype == "local":
        local_root = connection.get("local_root")
        if not local_root:
            UI.error("    Missing local_root for local connection")
            return 2
        # Plugin path: the otaman CLI itself is part of the plugin checkout.
        plugin_root = Path(__file__).resolve().parent.parent

        if not skip_pull:
            UI.muted(f"    Run: git -C {plugin_root} pull --ff-only")
            if not dry_run:
                rc = subprocess.run(["git", "-C", str(plugin_root), "pull", "--ff-only"]).returncode
                if rc != 0:
                    return rc

        if not skip_init:
            UI.muted(f"    Run: otaman init  (cwd={local_root})")
            if not dry_run:
                rc = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "init"],
                    cwd=local_root,
                ).returncode
                if rc != 0:
                    return rc
        return 0

    UI.error(f"    Unknown connection type: {ctype}")
    return 2


def cmd_blocked(
    args: list[str],
    list_mode: bool = False,
    clear_slug: str = "",
    blocked_by: str | None = None,
) -> int:
    """List, clear, or register blocked tasks for the current agent.

    `otaman blocked --list`              — list blocked entries (current agent)
    `otaman blocked --clear <slug>`      — remove a blocked entry (current agent)
    `otaman blocked clear <stem>`        — tombstone any matching entry across
                                            ALL agents' files by Proposal stem
                                            (auto-clear-blocked-entries 2.1)
    `otaman blocked <slug> [--blocked-by NAME]`  — register a new blocked entry
                                                    and set status (1.8)
    """
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # auto-clear-blocked-entries task 2.1 — `otaman blocked clear <stem>`
    # subcommand: search all `.agents/blocked/*.md` for entries whose
    # `- **Proposal**: <stem>` line matches, and tombstone them with
    # reason `manually-cleared`.  Idempotent (no-match exits 0).
    if len(args) >= 2 and args[0] == "clear":
        return _cmd_blocked_clear_by_stem(root, args[1])

    agent = resolve_agent_identity(root) or "unknown-agent"
    blocked_file = root / ".agents" / "blocked" / f"{agent}.md"

    if list_mode:
        if not blocked_file.is_file():
            print("No blocked tasks.")
            return 0
        text = blocked_file.read_text(encoding="utf-8")
        sections = re.findall(
            r"^## Blocked: (.+?)$(.*?)(?=^## Blocked:|\Z)",
            text, re.MULTILINE | re.DOTALL,
        )
        if not sections:
            print("No blocked tasks.")
            return 0
        for slug, body in sections:
            slug = slug.strip()
            since = ""
            m = re.search(r"\*\*Blocked since\*\*:\s*(.+)", body)
            if m:
                since = f"  (since {m.group(1).strip()})"
            print(f"{slug}{since}")
            proposal_m = re.search(r"\*\*Proposal\*\*:\s*(.+)", body)
            if proposal_m:
                UI.muted(f"  proposal: {proposal_m.group(1).strip()}")
        return 0

    if clear_slug:
        if not blocked_file.is_file():
            UI.muted(f"No blocked task found: {clear_slug}")
            return 0
        text = blocked_file.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^## Blocked: {re.escape(clear_slug)}\s*\n.*?(?=^## Blocked:|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        new_text = pattern.sub("", text).rstrip("\n")
        if new_text == text.rstrip("\n"):
            UI.muted(f"No blocked task found: {clear_slug}")
            return 0
        blocked_file.write_text(new_text + "\n" if new_text else "", encoding="utf-8")
        UI.ok(f"Cleared blocked task: {clear_slug}")
        return 0

    # agent-status-presence task 1.8 — register a new blocked entry +
    # set status. `otaman blocked <slug> [--blocked-by NAME]`.
    if args:
        slug = args[0].strip()
        if not slug:
            UI.error("Empty blocked slug")
            return 1
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        by = blocked_by or "human"
        entry = (
            f"## Blocked: {slug}\n"
            f"- **Blocked since**: {now_iso}\n"
            f"- **Blocked by**: {by}\n"
        )
        blocked_file.parent.mkdir(parents=True, exist_ok=True)
        existing = blocked_file.read_text(encoding="utf-8") if blocked_file.is_file() else ""
        if f"## Blocked: {slug}" in existing:
            UI.muted(f"Already blocked: {slug} (no change)")
        else:
            new_text = (existing.rstrip("\n") + "\n\n" + entry) if existing.strip() else entry
            blocked_file.write_text(new_text, encoding="utf-8")
            UI.ok(f"Registered blocked task: {slug}")
            UI.muted(f"  blocked_by: {by}")

        # Status hook — write blocked state
        _status_hook_after_blocked(root, agent, slug, by)
        return 0

    UI.error("Specify --list, --clear <slug>, or pass a slug to register")
    UI.muted("  otaman blocked --list")
    UI.muted("  otaman blocked --clear <slug>")
    UI.muted("  otaman blocked <slug> [--blocked-by NAME]")
    return 1


def _cmd_blocked_clear_by_stem(root: Path, stem: str) -> int:
    """auto-clear-blocked-entries task 2.1 — manual escape hatch.

    Scan every file under ``.agents/blocked/`` for entries whose
    ``- **Proposal**: <stem>`` line matches the given stem.  Tombstone each
    match by wrapping the entry block in an HTML comment with a
    ``cleared YYYY-MM-DD — manually-cleared`` trailer.  Idempotent: an
    already-commented entry is naturally skipped by the line-leading
    ``^## Blocked:`` regex.

    Returns 0 always — no-match is NOT an error (task 2.2: idempotent).
    """
    stem = (stem or "").strip()
    if not stem:
        UI.error("clear requires a proposal stem")
        UI.muted("  Usage: otaman blocked clear <proposal-stem>")
        return 1

    blocked_dir = root / ".agents" / "blocked"
    if not blocked_dir.is_dir():
        print(f"No blocked entry found for stem: {stem}")
        return 0

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Same regex shape as plugin-agent's `_auto_tombstone_blocked` in
    # bus_server.py, kept in sync deliberately so the tombstone format
    # is identical regardless of which agent / which trigger fired it.
    entry_re = re.compile(
        r"^(## Blocked: .+?)(?=\n## Blocked: |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    proposal_field_re = re.compile(
        r"^\s*-\s*\*\*Proposal\*\*:\s*(\S+)", re.MULTILINE,
    )
    title_re = re.compile(r"^## Blocked:\s*(.+)$", re.MULTILINE)

    tombstoned: list[tuple[str, str]] = []   # (agent, title)

    for blocked_file in sorted(blocked_dir.glob("*.md")):
        agent_name = blocked_file.stem
        try:
            text = blocked_file.read_text(encoding="utf-8")
        except OSError:
            continue

        modified = False
        new_parts: list[str] = []
        last_end = 0
        for m in entry_re.finditer(text):
            entry_block = m.group(1)
            new_parts.append(text[last_end:m.start()])

            prop_m = proposal_field_re.search(entry_block)
            if prop_m and prop_m.group(1) == stem:
                title_m = title_re.search(entry_block)
                title = title_m.group(1).strip() if title_m else "(untitled)"
                tombstoned.append((agent_name, title))
                trailer = f"\ncleared {today} — manually-cleared -->"
                new_parts.append("<!-- " + entry_block.rstrip() + trailer)
                modified = True
            else:
                new_parts.append(entry_block)
            last_end = m.end()

        new_parts.append(text[last_end:])

        if modified:
            try:
                blocked_file.write_text("".join(new_parts), encoding="utf-8")
            except OSError as exc:
                UI.warn(f"Failed to write {blocked_file}: {exc}")

    if not tombstoned:
        print(f"No blocked entry found for stem: {stem}")
        return 0

    for agent_name, title in tombstoned:
        UI.ok(f"Cleared: {agent_name} — {title}")
    return 0


def _status_hook_after_blocked(root: Path, agent: str, slug: str, by: str) -> None:
    """agent-status-presence task 1.8 — write `blocked` status after `otaman blocked <slug>`."""
    try:
        from otaman_cli.status import (
            AgentStatus, State, get_backend, is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    backend = get_backend(root)
    existing = backend.read(agent)
    since = existing.since if (existing and existing.state == State.BLOCKED) else now_iso
    # Preserve existing task/change so the operator sees what triggered the block
    task = existing.task if existing else slug
    change = existing.change if existing else None
    try:
        backend.write(AgentStatus(
            agent=agent, state=State.BLOCKED,
            task=task, change=change, blocked_by=by,
            since=since, updated_at=now_iso,
        ))
    except Exception:
        pass


def _parse_flag_value(rest: list[str], flag: str, *, default: str | None = None) -> str | None:
    """Consume `--flag VALUE` from *rest* (mutates), returning VALUE or default."""
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            value = rest[i + 1]
            del rest[i:i + 2]
            return value
    return default


def _parse_flag_list(rest: list[str], flag: str) -> list[str]:
    """Consume all `--flag VALUE` occurrences from *rest* (mutates), returning list."""
    values: list[str] = []
    while flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            values.append(rest[i + 1])
            del rest[i:i + 2]
        else:
            del rest[i:i + 1]
            break
    return values


def _parse_dependencies(deps: list[str]) -> list[dict]:
    """Parse `--depends-on KIND:VALUE` strings into typed dependency dicts.

    Format:
        outcome:JTBD-3-foo        → {kind: outcome, ref: JTBD-3-foo}
        solution:SOL-1-bar        → {kind: solution, ref: SOL-1-bar}
        external:Email provider   → {kind: external, name: "Email provider"}
    """
    out: list[dict] = []
    for d in deps:
        if ":" not in d:
            UI.warn(f"Ignoring malformed --depends-on (need KIND:VALUE): {d!r}")
            continue
        kind, value = d.split(":", 1)
        kind = kind.strip()
        value = value.strip()
        if kind in ("outcome", "solution"):
            out.append({"kind": kind, "ref": value})
        elif kind == "external":
            out.append({"kind": "external", "name": value})
        else:
            UI.warn(f"Ignoring --depends-on with unknown kind {kind!r}: {d!r}")
    return out


def cmd_hitl(args: list[str]) -> int:
    """`otaman hitl <action> [...]` — HITL stack (request-human-review / human-decision)."""
    if not args:
        UI.error("Usage: otaman hitl <action> [options]")
        UI.muted("Actions: list | next | take <id>")
        return 1
    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)
    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")
    from otaman_cli.hitl import commands as _hitl
    return _hitl.dispatch(action, parsed)


def cmd_project(args: list[str]) -> int:
    """`otaman project <action> [...]` — project/repo registry commands.

    Subcommands:
      add        — create remote repo + register (CVS, gated on otaman-core 1.x)
      assign     — register an existing local git repo (local-only; works now)
      list       — list registered repos
      show       — show one repo's full detail
      update     — modify repos[] entry fields
      disable    — set status: inactive
      enable     — restore to active (drop status field)
      remove     — deregister from platform.yaml; optional --delete-remote
    """
    if not args:
        UI.error("Usage: otaman project <action> [options]")
        UI.muted("Actions: add | assign | list | show | update | disable | enable | remove")
        return 1
    action, *rest_args = args
    rest = list(rest_args)

    # Pull flag values (simple consumer; flags can interleave with positional)
    def _take(flag: str) -> str | None:
        if flag in rest:
            i = rest.index(flag)
            if i + 1 < len(rest):
                value = rest[i + 1]
                del rest[i:i + 2]
                return value
        return None

    def _take_bool(flag: str) -> bool:
        if flag in rest:
            rest.remove(flag)
            return True
        return False

    # Common flags consumed first so positional pickup is clean
    owner = _take("--owner")
    name_flag = _take("--name")
    path_flag = _take("--path")
    url_flag = _take("--url")
    description_flag = _take("--description")
    status_flag = _take("--status")
    delete_remote = _take_bool("--delete-remote")

    # Remaining positional after flag stripping
    positional = [a for a in rest if not a.startswith("-")]
    primary = positional[0] if positional else ""

    if action == "assign":
        from otaman_cli.project.cmd_assign import cmd_project_assign
        return cmd_project_assign(primary, owner=owner, name=name_flag)
    if action == "list":
        from otaman_cli.project.cmd_list import cmd_project_list
        return cmd_project_list(status=status_flag or "active")
    if action == "show":
        from otaman_cli.project.cmd_show import cmd_project_show
        return cmd_project_show(primary)
    if action == "update":
        from otaman_cli.project.cmd_update import cmd_project_update
        return cmd_project_update(
            primary, owner=owner, path=path_flag,
            url=url_flag, description=description_flag,
        )
    if action == "disable":
        from otaman_cli.project.cmd_status import cmd_project_disable
        return cmd_project_disable(primary)
    if action == "enable":
        from otaman_cli.project.cmd_status import cmd_project_enable
        return cmd_project_enable(primary)
    if action == "remove":
        from otaman_cli.project.cmd_remove import cmd_project_remove
        return cmd_project_remove(primary, delete_remote=delete_remote)
    if action == "add":
        UI.error("`otaman project add` is not yet implemented in this phase.")
        UI.muted("Depends on otaman-core 1.x (GitHostAdapter.create_repo). Use `otaman project assign` for existing local repos.")
        return 2

    UI.error(f"Unknown project action: {action}")
    UI.muted("Available: add | assign | list | show | update | disable | enable | remove")
    return 2


def cmd_outcome(args: list[str]) -> int:
    """`otaman outcome <action> [...]` — dispatches to cli_outcome.dispatch."""
    if not args:
        UI.error("Usage: otaman outcome <action> [options]")
        UI.muted("Actions: add | list | show | history | promote | demote | "
                 "retire | request-estimate | accept-cost | reject-cost")
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}

    # Common: positional <id> for show/history/promote/demote/retire/etc.
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)

    # action-specific flags
    parsed["as_a"] = _parse_flag_value(rest, "--as-a")
    parsed["i_want_to"] = _parse_flag_value(rest, "--i-want-to")
    parsed["incremental_outcome"] = _parse_flag_value(rest, "--incremental-outcome")
    parsed["so_i_can"] = _parse_flag_value(rest, "--so-i-can")
    parsed["ultimate_outcome"] = _parse_flag_value(rest, "--ultimate-outcome")
    parsed["category"] = _parse_flag_value(rest, "--category")
    parsed["persona"] = _parse_flag_value(rest, "--persona")
    parsed["impact"] = _parse_flag_value(rest, "--impact")
    parsed["priority"] = _parse_flag_value(rest, "--priority")
    parsed["product_notes"] = _parse_flag_value(rest, "--product-notes")
    parsed["release"] = _parse_flag_value(rest, "--release")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason") or _parse_flag_value(rest, "--note")
    parsed["solution"] = _parse_flag_value(rest, "--solution")
    # `add` accepts an explicit --id if positional wasn't used
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_outcome
    return cli_outcome.dispatch(action, parsed)


def cmd_solution(args: list[str]) -> int:
    """`otaman solution <action> [...]` — dispatches to cli_solution.dispatch."""
    if not args:
        UI.error("Usage: otaman solution <action> [options]")
        UI.muted("Actions: add | list | show | history | propose | "
                 "promote-to-complete | discard")
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}

    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)

    parsed["outcome"] = _parse_flag_value(rest, "--outcome")
    parsed["description"] = _parse_flag_value(rest, "--description")
    parsed["t_shirt"] = _parse_flag_value(rest, "--t-shirt")
    ef = _parse_flag_value(rest, "--effort-days")
    parsed["effort_days"] = float(ef) if ef else None
    parsed["release"] = _parse_flag_value(rest, "--release")
    parsed["cto_notes"] = _parse_flag_value(rest, "--cto-notes")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason") or _parse_flag_value(rest, "--note")
    parsed["pros"] = _parse_flag_list(rest, "--pro")
    parsed["cons"] = _parse_flag_list(rest, "--con")
    parsed["dependencies"] = _parse_dependencies(_parse_flag_list(rest, "--depends-on"))
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_solution
    return cli_solution.dispatch(action, parsed)


def cmd_persona(args: list[str]) -> int:
    """`otaman persona <action> [...]` — dispatches to cli_persona.dispatch."""
    if not args:
        UI.error("Usage: otaman persona <action> [options]")
        UI.muted("Actions: add | list | show | retire")
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)
    parsed["name"] = _parse_flag_value(rest, "--name")
    parsed["description"] = _parse_flag_value(rest, "--description")
    parsed["kind"] = _parse_flag_value(rest, "--kind")
    parsed["domain_prefill_source"] = _parse_flag_value(rest, "--domain-prefill-source")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason")
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_persona
    return cli_persona.dispatch(action, parsed)


def cmd_set_agent(args: list[str]) -> int:
    """DEPRECATED: otaman set-agent no longer mutates any file.

    Identity is now resolved from $OTAMAN_AGENT env var or the .otaman
    'agent:' field in the CWD (written by 'otaman init --update').
    This subcommand exits non-zero and prints migration guidance.
    """
    name = args[0] if args else "<name>"
    print(
        "DEPRECATED: 'otaman set-agent' no longer mutates global state.\n"
        "Identity now resolves from $OTAMAN_AGENT or the .otaman 'agent:' field in CWD.\n"
        "\n"
        "To override identity in the current shell:\n"
        f"  export OTAMAN_AGENT={name}             # direct\n"
        f"  otaman-agent {name}                    # shell function (install via `otaman init --shell`)\n"
        f"  OTAMAN_AGENT={name} otaman <cmd>       # one-shot\n"
        "\n"
        "To make identity automatic, run from inside a repo whose .otaman has 'agent: <name>'.\n"
        "Run 'otaman init --update' to write agent: fields to all repos.",
        file=sys.stderr,
    )
    return 1


def _find_presale_dir(start: Path) -> Path | None:
    """Locate a presale directory walking up from ``start``.

    Prefers ``.otaman-presale/`` (current name). Falls back to legacy
    legacy: ``.maestro-presale/`` for one release window (sunset at otaman-core 1.0).
    Returns absolute Path or None.
    """
    for d in [start] + list(start.parents):
        new = d / ".otaman-presale"
        if new.is_dir():
            return new
        legacy = d / ".maestro-presale"  # legacy: fallback for pre-rebrand presale dirs
        if legacy.is_dir():
            return legacy
    return None


def cmd_presale(args: list[str]) -> int:
    """Initialize a pre-sale estimation project."""
    UI.header("Otaman Pre-Sale")

    # Check for existing presale
    cwd = Path.cwd()
    presale_dir = _find_presale_dir(cwd)

    if presale_dir:
        meta_path = presale_dir / "project-meta.yaml"
        if meta_path.exists():
            UI.info(f"Found existing pre-sale project at: {C.BOLD}{presale_dir}{C.RESET}")
            try:
                import yaml
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                UI.kv("Project", f"{meta.get('project_name', '?')} ({meta.get('project_code', '?')})")
                UI.kv("Domain", meta.get('domain', '?'))
                UI.kv("Phase", meta.get('current_phase', '?'))
            except Exception:
                pass
            UI.muted("To continue this estimation, use /otaman:presale in Claude Code.")
            UI.muted("The SA agent will pick up where you left off.")
            return 0

    # Interactive setup
    if len(args) >= 3:
        project_name, domain = args[0], args[1]
        client = args[2] if len(args) > 2 else ""
    else:
        print("Setting up a new pre-sale estimation project.\n")
        project_name = input(f"  Project name: ").strip()
        if not project_name:
            UI.error("Project name required")
            return 1
        domain = input(f"  Domain (healthcare/fintech/marketplace/ml-ai/saas/ecommerce/iot/general): ").strip()
        if not domain:
            domain = "general"
        client = input(f"  Client name (optional): ").strip()

    # Generate project code
    from datetime import date
    domain_prefix = {"healthcare": "HLT", "fintech": "FIN", "marketplace": "MKT",
                     "ml-ai": "ML", "saas": "SAS", "ecommerce": "ECM",
                     "iot": "IOT", "general": "GEN"}.get(domain, "GEN")
    date_suffix = date.today().strftime("%y%m%d")
    project_code = f"{domain_prefix}-EST-{date_suffix}"

    # Run init-presale script
    script_args = [project_code, project_name, domain]
    if client:
        script_args.extend(["--client", client])

    result = run_script("init-presale.py", *script_args)
    if result.returncode != 0:
        return result.returncode

    print()
    UI.ok("Pre-sale project initialized.")
    UI.kv("Code", project_code, C.BOLD)
    UI.kv("Domain", domain)
    UI.kv("Dir", ".otaman-presale/")
    print()
    UI.action(f"Run {C.GREEN}/otaman:presale{C.RESET} in Claude Code to start Gate 0 estimation.")
    UI.muted("The SA agent will guide you through the full estimation workflow.")
    return 0


def cmd_retrospective(args: list[str]) -> int:
    """Post-project retrospective — updates benchmarks."""
    UI.header("Otaman Retrospective")

    # Find project meta
    cwd = Path.cwd()
    presale_dir = _find_presale_dir(cwd)

    project_code = args[0] if args else None
    meta = None

    if presale_dir:
        meta_path = presale_dir / "project-meta.yaml"
        if meta_path.exists():
            try:
                import yaml
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                project_code = project_code or meta.get("project_code", "UNKNOWN")
            except Exception:
                pass

    if not project_code:
        UI.error("No project code found.")
        UI.muted("Usage: otaman retrospective [project-code]")
        UI.muted("Or run from a directory with .otaman-presale/project-meta.yaml (or legacy: .maestro-presale/)")
        return 1

    UI.kv("Project", project_code, C.BOLD)
    if meta:
        UI.kv("Domain", meta.get('domain', '?'))
        est = meta.get("estimation", {})
        if est.get("total_range_hours"):
            rng = est["total_range_hours"]
            UI.kv("Estimated", f"{rng[0]}-{rng[1]} hours")

    UI.subheader("To run the full retrospective:")
    UI.action(f"Use {C.GREEN}/otaman:retrospective{C.RESET} in Claude Code")
    UI.muted("The agent will collect actuals, calculate accuracy, and update benchmarks.")
    print()
    UI.muted("For a quick manual benchmark entry, add data directly to:")
    UI.muted("  assets/estimation-benchmarks.yaml")
    return 0


def cmd_discovery_phase(args: list[str]) -> int:
    """Show discovery phase status."""
    UI.header("Otaman Discovery Phase")

    # Find presale dir
    d = Path.cwd()
    presale_dir = None
    for _ in range(10):
        new_ = d / ".otaman-presale"
        if new_.is_dir():
            presale_dir = new_
            break
        if (d / ".maestro-presale").is_dir():  # legacy: fallback for pre-rebrand presale dirs
            presale_dir = d / ".maestro-presale"  # legacy: pre-rebrand directory name
            break
        parent = d.parent
        if parent == d:
            break
        d = parent

    if not presale_dir:
        UI.error("No .otaman-presale/ (or legacy: .maestro-presale/) directory found.")
        UI.muted("Run 'otaman presale' first to initialize a pre-sale project.")
        return 1

    # Show status
    meta_path = presale_dir / "project-meta.yaml"
    if meta_path.exists():
        try:
            import yaml
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            UI.kv("Project", f"{meta.get('project_name', '?')} ({meta.get('project_code', '?')})")
            UI.kv("Domain", meta.get('domain', '?'))
            UI.kv("Phase", meta.get('current_phase', '?'))
        except Exception:
            pass

    # Check artifacts
    checks = [
        ("Estimation", (presale_dir / "estimation").is_dir() and list((presale_dir / "estimation").glob("estimate-*.md"))),
        ("Assumptions", (presale_dir / "assumptions.yaml").exists()),
        ("Risks", (presale_dir / "risks.yaml").exists()),
        ("Architecture", (presale_dir / "architecture").is_dir() and list((presale_dir / "architecture").glob("*.md"))),
        ("Knowledge audit", (presale_dir / "knowledge-audit.yaml").exists()),
        ("Validated assumptions", (presale_dir / "discovery" / "validated-assumptions.yaml").exists()),
        ("Updated risks", (presale_dir / "discovery" / "updated-risks.yaml").exists()),
    ]

    UI.subheader("Discovery Artifacts:")
    for name, exists in checks:
        icon = UI.badge("OK", C.GREEN) if exists else UI.path("--")
        print(f"  [{icon}] {name}")

    UI.subheader("To manage discovery interactively:")
    UI.action(f"Use {C.GREEN}/otaman:discovery{C.RESET} in Claude Code")
    UI.muted("It will guide you through assumption validation and risk mitigation.")
    return 0


def cmd_handoff(args: list[str]) -> int:
    """Show handoff readiness."""
    UI.header("Otaman Handoff")

    d = Path.cwd()
    presale_dir = None
    for _ in range(10):
        new_ = d / ".otaman-presale"
        if new_.is_dir():
            presale_dir = new_
            break
        if (d / ".maestro-presale").is_dir():  # legacy: fallback for pre-rebrand presale dirs
            presale_dir = d / ".maestro-presale"  # legacy: pre-rebrand directory name
            break
        parent = d.parent
        if parent == d:
            break
        d = parent

    if not presale_dir:
        UI.error("No .otaman-presale/ (or legacy: .maestro-presale/) directory found.")
        return 1

    UI.kv("Presale dir", str(presale_dir))
    has_estimation = list((presale_dir / "estimation").glob("estimate-*.md")) if (presale_dir / "estimation").is_dir() else []
    has_platform = (presale_dir.parent / "platform.yaml").exists()

    UI.subheader("Handoff readiness:")
    est_icon = UI.badge("OK", C.GREEN) if has_estimation else UI.path("--")
    ka_icon = UI.badge("OK", C.GREEN) if (presale_dir / 'knowledge-audit.yaml').exists() else UI.path("--")
    py_icon = UI.badge("SKIP", C.YELLOW) if has_platform else UI.path("--")
    print(f"  [{est_icon}] Estimation document")
    print(f"  [{ka_icon}] Knowledge audit")
    print(f"  [{py_icon}] platform.yaml {'(already exists)' if has_platform else '(will be generated)'}")

    UI.subheader("To execute handoff:")
    UI.action(f"Use {C.GREEN}/otaman:handoff execute{C.RESET} in Claude Code")
    UI.muted("It will generate platform.yaml, create ADRs, and migrate artifacts.")
    return 0


def cmd_audit_knowledge(args: list[str]) -> int:
    """Show knowledge audit status."""
    UI.header("Otaman Knowledge Audit")

    # Check multiple locations for audit file
    for candidate in [".otaman-presale/knowledge-audit.yaml", ".maestro-presale/knowledge-audit.yaml", ".agents/knowledge-audit.yaml"]:  # legacy: presale fallback
        p = Path(candidate)
        if p.exists():
            try:
                import yaml
                audit = yaml.safe_load(p.read_text(encoding="utf-8"))
                UI.kv("Audit date", audit.get('audit_date', '?'))
                UI.kv("Overall readiness", f"{audit.get('overall_readiness', '?')}%")
                print()
                for item in audit.get("items", []):
                    conf = item.get("confidence", "?")
                    icon = {"high": UI.badge("OK", C.GREEN), "medium": UI.badge("??", C.YELLOW),
                            "low": UI.badge("!!", C.RED), "none": UI.badge("XX", C.RED)}.get(conf, "??")
                    print(f"  [{icon}] {item.get('tech', '?'):30s} {conf:8s} {item.get('action', '')}")
            except Exception as e:
                UI.error(f"Failed to read audit: {e}")
            return 0

    UI.muted("No knowledge audit found.")
    UI.subheader("To run the audit:")
    UI.action(f"Use {C.GREEN}/otaman:audit-knowledge{C.RESET} in Claude Code")
    UI.muted("It will assess Claude's confidence per tech stack item.")
    return 0


def cmd_team(args: list[str], desc: str) -> int:
    """Orchestrate a cross-repo feature."""
    UI.header("Otaman Team Orchestration")

    if not args:
        UI.error("Feature description required")
        UI.muted("Usage: otaman team <workflow-or-description> [-d details]")
        UI.muted("Examples:")
        UI.muted('  otaman team api-change -d "Add pagination to /users"')
        UI.muted('  otaman team "Add user authentication flow"')
        return 1

    feature = " ".join(args)
    UI.kv("Feature", feature, C.BOLD)
    if desc:
        UI.kv("Details", desc)

    # Check for workflow template
    plugin_root = Path(__file__).resolve().parent.parent
    template_path = plugin_root / "references" / "workflows" / f"{feature}.md"
    if template_path.exists():
        UI.kv("Template", f"{C.GREEN}found{C.RESET} ({feature}.md)")
    else:
        UI.kv("Template", "custom (no standard workflow template)")

    UI.subheader("To orchestrate this feature:")
    UI.action(f"Use {C.GREEN}/otaman:team {feature}{C.RESET} in Claude Code")
    UI.muted("It will decompose into tasks, assign to agents via bus, and track progress.")
    return 0


def cmd_gate(args: list[str]) -> int:
    """Check gate readiness for a phase transition."""
    UI.header("Otaman Gate Check")

    root = find_project_root()
    transition = args[0] if args else None

    # Determine current phase
    for meta_loc in [".otaman-presale/project-meta.yaml", ".maestro-presale/project-meta.yaml", ".agents/project-meta.yaml"]:  # legacy: presale fallback
        p = Path(meta_loc) if not root else root / meta_loc
        if p.exists():
            try:
                import yaml
                meta = yaml.safe_load(p.read_text(encoding="utf-8"))
                phase = meta.get("current_phase", "?")
                UI.kv("Current phase", phase, C.BOLD)
                if not transition:
                    # Auto-detect next transition
                    default_order = ["presale", "discovery", "development", "support"]
                    if phase in default_order:
                        idx = default_order.index(phase)
                        if idx + 1 < len(default_order):
                            next_phase = default_order[idx + 1]
                            transition = f"{phase}-to-{next_phase}"
            except Exception:
                pass
            break

    if transition:
        UI.kv("Transition", transition, C.BOLD)
    else:
        UI.error("Could not determine transition. Specify: otaman gate <from>-to-<to>")
        return 1

    UI.subheader("To run full gate validation:")
    UI.action(f"Use {C.GREEN}/otaman:gate {transition}{C.RESET} in Claude Code")
    UI.muted("It will check required artifacts, run validations, and apply domain-specific checks.")
    return 0


def cmd_help() -> int:
    """Show help."""
    print(f"""
{C.BOLD}{C.CYAN}Otaman{C.RESET} - Multi-Repo Agent Orchestration (v{VERSION})

{C.BOLD}Setup & maintenance:{C.RESET}
  {C.GREEN}scan{C.RESET} [path] [--otaman-dir D]    Scan repos, create otaman folder with draft config
  {C.GREEN}init{C.RESET} [config]                 Initialize an otaman project. Creates platform.yaml if none exists.
  {C.GREEN}init companion-repos{C.RESET} [opts]     Scaffold business/strategy companion repos (CE local; no bridge)
  {C.GREEN}migrate{C.RESET} [name]                Migrate legacy layout to dedicated otaman folder
  {C.GREEN}clone{C.RESET} <source> [--target D]    Clone all repos from otaman config (git URL, SSH, local)
  {C.GREEN}doctor{C.RESET}                        Check environment readiness (git, runtimes, CLI, tmux, MCP)
  {C.GREEN}validate{C.RESET} [config]             Validate platform.yaml against the schema
  {C.GREEN}validate-messages{C.RESET} [file]      Validate bus message files
  {C.GREEN}install-cli{C.RESET} [--prefix DIR]     Install ``otaman`` shim on PATH (so launchers find it)
  {C.GREEN}upgrade{C.RESET} [--dry-run]            Walk launcher registry: git pull + otaman init each
  {C.GREEN}compliance{C.RESET} [--format F]        Generate compliance audit report (HIPAA / ISO / GDPR)

{C.BOLD}Bus & messages:{C.RESET}
  {C.GREEN}status{C.RESET} [--blocked|--agent N|--json|--repos]   Fleet status (per-agent presence; --repos for legacy view)
  {C.GREEN}set-status{C.RESET} <state> [--task ...]   Update this agent's status (working|blocked|waiting|idle)
  {C.GREEN}watchdog{C.RESET} <status|start|pause|resume>   Query/control the runner watchdog (HTTP)
  {C.GREEN}whoami{C.RESET}, {C.GREEN}iam{C.RESET}                   Show agent identity + project + routing + bus state ([--json])
  {C.GREEN}check{C.RESET} [agent]                 Check pending messages for an agent (auto-detects from cwd)
  {C.GREEN}read{C.RESET} <message-stem>           Read full content of a bus message (substring match OK)
  {C.GREEN}send{C.RESET} <to> --subject S --body B  Send a bus message ([--type T] [--priority P])
  {C.GREEN}ack{C.RESET} <msg> [--read|--resolved]   Acknowledge a bus message (resolved is default)
  {C.GREEN}cleanup{C.RESET} [--dry-run]            Archive old, fully-acked bus messages
  {C.GREEN}blocked{C.RESET} --list               List blocked tasks for the current agent
  {C.GREEN}blocked{C.RESET} --clear <slug>        Remove a blocked task entry (idempotent)
  {C.GREEN}hitl{C.RESET} <action> [...]           HITL stack: list pending review requests, next, take <id>
  {C.GREEN}project{C.RESET} <action> [...]        Repo registry: assign / list / show / update / disable / enable / remove
  {C.GREEN}outcome{C.RESET} <action> [...]        Program outcome registry (JTBD); actions: add, list, show, history, promote, demote, retire, request-estimate, accept-cost, reject-cost
  {C.GREEN}solution{C.RESET} <action> [...]       Program solution registry; actions: add, list, show, history, propose, promote-to-complete, discard
  {C.GREEN}persona{C.RESET} <action> [...]        Program persona registry; actions: add, list, show, retire
  {C.GREEN}set-agent{C.RESET} <name>              DEPRECATED — see 'otaman set-agent --help' for migration

{C.BOLD}Workflow & specs:{C.RESET}
  {C.GREEN}propose{C.RESET} <title> [-d desc]     Propose a spec change (pending human approval)
  {C.GREEN}approve{C.RESET} [list|approve|reject]   Review/approve agent-initiated spec-change-requests
  {C.GREEN}assign{C.RESET} [tasks.md]             Map OpenSpec tasks to repo owners
  {C.GREEN}complete{C.RESET} <change> --tasks T    Report task completion, update tasks.md
  {C.GREEN}review{C.RESET} [--reviewer R]         Trigger observer review (CTO / security / all)
  {C.GREEN}team{C.RESET} <feature> [-d desc]       Orchestrate a cross-repo feature (decompose + assign)
  {C.GREEN}gate{C.RESET} [transition]             Check phase transition readiness (e.g. pre-sale → dev)

{C.BOLD}Team onboarding:{C.RESET}
  {C.GREEN}onboard{C.RESET} <sub> [args]            User / project provisioning:
                                  add-user, list-users, whoami, doctor,
                                  program-init (interactive Day 1 wizard)

{C.BOLD}Auth & tokens (multi-user):{C.RESET}
  {C.GREEN}login{C.RESET}                         Authenticate via OIDC device flow; cache token
  {C.GREEN}logout{C.RESET}                        Remove cached token
  {C.GREEN}token{C.RESET} [--token-path PATH]     Show cached-token metadata (no secrets)

{C.BOLD}Pre-sale & estimation:{C.RESET}
  {C.GREEN}presale{C.RESET} [name domain client]   Initialize pre-sale estimation project
  {C.GREEN}discovery{C.RESET}                     Show discovery phase status
  {C.GREEN}audit-knowledge{C.RESET}               Show tech stack knowledge audit (Claude's coverage)
  {C.GREEN}handoff{C.RESET}                       Show handoff readiness (presale → development)
  {C.GREEN}retrospective{C.RESET} [project-code]   Post-project retrospective (updates benchmarks library)

{C.BOLD}Accounts, launcher & models:{C.RESET}
  {C.GREEN}routing{C.RESET} [list|...]            Manage launcher routing (multi-subscription identities)
  {C.DIM}accounts{C.RESET} [list|...]           {C.DIM}deprecated alias of `routing` (sunset at otaman-core 1.0){C.RESET}
  {C.GREEN}launcher{C.RESET} <subcommand>         Launcher folder management:
                                  list, add, remove, register, <target> (scaffold)
  {C.GREEN}models{C.RESET} [show|set-default|...]   Inspect / manage model + effort tier overrides

{C.BOLD}Bridge & Telegram (remote approval):{C.RESET}
  {C.GREEN}bridge{C.RESET} [install|uninstall|...]   Bridge daemon lifecycle (install/run/status)
  {C.GREEN}afk{C.RESET} [on|off|status]            AFK toggle — when on, approvals route to Telegram
  {C.GREEN}ping{C.RESET} <message>                 Proactively notify the user via Telegram
  {C.GREEN}mcp-config{C.RESET} --bridge-url URL     Emit .mcp.json for Claude Code (team mode)
  {C.GREEN}session{C.RESET} spawn --agent A --repo R  Spawn a Claude session under logged-in user

{C.BOLD}Git host integration (PR / MR):{C.RESET}
  {C.GREEN}git-host{C.RESET} <subcommand>          Git host PAT + PR/MR API:
                                  detect, list, check, add, pr, post-review

{C.BOLD}PM tool sync:{C.RESET}
  {C.GREEN}pm{C.RESET} configure <provider> --url U  Write pm-sync block to platform.yaml + .mcp.json
  {C.GREEN}pm{C.RESET} init <provider> [--url U]    Initialize PM sync (creates projects, webhooks, custom fields)
  {C.GREEN}pm{C.RESET} status                      Show per-repo PM sync state (open issue counts)

{C.BOLD}Help:{C.RESET}
  {C.GREEN}help{C.RESET}                          Show this help

{C.BOLD}Common options:{C.RESET}
  --update                   Merge re-scan into existing platform.yaml
  --format json|markdown     Output format for compliance report
  --reviewer cto|spec|security|all   Which reviewer to trigger
  -d, --desc TEXT            Description for propose / team commands
  --tasks "2.1,3.1-3.5"     Task IDs to mark complete (for complete)
  --all                      Mark all tasks complete (for complete)
  --dry-run                  Preview without making changes (cleanup, upgrade)
  --read / --resolved        Ack status (resolved is default)
  --launcher PATH            Restrict upgrade to one registered launcher

{C.BOLD}Quick start:{C.RESET}
  otaman scan                # scan your repos
  otaman init                # set up .agents/ infrastructure
  otaman init --update       # write agent: fields to all repo .otaman files
  export OTAMAN_AGENT=<name> # set your identity (or use .otaman agent: field)
  otaman status              # see the dashboard
  otaman check               # check your messages
  otaman ack <msg-stem>      # acknowledge a message

{C.BOLD}Updating across many platforms:{C.RESET}
  otaman launcher list       # show registered launchers (auto-registered on first launch)
  otaman upgrade --dry-run   # preview: git pull each plugin checkout + otaman init each platform
  otaman upgrade             # for real

{C.BOLD}Bus lifecycle:{C.RESET}
  Messages are written to .agents/bus/active/ with timestamp-based IDs.
  Each agent acks independently via .agents/bus/active/acks/ files.
  Old, fully-acked messages are archived to .agents/bus/archive/YYYY-MM/.
  Cleanup runs automatically during 'status' and 'init'.

{C.BOLD}Cross-platform:{C.RESET}
  Works on Windows (cmd/PowerShell), WSL, Linux, and macOS.
  All paths in platform.yaml are relative (./repo-name) with forward slashes.
  Use 'python3' on Linux/macOS/WSL, 'py' on Windows.
""")
    return 0


def cmd_onboard(args: list[str]) -> int:
    """Dispatch to the otaman onboard CLI subcommands.

    The onboard package owns its own argparse; we pass through ``args``
    (everything after ``onboard``) verbatim and return its exit code.
    """
    from otaman_cli.onboard.cli import main as _onboard_main
    return _onboard_main(args)



def cmd_login(args: list[str]) -> int:
    """Dispatch to the otaman login / auth subcommands.

    Implements OAuth 2.0 Device Authorization Grant. Subcommands:
      login   — initiate device-flow auth (default if no subcommand)
      logout  — remove cached token
      show    — print cached-token metadata (no secrets)
    """
    from otaman_cli.auth.login import main as _login_main
    return _login_main(args)



# ---------------------------------------------------------------------------
# PM dispatch helper
# ---------------------------------------------------------------------------

def _cmd_pm_dispatch(args: list[str]) -> int:
    """Dispatch `otaman pm <sub>` subcommands."""
    from otaman_cli.pm.cmd_init import cmd_pm_init
    from otaman_cli.pm.cmd_status import cmd_pm_status
    sub = args[0] if args else ""
    rest = args[1:] if args else []
    if sub == "configure":
        from otaman_cli.pm.cmd_configure import cmd_pm_configure
        return cmd_pm_configure(rest)
    elif sub == "init":
        return cmd_pm_init(rest)
    elif sub == "status":
        return cmd_pm_status(rest)
    else:
        UI.error(f"Unknown pm subcommand: {sub!r}. Use: pm configure | pm init | pm status")
        return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        return cmd_help()

    if args[0] in ("-v", "--version"):
        print(f"otaman {VERSION}")
        return 0

    command = args[0]
    rest = args[1:]

    # Registry commands have rich per-action flags that the generic flag loop
    # below would mishandle. Dispatch them BEFORE the generic loop with the
    # raw argv intact (the cmd_* functions parse their own flags).
    if command == "outcome":
        return cmd_outcome(rest)
    if command == "solution":
        return cmd_solution(rest)
    if command == "persona":
        return cmd_persona(rest)

    # `otaman init companion-repos` — sub-action with its own flags
    # (--program / --repos / --dry-run / --force).  Dispatch raw before the
    # generic flag loop, same reason as the registry commands above.
    if command == "init" and rest and rest[0] == "companion-repos":
        return cmd_init_companion_repos(rest[1:])

    # `otaman hitl <action>` — human-in-the-loop stack (auto-session-spawn §3).
    if command == "hitl":
        return cmd_hitl(rest)

    # `otaman project <action>` — project/repo registry management
    if command == "project":
        return cmd_project(rest)

    # `otaman pm <action>` — PM tool sync (Easy8 / Redmine)
    if command == "pm":
        from otaman_cli.pm.cmd_init import cmd_pm_init
        from otaman_cli.pm.cmd_status import cmd_pm_status
        sub = rest[0] if rest else ""
        if sub == "configure":
            from otaman_cli.pm.cmd_configure import cmd_pm_configure
            return cmd_pm_configure(rest[1:])
        elif sub == "init":
            return cmd_pm_init(rest[1:])
        elif sub == "status":
            return cmd_pm_status(rest[1:])
        else:
            UI.error(f"Unknown pm subcommand: {sub!r}. Use: pm configure | pm init | pm status")
            return 1

    # Extract flags
    fmt = "markdown"
    reviewer = "all"
    desc = ""
    update = False
    dry_run = False
    skip_doctor = False
    ack_status = "resolved"
    approve_action = "list"
    complete_tasks = ""
    complete_all = False
    hide_broadcast_hours: int | None = None
    blocked_list = False
    blocked_clear = ""
    blocked_by_value: str | None = None
    maestro_dir: str | None = None
    project_name_override: str | None = None
    shell_flag = False
    yes_flag = False
    org_name: str | None = None
    positional: list[str] = []

    i = 0
    while i < len(rest):
        if rest[i] == "--format" and i + 1 < len(rest):
            fmt = rest[i + 1]
            i += 2
        elif rest[i] == "--reviewer" and i + 1 < len(rest):
            reviewer = rest[i + 1]
            i += 2
        elif rest[i] in ("-d", "--desc") and i + 1 < len(rest):
            desc = rest[i + 1]
            i += 2
        elif rest[i] == "--update":
            update = True
            i += 1
        elif rest[i] == "--shell":
            shell_flag = True
            i += 1
        elif rest[i] in ("--yes", "-y"):
            yes_flag = True
            i += 1
        elif rest[i] == "--org" and i + 1 < len(rest):
            org_name = rest[i + 1]
            i += 2
        elif rest[i] == "--dry-run":
            dry_run = True
            i += 1
        elif rest[i] == "--skip-doctor":
            skip_doctor = True
            i += 1
        elif rest[i] == "--tasks" and i + 1 < len(rest):
            complete_tasks = rest[i + 1]
            i += 2
        elif rest[i] == "--all":
            complete_all = True
            i += 1
        elif rest[i] == "--hide-broadcast-older-than" and i + 1 < len(rest):
            try:
                hide_broadcast_hours = int(rest[i + 1])
            except ValueError:
                UI.warn(f"--hide-broadcast-older-than expects an integer (hours); ignoring '{rest[i+1]}'")
            i += 2
        elif rest[i] == "--list":
            blocked_list = True
            i += 1
        elif rest[i] == "--clear" and i + 1 < len(rest):
            blocked_clear = rest[i + 1]
            i += 2
        elif rest[i] == "--blocked-by" and i + 1 < len(rest):
            blocked_by_value = rest[i + 1]
            i += 2
        elif rest[i] == "--read":
            ack_status = "read"
            i += 1
        elif rest[i] == "--resolved":
            ack_status = "resolved"
            i += 1
        elif rest[i] in ("--maestro-dir", "--otaman-dir", "--target") and i + 1 < len(rest):  # legacy: backward-compat arg
            maestro_dir = rest[i + 1]
            i += 2
        elif rest[i] == "--name" and i + 1 < len(rest):
            project_name_override = rest[i + 1]
            i += 2
        elif rest[i].startswith("-"):
            i += 1  # skip unknown flags
        else:
            positional.append(rest[i])
            i += 1

    commands = {
        "scan": lambda: cmd_scan(positional, update=update, maestro_dir=maestro_dir, dry_run=dry_run, project_name_override=project_name_override),
        "init": lambda: cmd_init(positional, dry_run=dry_run, skip_doctor=skip_doctor, update=update, shell=shell_flag, yes=yes_flag),
        "clone": lambda: cmd_clone(positional, target=maestro_dir or ""),
        "doctor": lambda: cmd_doctor(positional, org=org_name),
        "status": lambda: cmd_status(rest),
        "set-status": lambda: cmd_set_status(rest),
        "watchdog": lambda: _cmd_watchdog_dispatch(positional),
        "check": lambda: cmd_check(positional, hide_broadcast_hours=hide_broadcast_hours),
        "read": lambda: cmd_read(positional),
        "send": lambda: cmd_send(rest),
        "whoami": lambda: cmd_whoami(rest),
        "iam": lambda: cmd_whoami(rest),
        "ack": lambda: cmd_ack(positional, ack_status),
        "cleanup": lambda: cmd_cleanup(positional, dry_run),
        "propose": lambda: cmd_propose(positional, desc),
        "complete": lambda: cmd_complete(positional, tasks_spec=complete_tasks, mark_all=complete_all),
        "approve": lambda: cmd_approve(positional, action=approve_action, comment=desc),
        "assign": lambda: cmd_assign(positional),
        "review": lambda: cmd_review(positional, reviewer),
        "validate": lambda: cmd_validate(positional),
        "validate-messages": lambda: cmd_validate_messages(positional),
        "compliance": lambda: cmd_compliance(positional, fmt),
        "migrate": lambda: cmd_migrate(positional),
        "accounts": lambda: cmd_accounts(rest),
        "routing": lambda: cmd_accounts(rest),
        "afk": lambda: cmd_afk(rest),
        "bridge": lambda: cmd_bridge(rest),
        "mcp-config": lambda: cmd_mcp_config(rest),
        "session": lambda: cmd_session(rest),
        "ping": lambda: cmd_ping(rest),
        "launcher": lambda: cmd_launcher(rest),
        "upgrade": lambda: cmd_upgrade(rest),
        "install-cli": lambda: cmd_install_cli(rest),
        "git-host": lambda: cmd_git_host(rest),
        "models": lambda: cmd_models(rest),
        "set-agent": lambda: cmd_set_agent(positional),
        "blocked": lambda: cmd_blocked(positional, list_mode=blocked_list, clear_slug=blocked_clear, blocked_by=blocked_by_value),
        # outcome/solution/persona are dispatched BEFORE this dict by the early
        # branch in main() so flags survive the generic loop. These entries are
        # retained for discoverability (help-coverage test scans this dict).
        "hitl": lambda: cmd_hitl(rest),
        "project": lambda: cmd_project(rest),
        "outcome": lambda: cmd_outcome(rest),
        "solution": lambda: cmd_solution(rest),
        "persona": lambda: cmd_persona(rest),
        "presale": lambda: cmd_presale(positional),
        "retrospective": lambda: cmd_retrospective(positional),
        "discovery": lambda: cmd_discovery_phase(positional),
        "handoff": lambda: cmd_handoff(positional),
        "audit-knowledge": lambda: cmd_audit_knowledge(positional),
        "gate": lambda: cmd_gate(positional),
        "team": lambda: cmd_team(positional, desc),
        "login": lambda: cmd_login(["login"] + rest),
        "logout": lambda: cmd_login(["logout"] + rest),
        "token": lambda: cmd_login(["show"] + rest),
        "onboard": lambda: cmd_onboard(rest),
        "pm": lambda: _cmd_pm_dispatch(rest),
    }

    if command not in commands:
        UI.error(f"Unknown command: {command}")
        UI.muted("Run 'otaman help' for available commands")
        return 1

    return commands[command]()


if __name__ == "__main__":
    sys.exit(main())
