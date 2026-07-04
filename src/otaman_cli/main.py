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
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


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
            from otaman_cli.commands.scan import cmd_scan
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
    from otaman_cli.commands.doctor import cmd_doctor
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

    # F020/F022 strangler-fig cutover: migrated command groups (outcome,
    # solution, persona, hitl, project, pm as of this migration) register in
    # otaman_cli.commands instead of an if-branch here. Checked before the
    # generic flag loop below, same reasoning as the old special-cases: these
    # commands have rich per-action flags the shared loop would mishandle, so
    # they always need the raw argv, never the loop's parsed positional/fmt/etc.
    from otaman_cli import commands as _commands_registry
    registry_result = _commands_registry.dispatch(command, rest)
    if registry_result is not None:
        return registry_result

    # `otaman init companion-repos` — sub-action with its own flags
    # (--program / --repos / --dry-run / --force).  Dispatch raw before the
    # generic flag loop, same reason as the registry commands above.
    if command == "init" and rest and rest[0] == "companion-repos":
        return cmd_init_companion_repos(rest[1:])

    # Extract flags
    update = False
    dry_run = False
    skip_doctor = False
    maestro_dir: str | None = None
    shell_flag = False
    yes_flag = False
    positional: list[str] = []

    i = 0
    while i < len(rest):
        if rest[i] == "--update":
            update = True
            i += 1
        elif rest[i] == "--shell":
            shell_flag = True
            i += 1
        elif rest[i] in ("--yes", "-y"):
            yes_flag = True
            i += 1
        elif rest[i] == "--dry-run":
            dry_run = True
            i += 1
        elif rest[i] == "--skip-doctor":
            skip_doctor = True
            i += 1
        elif rest[i] in ("--maestro-dir", "--otaman-dir", "--target") and i + 1 < len(rest):  # legacy: backward-compat arg
            maestro_dir = rest[i + 1]
            i += 2
        elif rest[i].startswith("-"):
            i += 1  # skip unknown flags
        else:
            positional.append(rest[i])
            i += 1

    commands = {
        "init": lambda: cmd_init(positional, dry_run=dry_run, skip_doctor=skip_doctor, update=update, shell=shell_flag, yes=yes_flag),
        "clone": lambda: cmd_clone(positional, target=maestro_dir or ""),
        "launcher": lambda: cmd_launcher(rest),
        "set-agent": lambda: cmd_set_agent(positional),
    }

    if command not in commands:
        UI.error(f"Unknown command: {command}")
        UI.muted("Run 'otaman help' for available commands")
        return 1

    return commands[command]()


if __name__ == "__main__":
    sys.exit(main())
