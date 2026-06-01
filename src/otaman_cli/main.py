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
    otaman doctor                     Check environment readiness
    otaman status [<repo>]            Cross-repo status dashboard
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

    # Non-TTY: print improved error and exit
    if not sys.stdin.isatty():
        UI.error("No platform.yaml found.")
        UI.muted("Interactive setup unavailable (non-TTY). Create platform.yaml first:")
        UI.muted("  otaman scan .                  — detect existing repos")
        UI.muted("  otaman onboard program-init    — interactive wizard")
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


def cmd_init(args: list[str], dry_run: bool = False, skip_doctor: bool = False, update: bool = False, shell: bool = False) -> int:
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

    # Validate first
    print(f"Validating {config_path.name}...")
    result = run_script("validate-platform.py", str(config_path), capture=True)
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


def cmd_doctor(args: list[str]) -> int:
    """Check environment readiness — git, runtimes, CLI tools, MCP."""
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

    return 1 if report["summary"]["failed"] > 0 else 0


def cmd_status(args: list[str]) -> int:
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

    content = (
        f"---\n"
        f"id: {ts}-{agent[:8]}\n"
        f"from: {agent}\n"
        f"to: {ns.to}\n"
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
    UI.kv("  Type", ns.msg_type)
    UI.kv("  Priority", ns.priority)
    UI.muted(f"  Path: {msg_path.relative_to(root)}")
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
            if to != agent and to != "all":
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
            })
        except (OSError, yaml.YAMLError):
            continue

    # Display pending first, then others
    pending = [m for m in messages if m["status"] == "pending"]
    other = [m for m in messages if m["status"] != "pending"]

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

    if pending:
        for m in pending:
            broadcast_label = " (broadcast)" if m.get("to") == "all" else ""
            UI.bullet(f"{m['id']} from {UI.agent(m['from'])} [{UI.priority(m['priority'])}]{broadcast_label}")
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
    return 0


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

    for msg_file in matches:
        ack_file = acks_dir / f"{msg_file.stem}.{agent}.ack"
        ack_file.write_text(status + "\n", encoding="utf-8")
        UI.ok(f"Acked: {msg_file.stem} -> {status}")

    return 0


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

    return 0


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


def cmd_validate(args: list[str]) -> int:
    """Validate platform.yaml."""
    config = args[0] if args else "platform.yaml"
    result = run_script("validate-platform.py", config)
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
         && python3 cli/maestro.py init``  (legacy: uses plugin entry-point directly)
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
            # Use python3 -m so we don't depend on `otaman` being on PATH on
            # the remote (nvm/login-shell dance is fragile in non-interactive
            # SSH). The plugin path is already known.
            if not plugin_path:
                UI.warn("    Cannot run otaman init -- no ssh_plugin_path configured")
                return 3
            remote = (
                f"cd {maestro_root} && "
                f"python3 {plugin_path}/cli/maestro.py init"  # legacy: plugin entry-point not yet renamed
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


def cmd_blocked(args: list[str], list_mode: bool = False, clear_slug: str = "") -> int:
    """List or clear blocked tasks for the current agent."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

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

    UI.error("Specify --list or --clear <slug>")
    UI.muted("  otaman blocked --list")
    UI.muted("  otaman blocked --clear <slug>")
    return 1


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
  {C.GREEN}migrate{C.RESET} [name]                Migrate legacy layout to dedicated otaman folder
  {C.GREEN}clone{C.RESET} <source> [--target D]    Clone all repos from otaman config (git URL, SSH, local)
  {C.GREEN}doctor{C.RESET}                        Check environment readiness (git, runtimes, CLI, tmux, MCP)
  {C.GREEN}validate{C.RESET} [config]             Validate platform.yaml against the schema
  {C.GREEN}validate-messages{C.RESET} [file]      Validate bus message files
  {C.GREEN}install-cli{C.RESET} [--prefix DIR]     Install ``otaman`` shim on PATH (so launchers find it)
  {C.GREEN}upgrade{C.RESET} [--dry-run]            Walk launcher registry: git pull + otaman init each
  {C.GREEN}compliance{C.RESET} [--format F]        Generate compliance audit report (HIPAA / ISO / GDPR)

{C.BOLD}Bus & messages:{C.RESET}
  {C.GREEN}status{C.RESET} [repo]                 Cross-repo status dashboard (commits, messages, reviews)
  {C.GREEN}whoami{C.RESET}, {C.GREEN}iam{C.RESET}                   Show agent identity + project + routing + bus state ([--json])
  {C.GREEN}check{C.RESET} [agent]                 Check pending messages for an agent (auto-detects from cwd)
  {C.GREEN}read{C.RESET} <message-stem>           Read full content of a bus message (substring match OK)
  {C.GREEN}send{C.RESET} <to> --subject S --body B  Send a bus message ([--type T] [--priority P])
  {C.GREEN}ack{C.RESET} <msg> [--read|--resolved]   Acknowledge a bus message (resolved is default)
  {C.GREEN}cleanup{C.RESET} [--dry-run]            Archive old, fully-acked bus messages
  {C.GREEN}blocked{C.RESET} --list               List blocked tasks for the current agent
  {C.GREEN}blocked{C.RESET} --clear <slug>        Remove a blocked task entry (idempotent)
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
    maestro_dir: str | None = None
    project_name_override: str | None = None
    shell_flag = False
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
        "init": lambda: cmd_init(positional, dry_run=dry_run, skip_doctor=skip_doctor, update=update, shell=shell_flag),
        "clone": lambda: cmd_clone(positional, target=maestro_dir or ""),
        "doctor": lambda: cmd_doctor(positional),
        "status": lambda: cmd_status(positional),
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
        "blocked": lambda: cmd_blocked(positional, list_mode=blocked_list, clear_slug=blocked_clear),
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
    }

    if command not in commands:
        UI.error(f"Unknown command: {command}")
        UI.muted("Run 'otaman help' for available commands")
        return 1

    return commands[command]()


if __name__ == "__main__":
    sys.exit(main())
