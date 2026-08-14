"""Trivial pure-dispatch commands — migrated from main.py.

Each of these is a thin forward to a dedicated module or legacy script;
none has any main()-level flag-loop dependency or exclusive helper, so
this is the cheapest tier of the F020 migration. Grouped into one file
since each is a handful of lines.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI, run_script


def cmd_notify_change_dispatch(args: list[str]) -> int:
    """Lazy-import wrapper for `otaman notify-change` (post-merge-spec-notify 1.1)."""
    from otaman_cli.notify_change import cmd_notify_change

    return cmd_notify_change(args)


def cmd_watchdog_dispatch(args: list[str]) -> int:
    """Lazy-import wrapper for `otaman watchdog ...` so urllib + endpoint
    discovery don't load on every CLI invocation (the watchdog is a
    rarely-used surface; most operators never hit it).
    """
    from otaman_cli.watchdog import cmd_watchdog

    return cmd_watchdog(args)


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


register(
    CommandSpec(
        name="notify-change",
        handler=cmd_notify_change_dispatch,
        help="Send spec-change notification",
    )
)
register(
    CommandSpec(
        name="watchdog",
        handler=cmd_watchdog_dispatch,
        help="Query/control the runner watchdog (HTTP)",
    )
)
register(
    CommandSpec(
        name="models", handler=cmd_models, help="Inspect / manage model + effort tier overrides"
    )
)
register(
    CommandSpec(
        name="accounts",
        handler=cmd_accounts,
        help="Manage launcher routing (multi-subscription identities)",
    )
)
register(
    CommandSpec(
        name="routing",
        handler=cmd_accounts,
        help="Manage launcher routing (multi-subscription identities)",
    )
)
register(
    CommandSpec(name="ping", handler=cmd_ping, help="Proactively notify the user via Telegram")
)
register(
    CommandSpec(
        name="afk", handler=cmd_afk, help="AFK toggle — when on, approvals route to Telegram"
    )
)
register(
    CommandSpec(
        name="bridge", handler=cmd_bridge, help="Bridge daemon lifecycle (install/run/status)"
    )
)
register(
    CommandSpec(
        name="mcp-config", handler=cmd_mcp_config, help="Emit .mcp.json for Claude Code (team mode)"
    )
)
register(
    CommandSpec(
        name="session", handler=cmd_session, help="Spawn a Claude session under logged-in user"
    )
)
register(
    CommandSpec(name="install-cli", handler=cmd_install_cli, help="Install `otaman` shim on PATH")
)
register(CommandSpec(name="onboard", handler=cmd_onboard, help="User / project provisioning"))
register(
    CommandSpec(
        name="login",
        handler=lambda args: cmd_login(["login"] + args),
        help="Authenticate via OIDC device flow; cache token",
    )
)
register(
    CommandSpec(
        name="logout", handler=lambda args: cmd_login(["logout"] + args), help="Remove cached token"
    )
)
register(
    CommandSpec(
        name="token",
        handler=lambda args: cmd_login(["show"] + args),
        help="Show cached-token metadata (no secrets)",
    )
)
