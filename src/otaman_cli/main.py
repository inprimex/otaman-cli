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
    otaman cli install [--apply]      Put `otaman` on PATH (symlink on POSIX, setx on Windows)
    otaman cli interactive            Open the interactive human console (alias: `otaman -i`)
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
    otaman -i / --interactive          Alias for `otaman cli interactive` (permanent; needs the 'console' extra)
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

import sys

# ---------------------------------------------------------------------------
# What this module used to BE, and is not any more.
#
# `UI`, `C`, `run_script`, `SCRIPT_MAP` and four platform/bus helpers lived here, so
# 39 of 40 modules under `commands/` imported the entry point in order to print a
# line (CTO review 2026-09-26, "inverted dependency"). They live in leaf modules now
# and this file is the entry point again: version, help, dispatch.
#
# Re-exported rather than merely moved, for two reasons that are not style:
#   * anything outside this repo that learned `from otaman_cli.main import UI` keeps
#     working — nothing is forced to follow a refactor it did not ask for;
#   * tests patch `otaman_cli.main.run_script` and `otaman_cli.main._resolve_bus_paths`
#     by string path, and a consumer that still reads the name off `main` sees those
#     patches. The consumers repointed in this change are patched at the new home
#     instead, and those tests moved with them.
# ---------------------------------------------------------------------------
from otaman_cli.bus_paths import _get_agent_ack_status as _get_agent_ack_status
from otaman_cli.bus_paths import _resolve_bus_paths as _resolve_bus_paths
from otaman_cli.platform_config import (
    _normalize_ce_platform_yaml_for_validation as _normalize_ce_platform_yaml_for_validation,
)
from otaman_cli.platform_config import _read_platform_specs_path as _read_platform_specs_path
from otaman_cli.scripts import SCRIPT_MAP as SCRIPT_MAP
from otaman_cli.scripts import run_script as run_script
from otaman_cli.ui import UI as UI
from otaman_cli.ui import C as C


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


# _ensure_sibling_paths removed: cross-repo imports now use proper package resolution
# (otaman-cli depends on otaman-core, otaman-bridge, otaman-plugin via pyproject deps)
#
# find_project_root + resolve_agent_identity moved to identity.py (Stage 2A).


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_help() -> int:
    """Show help."""
    # version-authority 1.3: the banner shows the deploy RELEASE when a marker
    # exists, else a `cli <x>`-labelled component version — never a bare number
    # that reads like a shipping version.
    from otaman_cli.release_version import banner_version, resolve_release_version

    _v = banner_version(resolve_release_version(VERSION))
    print(f"""
{C.BOLD}{C.CYAN}Otaman{C.RESET} - Multi-Repo Agent Orchestration ({_v})

{C.BOLD}Setup & maintenance:{C.RESET}
  {C.GREEN}scan{C.RESET} [path] [--otaman-dir D]    Scan repos, create otaman folder with draft config
  {C.GREEN}init{C.RESET} [config]                 Initialize an otaman project. Creates platform.yaml if none exists.
  {C.GREEN}init companion-repos{C.RESET} [opts]     Scaffold business/strategy companion repos (CE local; no bridge)
  {C.GREEN}migrate{C.RESET} [name] [--dry-run] [--yes]   Migrate legacy layout to dedicated otaman folder
  {C.GREEN}clone{C.RESET} <source> [--target D]    Clone all repos from otaman config (git URL, SSH, local)
  {C.GREEN}doctor{C.RESET} [--org N] [--scan]      Check environment readiness (git, runtimes, CLI, tmux, MCP, ~/.local ownership; --scan deep-scans ownership)
  {C.GREEN}validate{C.RESET} [config]             Validate platform.yaml against the schema
  {C.GREEN}validate docs{C.RESET} [--fix|--align] <t...>  Lint/fix markdown tables (R1-R4, fence+span-aware, backtick-first; explicit targets)
  {C.GREEN}validate-messages{C.RESET} [file]      Validate bus message files
  {C.GREEN}cli install{C.RESET} [--prefix DIR]     Install ``otaman`` shim on PATH (so launchers find it)
  {C.GREEN}cli interactive{C.RESET}                Open the interactive human console (alias: {C.GREEN}otaman -i{C.RESET})
  {C.DIM}install-cli{C.RESET}                     DEPRECATED alias for {C.GREEN}otaman cli install{C.RESET}
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
  {C.GREEN}policy{C.RESET} <action> [...]         Policy engine: list packs / show effective policy / validate
  {C.GREEN}release{C.RESET} clear-fragments <m>   Clear changelog fragments a release cut consumed (by manifest)
  {C.GREEN}console{C.RESET} seat [--socket S]     Seat the human console on its private tmux server
  {C.GREEN}outcome{C.RESET} <action> [...]        Program outcome registry (JTBD); actions: add, list, show, history, promote, demote, retire, request-estimate, choose, accept-cost, reject-cost
  {C.GREEN}solution{C.RESET} <action> [...]       Program solution registry; actions: add, list, show, history, propose, promote-to-complete, discard
  {C.GREEN}persona{C.RESET} <action> [...]        Program persona registry; actions: add, list, show, retire
  {C.GREEN}set-agent{C.RESET} <name>              DEPRECATED — see 'otaman set-agent --help' for migration

{C.BOLD}Workflow & specs:{C.RESET}
  {C.GREEN}propose{C.RESET} <title> [-d desc]     Propose a spec change (pending human approval)
  {C.GREEN}approve{C.RESET} [list|approve|reject]   Review/approve agent-initiated spec-change-requests
  {C.GREEN}emergency-halt{C.RESET} --reason "..."  Broadcast an emergency halt to every agent (requires interactive confirmation)
  {C.GREEN}assign{C.RESET} [tasks.md]             Map OpenSpec tasks to repo owners
  {C.GREEN}complete{C.RESET} <change> --tasks T    Report task completion, update tasks.md
  {C.GREEN}spec{C.RESET} <status|gate|approve|reconcile>  Spec-lifecycle surface + dispatch/archive/merge gate + mint spec-approved (human) + reconcile ratified records
  {C.GREEN}ratify{C.RESET} <change> --reason "..." Human-only ratified approval of a change (HUMAN-DECISION, mandatory reason)
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
  {C.GREEN}credential-helper{C.RESET} <op>     Git credential helper (resolves secret_ref; stores nothing)
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
        # version-authority 1.3 — the otaman-deploy RELEASE is the authoritative
        # installed version; this package's version is a component detail. It
        # used to print `otaman <cli package version>`, which a tenant would
        # reasonably quote as their installed version — and could not verify.
        from otaman_cli.release_version import format_version, resolve_release_version

        print(format_version(resolve_release_version(VERSION)))
        return 0

    # interactive-human-console: `otaman -i` opens the TTY human console (a
    # human seat with no LLM in the loop). Optional `console` extra (Textual).
    #
    # cli-component-namespacing 1.2: the HOME is `otaman cli interactive`, and this
    # is a PERMANENT alias, not a deprecation — it is baked into every generated
    # CLAUDE.local.md on the fleet. So it prints no notice: warning someone about a
    # spelling that is not going anywhere teaches them to ignore warnings. It routes
    # through the namespaced verb rather than calling `run_console` itself, so the
    # two spellings cannot drift — which is the failure the scattered shapes caused.
    if args[0] in ("-i", "--interactive"):
        from otaman_cli.commands.cli_group import cmd_cli

        return cmd_cli(["interactive", *args[1:]])

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
