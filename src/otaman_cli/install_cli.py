#!/usr/bin/env python3
"""otaman install-cli — put the ``otaman`` command on your PATH.

The plugin ships ``cli/maestro.cmd`` (Windows) and ``cli/maestro.sh``  # legacy: launcher filenames
(POSIX) as launchers, but unless those are on PATH users have to call
them by absolute path. This command diagnoses the current state and
either prints the exact command the user should run to fix it
(``--dry-run``, the default) or applies the change when ``--apply`` is
passed.

Behavior by platform:

* **POSIX** (Linux, macOS, WSL) — creates ``~/.local/bin/otaman`` as a
  symlink to ``cli/maestro.sh``  # legacy: launcher filename. If ``~/.local/bin`` isn't on PATH we
  additionally emit (or print) a shell-rc line the user can append.
* **Windows** — appends the plugin's ``cli\\`` directory to the user's
  PATH via ``setx`` (does NOT require admin). The user must open a new
  terminal for the change to take effect; we say so.

Uninstall (``--uninstall``) removes the symlink on POSIX and prints
the setx command needed on Windows (we don't attempt destructive PATH
edits silently).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Layout (post-Step-1 carve): src/otaman_cli/install_cli.py
# Walk up to otaman-cli/ root, then into cli/.
CLI_DIR = Path(__file__).resolve().parent.parent.parent / "cli"
# legacy: launcher shell scripts still use maestro.sh / maestro.cmd filename in plugin repo
POSIX_LAUNCHER = CLI_DIR / "maestro.sh"
WINDOWS_LAUNCHER = CLI_DIR / "maestro.cmd"


# ---------------------------------------------------------------------------
# Platform detection


def is_windows() -> bool:
    return os.name == "nt"


def default_posix_bin() -> Path:
    """Where to put the symlink on POSIX. ``~/.local/bin`` is standard
    (XDG, added to PATH by most modern distros; on macOS you might
    need to add it yourself — we tell you if so)."""
    return Path.home() / ".local" / "bin"


# ---------------------------------------------------------------------------
# POSIX install / uninstall


def posix_install(
    bin_dir: Path | None = None,
    *,
    apply: bool = False,
    out=sys.stdout,
) -> int:
    bin_dir = bin_dir or default_posix_bin()
    target = bin_dir / "otaman"

    if not POSIX_LAUNCHER.exists():
        print(f"ERROR: launcher missing: {POSIX_LAUNCHER}", file=sys.stderr)
        return 1

    # Already installed?
    if target.is_symlink():
        current = target.resolve()
        if current == POSIX_LAUNCHER.resolve():
            print(f"OK: {target} → {POSIX_LAUNCHER}", file=out)
            return _posix_path_hint(bin_dir, out=out)
        print(
            f"WARNING: {target} is a symlink but points elsewhere:\n"
            f"  current: {current}\n  wanted:  {POSIX_LAUNCHER}",
            file=out,
        )
        if not apply:
            print(
                f"\nTo replace it: otaman install-cli --apply",
                file=out,
            )
            return 0
    elif target.exists():
        print(
            f"WARNING: {target} already exists and is NOT a symlink.\n"
            f"Remove it manually if you want to install the otaman "
            f"launcher there.",
            file=out,
        )
        return 1

    # What we would do.
    if not apply:
        print(f"Would symlink: {target} → {POSIX_LAUNCHER}", file=out)
        if not bin_dir.exists():
            print(f"Would create dir: {bin_dir}", file=out)
        print(f"\nRun with --apply to do it.", file=out)
        return _posix_path_hint(bin_dir, out=out, preview=True)

    # Actually do it.
    bin_dir.mkdir(parents=True, exist_ok=True)
    try:
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(POSIX_LAUNCHER)
    except OSError as e:
        print(f"ERROR: failed to symlink: {e}", file=sys.stderr)
        return 1
    # Ensure the launcher itself is executable.
    try:
        mode = POSIX_LAUNCHER.stat().st_mode
        POSIX_LAUNCHER.chmod(mode | 0o111)
    except OSError:
        pass
    print(f"Installed: {target} → {POSIX_LAUNCHER}", file=out)
    return _posix_path_hint(bin_dir, out=out)


def posix_uninstall(
    bin_dir: Path | None = None,
    *,
    apply: bool = False,
    out=sys.stdout,
) -> int:
    bin_dir = bin_dir or default_posix_bin()
    target = bin_dir / "otaman"
    if not target.exists() and not target.is_symlink():
        print(f"Nothing to do: {target} does not exist.", file=out)
        return 0
    if not target.is_symlink():
        print(
            f"WARNING: {target} is not a symlink — refusing to remove.\n"
            f"If you created it by hand, remove it yourself.",
            file=out,
        )
        return 1
    if not apply:
        print(f"Would remove: {target}", file=out)
        print(f"\nRun with --apply to do it.", file=out)
        return 0
    try:
        target.unlink()
    except OSError as e:
        print(f"ERROR: failed to remove {target}: {e}", file=sys.stderr)
        return 1
    print(f"Removed: {target}", file=out)
    return 0


def _posix_path_hint(bin_dir: Path, *, out, preview: bool = False) -> int:
    """Tell the user whether ``bin_dir`` is on PATH; if not, suggest rc line."""
    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if bin_dir in path_dirs:
        return 0
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        rc = "~/.zshrc"
    elif "bash" in shell:
        rc = "~/.bashrc"
    else:
        rc = "your shell rc file"
    line = f'export PATH="{bin_dir}:$PATH"'
    prefix = "Heads up" if not preview else "Note"
    print(
        f"\n{prefix}: {bin_dir} is NOT on your PATH.\n"
        f"Add this to {rc}:\n    {line}\n"
        f"Then restart your shell (or `source {rc}`).",
        file=out,
    )
    return 0


# ---------------------------------------------------------------------------
# Windows install / uninstall


def windows_current_user_path() -> str:
    """Read the *User* PATH from the registry (persistent, vs process
    PATH which is inherited + transient). Returns empty string on
    failure so the rest of the code can still print guidance."""
    try:
        import winreg  # type: ignore
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
            return str(value)
    except OSError:
        return ""


def windows_install(
    *, apply: bool = False, out=sys.stdout,
) -> int:
    if not WINDOWS_LAUNCHER.exists():
        print(f"ERROR: launcher missing: {WINDOWS_LAUNCHER}", file=sys.stderr)
        return 1

    cli_dir_str = str(CLI_DIR)
    user_path = windows_current_user_path()
    user_path_entries = [p.strip() for p in user_path.split(";") if p.strip()]
    already_present = any(
        Path(p).resolve() == CLI_DIR.resolve()
        for p in user_path_entries
        if p
    )

    if already_present:
        print(f"OK: {cli_dir_str} is already on the User PATH.", file=out)
        return 0

    if not apply:
        print(
            f"Would prepend to User PATH: {cli_dir_str}\n"
            f"\nRun with --apply to do it (uses `setx`; does NOT need admin).\n"
            f"After applying you MUST open a new terminal for the PATH "
            f"change to take effect.",
            file=out,
        )
        return 0

    # Apply: use setx so the change survives reboots. Append to the
    # existing User PATH value — we prepend the plugin cli so a conda /
    # asdf / other wrapper-shadowed `otaman` doesn't win.
    new_path = (
        cli_dir_str + ";" + user_path if user_path else cli_dir_str
    )
    # setx truncates at 1024 chars silently — warn if we'd hit that
    # (rare but annoying to debug).
    if len(new_path) > 1024:
        print(
            "WARNING: resulting User PATH exceeds 1024 chars; `setx` "
            "will truncate it. Edit PATH manually via "
            "`rundll32.exe sysdm.cpl,EditEnvironmentVariables` instead.",
            file=out,
        )
        return 1
    setx = shutil.which("setx") or "setx"
    import subprocess
    try:
        result = subprocess.run(
            [setx, "PATH", new_path],
            capture_output=True, text=True, check=False,
        )
    except OSError as e:
        print(f"ERROR: failed to run setx: {e}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(
            f"ERROR: setx failed (rc={result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}",
            file=sys.stderr,
        )
        return 1
    print(
        f"Installed: {cli_dir_str} prepended to User PATH.\n"
        f"Open a new terminal to pick up the change.",
        file=out,
    )
    return 0


def windows_uninstall(*, apply: bool = False, out=sys.stdout) -> int:
    # We deliberately don't edit PATH ourselves on uninstall — too risky
    # if the user has hand-tuned entries. Print the setx invocation they
    # can run instead.
    user_path = windows_current_user_path()
    cli_dir_str = str(CLI_DIR)
    user_path_entries = [p for p in user_path.split(";") if p]
    if not any(Path(p).resolve() == CLI_DIR.resolve()
               for p in user_path_entries if p):
        print(f"Nothing to do: {cli_dir_str} is not on the User PATH.",
              file=out)
        return 0
    filtered = ";".join(
        p for p in user_path_entries
        if Path(p).resolve() != CLI_DIR.resolve()
    )
    print(
        f"To remove {cli_dir_str} from User PATH, run:\n"
        f'    setx PATH "{filtered}"\n'
        f"\n(Not applied automatically — PATH edits are sensitive.)",
        file=out,
    )
    return 0


# ---------------------------------------------------------------------------
# Orchestration


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="otaman install-cli",
        description="Put `otaman` on your PATH.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually make the change. Without this flag, the command "
             "only prints what it would do.",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove the otaman launcher from PATH (POSIX) or print "
             "the command to remove it (Windows).",
    )
    parser.add_argument(
        "--bin-dir",
        help="POSIX only — override ~/.local/bin destination.",
    )
    args = parser.parse_args(argv)

    bin_dir = Path(args.bin_dir).expanduser() if args.bin_dir else None

    if is_windows():
        if args.bin_dir:
            print(
                "WARNING: --bin-dir is POSIX-only; ignored on Windows.",
                file=sys.stderr,
            )
        if args.uninstall:
            return windows_uninstall(apply=args.apply)
        return windows_install(apply=args.apply)

    if args.uninstall:
        return posix_uninstall(bin_dir, apply=args.apply)
    return posix_install(bin_dir, apply=args.apply)


if __name__ == "__main__":
    sys.exit(run())
