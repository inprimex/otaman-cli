"""`otaman upgrade` — migrated from main.py.

Walks the launcher registry (`_launchers_registry`) and refreshes each
entry: `git pull` the plugin checkout, then `otaman init` the otaman
folder, locally or over SSH depending on the launcher's connection type.

Also where finding F031 (SSH command-injection) lived before its
standalone fix in #83 -- the shlex.quote()/`--`-guard code here predates
this module move.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from otaman_cli import main as _main
from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI
from otaman_cli.safety import confirm_destructive_operation


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
      --yes, -y              skip the batch confirmation prompt
    """
    dry_run = False
    only_launcher: str | None = None
    skip_pull = False
    skip_init = False
    yes = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run":
            dry_run = True
        elif a == "--skip-pull":
            skip_pull = True
        elif a == "--skip-init":
            skip_init = True
        elif a in ("--yes", "-y"):
            yes = True
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

    # Batch-level summary + confirm (task 1.4): resolve each entry's
    # connection type/host up front so the operator sees the full scope
    # before anything runs. Mirrors the settings-read + resolve-connection
    # steps the main loop below performs per entry -- a second, lightweight
    # pass, not a refactor of the loop itself; a parse failure here is
    # silently skipped since the main loop will surface it properly as a
    # per-launcher failure.
    if not dry_run:
        hosts: set[str] = set()
        for entry in entries:
            ls_path = Path(entry["path"]) / "launch-settings.yaml"
            if not ls_path.is_file():
                continue
            try:
                settings = yaml.safe_load(ls_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            active_name = settings.get("active_connection")
            connections = settings.get("connections") or {}
            if not active_name or active_name not in connections:
                continue
            conn = _resolve_connection(connections, active_name)
            ctype = (conn.get("type") or "ssh").lower()
            if ctype in ("ssh", "mesh"):
                host = conn.get("ssh_default_host")
                hosts.add(host if host else "<unknown host>")
            else:
                hosts.add("local")

        host_desc = f" across {len(hosts)} host{'s' if len(hosts) != 1 else ''}" if hosts else ""
        if not confirm_destructive_operation(
            f"{len(entries)} launcher{'s' if len(entries) != 1 else ''}{host_desc} "
            f"will be modified (git pull + otaman init):",
            [e["path"] for e in entries],
            yes=yes,
        ):
            UI.muted("Aborted — no changes made.")
            return 1

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

        # F031: host/ssh_key come from launch-settings.yaml, a file an
        # attacker with write access to shared config/dotfiles could taint.
        # A leading '-' would let a value like -oProxyCommand=... be parsed
        # as an ssh option instead of a hostname/path (local command
        # injection), since ssh_cmd is a flat argv list with no separator.
        for field_name, value in (("ssh_default_host", host), ("ssh_key", ssh_key)):
            if value and value.startswith("-"):
                UI.error(f"    Refusing unsafe {field_name} value starting with '-': {value!r}")
                return 2

        ssh_cmd = ["ssh"]
        if ssh_key:
            ssh_cmd += ["-i", ssh_key]
        # "--" marks the end of ssh's own options, so host can never be
        # misparsed as a flag even if the leading-'-' guard above is bypassed.
        ssh_cmd += ["--", host]

        if not skip_pull and plugin_path:
            # plugin_path is interpolated into a string the *remote* shell
            # parses (ssh's implicit `sh -c`), so it must be shell-quoted
            # here -- passing it as a separate argv element wouldn't help,
            # since ssh joins argv into one string for the remote shell.
            remote = f"cd {shlex.quote(plugin_path)} && git pull --ff-only"
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
                f"cd {shlex.quote(maestro_root)} && "
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
        # NOTE: this must resolve relative to main.py's location, not this
        # module's -- otaman_cli.main.__file__ is used explicitly rather
        # than __file__ so the move out of main.py doesn't shift the path
        # up an extra directory level.
        plugin_root = Path(_main.__file__).resolve().parent.parent

        if not skip_pull:
            UI.muted(f"    Run: git -C {plugin_root} pull --ff-only")
            if not dry_run:
                rc = subprocess.run(["git", "-C", str(plugin_root), "pull", "--ff-only"]).returncode
                if rc != 0:
                    return rc

        if not skip_init:
            UI.muted(f"    Run: otaman init  (cwd={local_root})")
            if not dry_run:
                # Re-invoke main.py as a script (`python3 main.py init`), same
                # reason as plugin_root above: must be main.py's path, not
                # this module's, for `if __name__ == "__main__"` to fire.
                rc = subprocess.run(
                    [sys.executable, str(Path(_main.__file__).resolve()), "init"],
                    cwd=local_root,
                ).returncode
                if rc != 0:
                    return rc
        return 0

    UI.error(f"    Unknown connection type: {ctype}")
    return 2


register(CommandSpec(
    name="upgrade",
    handler=cmd_upgrade,
    help="Walk launcher registry: git pull + otaman init each",
))
