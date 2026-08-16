"""`otaman runner platforms|token` — on-disk runner config management.

Implements `specs/otaman-runner-platforms/spec.md`. Re-authored from closed
PR #82 against the post-F020 command registry, with the review's contract
fixes: shared `safety.confirm_destructive_operation` (TTY-safe, --yes) in
place of a bespoke prompt, hard errors on unknown or valueless flags (a
dropped `--token-file` must never silently retarget the default token),
and rotation messaging that is truthful about when the runner actually
adopts the new value (only a `--token-source file:` runner re-reads it).

Manages on-disk state only (symlinks + a token file) — never starts,
stops, or signals a runner process.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.commands._flag_parsing import _parse_flag_value
from otaman_cli.main import UI, C

_RUNNER_USAGE = (
    "Usage:\n"
    "  otaman runner platforms add <path-to-platform.yaml> [--force] [--platforms-dir D]\n"
    "  otaman runner platforms list [--platforms-dir D]\n"
    "  otaman runner platforms remove <program-name> [--platforms-dir D]\n"
    "  otaman runner token install [--force] [--yes] [--token-file F]\n"
    "  otaman runner token rotate [--yes] [--token-file F]\n"
    "  otaman runner token show [--reveal] [--token-file F]"
)


def _pop_bool(rest: list[str], flag: str) -> bool:
    if flag in rest:
        rest.remove(flag)
        return True
    return False


def _reject_leftover_flags(rest: list[str]) -> str | None:
    """Return an error string if any '-'-prefixed token survived parsing.

    Catches both unknown flags AND a known value-flag left valueless at the
    end of the line (`_parse_flag_value` only consumes `--flag VALUE` pairs).
    Silently ignoring either caused the worst finding of the original PR:
    `token rotate --yes --token-file` rotating the real default token.
    """
    stray = [a for a in rest if a.startswith("-")]
    if stray:
        return f"Unknown or valueless flag(s): {', '.join(stray)}"
    return None


def _cmd_platforms(rest: list[str]) -> int:
    from otaman_cli import _runner_registry as rr

    if not rest:
        UI.error("Missing platforms subcommand")
        UI.muted(_RUNNER_USAGE)
        return 1

    sub, rest = rest[0], list(rest[1:])
    dir_override = _parse_flag_value(rest, "--platforms-dir")
    force = _pop_bool(rest, "--force")
    if err := _reject_leftover_flags(rest):
        UI.error(err)
        UI.muted(_RUNNER_USAGE)
        return 2
    positional = rest

    if sub == "add":
        if not positional:
            UI.error("path to platform.yaml required")
            UI.muted("Usage: otaman runner platforms add <path> [--force]")
            return 1
        try:
            result = rr.platforms_add(positional[0], force=force, dir_override=dir_override)
        except rr.PlatformsError as e:
            UI.error(str(e))
            return 1
        if result["status"] == "already-installed":
            UI.ok(f"Already installed: {result['name']} -> {result['target']}")
        else:
            UI.ok(f"Registered '{result['name']}' -> {result['link']}")
            UI.muted(f"  target: {result['target']}")
        return 0

    if sub == "list":
        entries = rr.platforms_list(dir_override=dir_override)
        if not entries:
            UI.muted("No platforms registered.")
            UI.muted("  otaman runner platforms add <path-to-platform.yaml>")
            return 0
        UI.header(f"Registered Platforms ({len(entries)})")
        markers = {
            "dangling": f" {C.RED}[dangling]{C.RESET}",
            "unmanaged": f" {C.YELLOW}[unmanaged]{C.RESET}",
        }
        for e in entries:
            print(f"  {e['name']}  ->  {e['target']}{markers.get(e['state'], '')}")
        return 0

    if sub == "remove":
        if not positional:
            UI.error("program name required")
            UI.muted("Usage: otaman runner platforms remove <program-name>")
            return 1
        try:
            result = rr.platforms_remove(positional[0], dir_override=dir_override)
        except rr.PlatformsError as e:
            UI.warn(str(e))
            return 1
        UI.ok(f"Removed '{result['name']}'")
        return 0

    UI.error(f"Unknown platforms subcommand: {sub}")
    UI.muted(_RUNNER_USAGE)
    return 1


def _adoption_advisory(path) -> None:
    """Truthful rotation/adoption guidance (review finding #2): the runner
    re-reads this file on SIGHUP ONLY when launched with a persistent
    --token-source; its default source is 'random', which never reads it."""
    UI.muted(f"  If the runner was launched with --token-source file:{path},")
    UI.muted("  send it SIGHUP to adopt the new token without a restart.")
    UI.muted("  Otherwise (default 'random' source) the runner does NOT read this")
    UI.muted(f"  file — relaunch it with --token-source file:{path}.")


def _cmd_token(rest: list[str]) -> int:
    from otaman_cli import _runner_registry as rr
    from otaman_cli.safety import confirm_destructive_operation

    if not rest:
        UI.error("Missing token subcommand")
        UI.muted(_RUNNER_USAGE)
        return 1

    sub, rest = rest[0], list(rest[1:])
    file_override = _parse_flag_value(rest, "--token-file")
    force = _pop_bool(rest, "--force")
    yes = _pop_bool(rest, "--yes")
    reveal = _pop_bool(rest, "--reveal")
    if err := _reject_leftover_flags(rest):
        UI.error(err)
        UI.muted(_RUNNER_USAGE)
        return 2

    if sub == "install":
        path = rr.token_file(file_override)
        if path.is_file() and force:
            if not confirm_destructive_operation(
                "Regenerate the runner persistence token? The live token stays valid "
                "until the runner re-reads the file.",
                str(path),
                yes=yes,
            ):
                UI.muted("Reinstall cancelled.")
                return 1
        result = rr.token_install(force=force, file_override=file_override)
        if result["status"] == "already-installed":
            UI.ok(f"Already installed: {result['path']} ({rr.mask_token(result['token'])})")
            UI.muted("  Run 'otaman runner token rotate' to replace it.")
            return 0
        verb = "Reinstalled" if result["status"] == "reinstalled" else "Installed"
        UI.ok(f"{verb} token: {result['path']} ({rr.mask_token(result['token'])})")
        UI.muted(f"  Launch the runner with --token-source file:{result['path']}")
        return 0

    if sub == "rotate":
        path = rr.token_file(file_override)
        if not path.is_file():
            UI.error(f"No token installed at {path}")
            UI.muted("  Run 'otaman runner token install' first.")
            return 1
        if not confirm_destructive_operation(
            "Rotate the runner persistence token? This invalidates the current "
            "secret for anything that re-reads the file.",
            str(path),
            yes=yes,
        ):
            UI.muted("Rotate cancelled.")
            return 1
        result = rr.token_rotate(file_override=file_override)
        UI.ok(f"Rotated token: {result['path']} ({rr.mask_token(result['token'])})")
        _adoption_advisory(result["path"])
        return 0

    if sub == "show":
        try:
            result = rr.token_show(file_override=file_override)
        except rr.TokenError as e:
            UI.error(str(e))
            return 1
        mode = result["mode"]
        UI.kv("path", str(result["path"]))
        UI.kv("mode", f"{mode:o}")
        UI.kv("token", result["token"] if reveal else rr.mask_token(result["token"]))
        if mode & ~0o600 & 0o777:
            UI.warn(f"Token file mode is {mode:o}, should be 600")
        return 0

    UI.error(f"Unknown token subcommand: {sub}")
    UI.muted(_RUNNER_USAGE)
    return 1


def cmd_runner(args: list[str]) -> int:
    """Manage the local otaman-runner's on-disk config."""
    if not args:
        UI.error("Missing subcommand")
        UI.muted(_RUNNER_USAGE)
        return 1

    family, rest = args[0], args[1:]
    if family == "platforms":
        return _cmd_platforms(rest)
    if family == "token":
        return _cmd_token(rest)

    UI.error(f"Unknown runner subcommand: {family}")
    UI.muted(_RUNNER_USAGE)
    return 1


register(
    CommandSpec(
        name="runner",
        handler=cmd_runner,
        help="Manage runner on-disk config: platforms symlinks + stable token",
    )
)
