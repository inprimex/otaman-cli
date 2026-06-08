"""Interactive wizard for launch-settings.yaml (tasks 1.2, 1.3).

Linear prompt flow with one-line explanations before each choice. spec-agent
is mandatory and locked — the checklist UI cannot deselect it. `--yes` mode
short-circuits the prompts: returns the all-defaults LaunchSettings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from otaman_cli.init.schema import (
    AgentEntry,
    Connection,
    LaunchSettings,
    SSHParams,
    TmuxLayoutConfig,
)


SPEC_AGENT = "spec-agent"


def default_settings(*, project_name: str, extra_agent_names: list[str] | None = None) -> LaunchSettings:
    """Build a `LaunchSettings` populated with all defaults — no prompts."""
    agents = [AgentEntry(name=SPEC_AGENT, enabled=True)]
    for name in extra_agent_names or []:
        if name == SPEC_AGENT:
            continue
        # Extras present in platform.yaml but not auto-enabled — user can flip
        agents.append(AgentEntry(name=name, enabled=False))
    return LaunchSettings(
        version=1,
        connection=Connection(mode="local"),
        agents=agents,
        tmux=TmuxLayoutConfig(session_prefix=project_name, layout="tiled"),
    )


def _prompt(text: str, *, default: str = "") -> str:
    """Bare input with default fallback on EOF/Ctrl-C."""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"  {text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return raw or default


def _prompt_choice(text: str, *, choices: list[str], default: str) -> str:
    """Prompt for one of several choices; defaults on blank/EOF."""
    while True:
        val = _prompt(text, default=default).lower()
        if val in choices:
            return val
        if not val:
            return default
        print(f"  ! invalid choice {val!r}; try one of: {', '.join(choices)}")


def _print_connection_help() -> None:
    print("  • local  — everything on this machine; no network config needed (default)")
    print("  • ssh    — agents run on a remote server; you connect via SSH")
    print("  • mesh   — agents distributed across machines via otaman mesh network")


def _print_agent_help(extras: list[str]) -> None:
    print("  ◉ spec-agent   (mandatory — cannot be deselected)")
    if extras:
        print(f"  Additional agents from platform.yaml (default: disabled): {', '.join(extras)}")
        print("  Type comma-separated names to enable them, or blank to accept defaults.")


def run_wizard(
    *,
    project_name: str,
    platform_agent_names: Iterable[str] | None = None,
    yes: bool = False,
) -> LaunchSettings:
    """Run the wizard and return a populated LaunchSettings.

    `--yes` mode skips all prompts and returns defaults.  Interactive mode
    prints the design-defined explanations before each prompt.
    spec-agent is hard-locked in both paths.
    """
    extras = [a for a in (platform_agent_names or []) if a != SPEC_AGENT]

    if yes:
        # Non-interactive: all defaults, spec-agent enabled, no extras enabled
        return default_settings(project_name=project_name, extra_agent_names=extras)

    print()
    print("  Project name (used as tmux session prefix)")
    name = _prompt("Project name", default=project_name) or project_name

    print()
    print("  Connection mode — how will your agents connect to the runner?")
    _print_connection_help()
    mode = _prompt_choice("Mode", choices=["local", "ssh", "mesh"], default="local")

    ssh_params: SSHParams | None = None
    if mode == "ssh":
        print()
        print("  SSH connection details")
        host = _prompt("SSH host", default="localhost") or "localhost"
        user = _prompt("SSH user", default="deploy") or "deploy"
        key_path = _prompt("SSH key path (blank for ~/.ssh/id_rsa)") or None
        ssh_params = SSHParams(host=host, user=user, key_path=key_path)

    print()
    print("  Agents to launch")
    _print_agent_help(extras)
    raw = _prompt("Additional agents to enable (comma-separated, blank for none)")
    enabled_extras = {a.strip() for a in raw.split(",") if a.strip()}

    agents = [AgentEntry(name=SPEC_AGENT, enabled=True)]
    for extra in extras:
        agents.append(AgentEntry(name=extra, enabled=extra in enabled_extras))

    print()
    print("  Tmux layout — how the panes are arranged when you attach")
    layout = _prompt_choice(
        "Layout",
        choices=["tiled", "main-horizontal", "main-vertical", "even-horizontal"],
        default="tiled",
    )

    return LaunchSettings(
        version=1,
        connection=Connection(mode=mode, ssh=ssh_params),
        agents=agents,
        tmux=TmuxLayoutConfig(session_prefix=name, layout=layout),
    )


__all__ = ["run_wizard", "default_settings", "SPEC_AGENT"]
