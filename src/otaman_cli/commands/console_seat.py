"""`otaman console seat` — seat the human console from the launcher (1.2/1.3).

The launcher needs to seat a console, and the D1 boundary says exactly where
that seat may land. Putting the tmux invocation in the generated bash would put
the rule in a template — a place where a tenant edit, a regenerated launcher or
a copy-paste can quietly move the seat onto the fleet socket, which is the one
mistake the never-inject boundary exists to prevent.

So the template calls THIS, and the rule stays in :func:`seat_console`: one
implementation, one refusal message, and a non-zero exit when the seat did not
happen. A launcher that carries on after a refused seat would be reporting
success for work it did not do.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.console.seat import (
    SEAT_ALREADY,
    SEAT_CREATED,
    SEAT_REFUSED_SOCKET,
    seat_console,
)
from otaman_cli.main import UI


def _seat(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="otaman console seat",
        description="Seat the human console on its private tmux server.",
    )
    parser.add_argument("--socket", default=None, help="tmux socket (must be the private one)")
    parser.add_argument("--session", default=None, help="tmux session name")
    parser.add_argument("--command", default="", help="command to run inside the seat")
    args = parser.parse_args(argv)

    kwargs: dict = {"command": args.command}
    if args.socket:
        kwargs["socket"] = args.socket
    if args.session:
        kwargs["session"] = args.session

    outcome, message = seat_console(**kwargs)

    if outcome == SEAT_CREATED:
        UI.ok(message)
        return 0
    if outcome == SEAT_ALREADY:
        # Idempotent, and a SUCCESS: the console the operator wanted is running.
        UI.ok(message)
        return 0
    if outcome == SEAT_REFUSED_SOCKET:
        UI.error("Refusing to seat the console.")
        for line in message.splitlines():
            UI.muted(f"  {line}")
        return 2
    UI.error(message)
    return 1


def cmd_console(args: list[str]) -> int:
    if not args or args[0] in {"-h", "--help", "help"}:
        UI.info("otaman console <subcommand>")
        UI.muted("  seat [--socket S] [--session S] [--command C]   Seat the human console")
        return 0 if args else 1
    if args[0] == "seat":
        return _seat(args[1:])
    UI.error(f"unknown `otaman console` subcommand: {args[0]!r}")
    return 1


register(
    CommandSpec(
        name="console",
        handler=cmd_console,
        help="Human console: seat it on the private tmux server",
    )
)

__all__ = ["cmd_console"]
