"""`otaman solution` — migrated from main.py's early special-case dispatch
(same reasoning as commands/outcome.py: rich per-action flags bypass the
generic flag loop).
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.commands._flag_parsing import _parse_dependencies, _parse_flag_list, _parse_flag_value
from otaman_cli.main import UI


def cmd_solution(args: list[str]) -> int:
    """`otaman solution <action> [...]` — dispatches to cli_solution.dispatch."""
    if not args:
        UI.error("Usage: otaman solution <action> [options]")
        UI.muted("Actions: add | list | show | history | propose | "
                 "promote-to-complete | discard")
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}

    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)

    parsed["outcome"] = _parse_flag_value(rest, "--outcome")
    parsed["description"] = _parse_flag_value(rest, "--description")
    parsed["t_shirt"] = _parse_flag_value(rest, "--t-shirt")
    ef = _parse_flag_value(rest, "--effort-days")
    parsed["effort_days"] = float(ef) if ef else None
    parsed["release"] = _parse_flag_value(rest, "--release")
    parsed["cto_notes"] = _parse_flag_value(rest, "--cto-notes")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason") or _parse_flag_value(rest, "--note")
    parsed["pros"] = _parse_flag_list(rest, "--pro")
    parsed["cons"] = _parse_flag_list(rest, "--con")
    parsed["dependencies"] = _parse_dependencies(_parse_flag_list(rest, "--depends-on"))
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_solution
    return cli_solution.dispatch(action, parsed)


register(CommandSpec(name="solution", handler=cmd_solution, help="Program solution registry"))
