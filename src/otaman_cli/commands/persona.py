"""`otaman persona` — migrated from main.py's early special-case dispatch
(same reasoning as commands/outcome.py: rich per-action flags bypass the
generic flag loop).
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.commands._flag_parsing import _parse_flag_value
from otaman_cli.main import UI


def cmd_persona(args: list[str]) -> int:
    """`otaman persona <action> [...]` — dispatches to cli_persona.dispatch."""
    if not args:
        UI.error("Usage: otaman persona <action> [options]")
        UI.muted("Actions: add | list | show | retire")
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)
    parsed["name"] = _parse_flag_value(rest, "--name")
    parsed["description"] = _parse_flag_value(rest, "--description")
    parsed["kind"] = _parse_flag_value(rest, "--kind")
    parsed["domain_prefill_source"] = _parse_flag_value(rest, "--domain-prefill-source")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason")
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_persona

    return cli_persona.dispatch(action, parsed)


register(CommandSpec(name="persona", handler=cmd_persona, help="Program persona registry"))
