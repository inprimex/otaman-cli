#!/usr/bin/env python3
# Long lines in this file are aligned usage/help tables inside the module docstring
# and the cmd_help() f-string; wrapping them would change CLI help output.
# ruff: noqa: E501
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
    otaman whoami --for-path <p>      Resolve owning agent for a path (monorepo-path-ownership)
    otaman owner-paths --validate     Validate owner-paths globs in platform.yaml
    otaman notify-change <change>     Send spec-change notification (post-merge-spec-notify)
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
    otaman -i / --interactive          Open the interactive human console (TUI; needs the 'console' extra)
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
import sys
from pathlib import Path


def _resolve_version() -> str:
    # Read the installed package version from importlib.metadata so the value
    # tracks pipx/uv installs and reflects the actual release tag baked into
    # the wheel. Falls back to a "-dev" suffix for editable/source-tree runs
    # where the package isn't installed.
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("otaman-cli")
    except (ImportError, PackageNotFoundError):
        return "0.1.0-dev"


VERSION = _resolve_version()
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
if not sys.stdout.isatty() or (
    sys.platform == "win32" and "WT_SESSION" not in os.environ and "TERM" not in os.environ
):
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
    def table(
        headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None
    ) -> None:
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

    import contextlib
    import importlib
    import io
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


def _read_platform_specs_path(root: Path) -> str:
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
    import tempfile as _tmp

    import yaml as _yaml

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
            'platform.yaml: `version:` field missing — defaulted to "1.0" for validation. '
            'Add `version: "1.0"` to the canonical file to silence this hint.'
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
    if (
        not isinstance(doc.get("repos"), list) or len(doc.get("repos") or []) == 0
    ) and has_ce_marker:
        # Schema requires name to match ^[A-Za-z][A-Za-z0-9._-]{1,63}$
        # and owner to match ^[a-z][a-z0-9-]{1,63}$.
        doc["repos"] = [
            {
                "name": "ce-org-placeholder",
                "path": ".",
                "owner": "ops-agent",
            }
        ]
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
            prefix=".otaman-ce-norm-",
            suffix=".yaml",
            dir=str(parent_dir),
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


def cmd_help() -> int:
    """Show help."""
    print(f"""
{C.BOLD}{C.CYAN}Otaman{C.RESET} - Multi-Repo Agent Orchestration (v{VERSION})

{C.BOLD}Setup & maintenance:{C.RESET}
  {C.GREEN}scan{C.RESET} [path] [--otaman-dir D]    Scan repos, create otaman folder with draft config
  {C.GREEN}init{C.RESET} [config]                 Initialize an otaman project. Creates platform.yaml if none exists.
  {C.GREEN}init companion-repos{C.RESET} [opts]     Scaffold business/strategy companion repos (CE local; no bridge)
  {C.GREEN}migrate{C.RESET} [name] [--dry-run] [--yes]   Migrate legacy layout to dedicated otaman folder
  {C.GREEN}clone{C.RESET} <source> [--target D]    Clone all repos from otaman config (git URL, SSH, local)
  {C.GREEN}doctor{C.RESET}                        Check environment readiness (git, runtimes, CLI, tmux, MCP)
  {C.GREEN}validate{C.RESET} [config]             Validate platform.yaml against the schema
  {C.GREEN}validate-messages{C.RESET} [file]      Validate bus message files
  {C.GREEN}install-cli{C.RESET} [--prefix DIR]     Install ``otaman`` shim on PATH (so launchers find it)
  {C.GREEN}upgrade{C.RESET} [--dry-run] [--yes]    Walk launcher registry: git pull + otaman init each
  {C.GREEN}sync-repos{C.RESET} [--dry-run]          Clone registered-but-absent repos + regenerate their agent artifacts
  {C.GREEN}compliance{C.RESET} [--format F]        Generate compliance audit report (HIPAA / ISO / GDPR)

{C.BOLD}Bus & messages:{C.RESET}
  {C.GREEN}status{C.RESET} [--blocked|--agent N|--json|--repos]   Fleet status (per-agent presence; --repos for legacy view)
  {C.GREEN}set-status{C.RESET} <state> [--task ...]   Update this agent's status (working|blocked|waiting|idle)
  {C.GREEN}whoami --for-path{C.RESET} <p>        Resolve owning agent for a path (monorepo-path-ownership)
  {C.GREEN}owner-paths --validate{C.RESET}        Validate owner-paths globs in platform.yaml
  {C.GREEN}notify-change{C.RESET} <change>             Send spec-change notification (post-merge replacement)
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
  {C.GREEN}connection{C.RESET} <action> [...]     Connections: create, list, show, update, delete, check (values-free; secret_ref never a value)
  {C.GREEN}human{C.RESET} <action> [...]          Human-seat identity: list enrolled humans; enroll/remove SSH-key identities
  {C.GREEN}project{C.RESET} <action> [...]        Repo registry: assign / list / show / update / disable / enable / remove
  {C.GREEN}program{C.RESET} <action> [...]        Program lifecycle: status / limit / suspend / resume / archive / unarchive
  {C.GREEN}acting-lock{C.RESET} <run|probe> [...]   Acting-session lock: run a command holding it, or probe the holder
  {C.GREEN}outcome{C.RESET} <action> [...]        Program outcome registry (JTBD); actions: add, list, show, history, promote, demote, retire, request-estimate, accept-cost, reject-cost
  {C.GREEN}solution{C.RESET} <action> [...]       Program solution registry; actions: add, list, show, history, propose, promote-to-complete, discard
  {C.GREEN}persona{C.RESET} <action> [...]        Program persona registry; actions: add, list, show, retire
  {C.GREEN}set-agent{C.RESET} <name>              DEPRECATED — see 'otaman set-agent --help' for migration

{C.BOLD}Workflow & specs:{C.RESET}
  {C.GREEN}propose{C.RESET} <title> [-d desc]     Propose a spec change (pending human approval)
  {C.GREEN}approve{C.RESET} [list|approve|reject]   Review/approve agent-initiated spec-change-requests
  {C.GREEN}emergency-halt{C.RESET} --reason "..."  Broadcast an emergency halt to every agent (requires interactive confirmation)
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
  {C.GREEN}runner platforms{C.RESET} add|list|remove   Manage which platform.yaml files a --platforms-dir runner serves
  {C.GREEN}runner token{C.RESET} install|rotate|show    Bootstrap / rotate / inspect the runner's stable token

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
  --dry-run                  Preview without making changes (cleanup, upgrade, migrate, init --update)
  --yes, -y                  Skip confirmation prompt in non-interactive contexts (migrate, upgrade)
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

    # interactive-human-console: `otaman -i` opens the TTY human console (a
    # human seat with no LLM in the loop). Optional `console` extra (Textual).
    if args[0] in ("-i", "--interactive"):
        from otaman_cli.console.launch import run_console

        return run_console(args[1:])

    command = args[0]
    rest = args[1:]

    # F020 complete: every top-level command is registered in
    # otaman_cli.commands; nothing left to fall back to. The old
    # `commands = {...}` dict and its shared flag-parsing loop (F021/F022)
    # were retired in this change along with the last dict entry, "init".
    from otaman_cli import commands as _commands_registry

    registry_result = _commands_registry.dispatch(command, rest)
    if registry_result is not None:
        return registry_result

    UI.error(f"Unknown command: {command}")
    UI.muted("Run 'otaman help' for available commands")
    return 1


if __name__ == "__main__":
    sys.exit(main())
