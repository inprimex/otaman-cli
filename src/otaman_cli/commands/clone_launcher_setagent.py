"""`otaman clone`/`launcher`/`set-agent` — migrated from main.py.

Three small, independent commands grouped into one module since none
of them share state or helpers with each other or with any
not-yet-migrated command. `clone`'s `--target` flag was already
exclusive (parsed in main()'s shared loop only for `clone`), so it
drops out of that loop entirely (F021/F022) rather than being
duplicated.
"""

from __future__ import annotations

import sys
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI, C, run_script


def cmd_clone(args: list[str]) -> int:
    """Clone all project repos from a otaman configuration."""
    target = ""
    positional: list[str] = []
    i = 0
    while i < len(args):
        if (
            args[i] in ("--maestro-dir", "--otaman-dir", "--target")  # legacy: backward-compat
            and i + 1 < len(args)
        ):
            target = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1
    args = positional

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
            UI.error(f"{f_['name']}: {f_.get('error', 'unknown')}")

    # Doctor summary
    doctor = report.get("doctor", {})
    if doctor:
        p, w, f_ = doctor.get("passed", 0), doctor.get("warned", 0), doctor.get("failed", 0)
        print()
        if f_ == 0:
            UI.ok(f"Environment: {p} checks passed, {w} warnings")
        else:
            UI.warn(
                f"Environment: {p} passed, {w} warnings, {f_} failed "
                f"— run otaman doctor for details"
            )

    maestro_dir = report.get("maestro_dir", "")
    print()
    UI.kv("Otaman folder", maestro_dir)
    UI.muted("Next: launch agents or run otaman doctor for full environment check")

    return 1 if failed else 0


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
        f"  otaman-agent {name}                    "
        "# shell function (install via `otaman init --shell`)\n"
        f"  OTAMAN_AGENT={name} otaman <cmd>       # one-shot\n"
        "\n"
        "To make identity automatic, run from inside a repo whose .otaman has 'agent: <name>'.\n"
        "Run 'otaman init --update' to write agent: fields to all repos.",
        file=sys.stderr,
    )
    return 1


register(
    CommandSpec(
        name="clone",
        handler=cmd_clone,
        help="Clone all repos from otaman config (git URL, SSH, local)",
    )
)
register(
    CommandSpec(
        name="launcher",
        handler=cmd_launcher,
        help="Launcher management: scaffold, list, add, remove, register",
    )
)
register(
    CommandSpec(
        name="set-agent", handler=cmd_set_agent, help="DEPRECATED: no-op, prints migration guidance"
    )
)
