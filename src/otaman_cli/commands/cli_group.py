"""`otaman cli <verb>` — CLI self-management, component-first (cli-component-namespacing).

Install- and console-related commands were scattered across three shapes:
`otaman install-cli` (a hyphenated top-level verb), `otaman -i` (a top-level flag),
and `otaman runner <verb>` (component-first, already shipping). Roman hit the scatter
live on the sunflowers fresh tenant — `install-cli` broken and `-i` missing the
console extra in one session, reported separately because they did not look like one
problem.

The delta rules component-first, because that pattern already ships. So this is the
home, and `otaman runner <verb>` is the shape it matches.

Two positions on the old spellings, and they are deliberately different:

**`otaman install-cli` is a DEPRECATION alias.** It still works, prints a notice
naming the new home, and a later change removes it. The timing is mine per the scope
guard, and nothing is removed here — a tenant mid-install does not get a broken
command because a name moved.

**`otaman -i` is a PERMANENT alias, not a deprecation.** It is baked into every
generated CLAUDE.local.md on the fleet and into the muscle memory of the one human
who uses it most. The delta says "retained as a permanently documented alias", so it
prints no notice: warning someone about a spelling that is not going anywhere is
noise that teaches them to ignore warnings.

Both route to the same handlers, so the two spellings cannot drift in behavior —
which is the failure the scatter produced in the first place.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.ui import UI

#: One line per entry, printed line by line — `UI.muted` indents only what it is
#: handed, so a single embedded-newline string renders with every line after the
#: first flush left against the error above it.
_CLI_USAGE = (
    "Usage: otaman cli install [--apply] [--prefix DIR]",
    "       otaman cli interactive [--path DIR] [--no-seat]",
    "",
    "Aliases: `otaman install-cli` (deprecated — use `otaman cli install`)",
    "         `otaman -i` / `--interactive` (permanent alias for `cli interactive`)",
)


def _usage() -> None:
    for line in _CLI_USAGE:
        UI.muted(line)


#: Printed when the deprecated top-level spelling is used. Names the new home,
#: because a deprecation notice that does not say what to run instead only tells the
#: reader they are wrong.
DEPRECATION_NOTICE = (
    "`otaman install-cli` is deprecated — use `otaman cli install` "
    "(same behavior; this alias still works and will be removed by a later change)."
)


def _cmd_cli_install(rest: list[str]) -> int:
    """`otaman cli install` — the new home of `install-cli` (1.1).

    Delegates to the SAME handler the old spelling used, rather than re-deriving the
    install, so the two cannot behave differently while both exist.
    """
    from otaman_cli.commands.simple_dispatch import cmd_install_cli

    return cmd_install_cli(rest)


def _cmd_cli_interactive(rest: list[str]) -> int:
    """`otaman cli interactive` — the human console (1.2).

    The same `run_console` that `otaman -i` reaches, for the same reason.
    """
    from otaman_cli.console.launch import run_console

    return run_console(rest)


def cmd_cli(args: list[str]) -> int:
    """Manage the otaman CLI itself: install it, open its interactive console."""
    if not args or args[0] in ("-h", "--help", "help"):
        if not args:
            UI.error("Missing subcommand")
        _usage()
        return 1 if not args else 0

    verb, rest = args[0], args[1:]
    if verb == "install":
        return _cmd_cli_install(rest)
    if verb in ("interactive", "console"):
        # `console` accepted as a synonym because that is what the thing IS called
        # everywhere else in this repo (console/, run_console, the `console` extra),
        # and refusing the word a reader already has is a worse surface than one
        # extra spelling under a namespaced verb. NOT a new top-level spelling —
        # the delta forbids those, not synonyms within the group.
        return _cmd_cli_interactive(rest)

    UI.error(f"Unknown cli subcommand: {verb}")
    _usage()
    return 1


register(
    CommandSpec(
        name="cli",
        handler=cmd_cli,
        help="Manage the otaman CLI: `cli install` (on PATH) | `cli interactive` (console)",
    )
)
