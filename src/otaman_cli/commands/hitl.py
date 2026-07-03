"""`otaman hitl` — migrated from main.py's early special-case dispatch."""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI


def cmd_hitl(args: list[str]) -> int:
    """`otaman hitl <action> [...]` — HITL stack (request-human-review / human-decision)."""
    if not args:
        UI.error("Usage: otaman hitl <action> [options]")
        UI.muted("Actions: list | next | take <id>")
        return 1
    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)
    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")
    from otaman_cli.hitl import commands as _hitl
    return _hitl.dispatch(action, parsed)


register(CommandSpec(name="hitl", handler=cmd_hitl, help="HITL stack: list pending review requests, next, take <id>"))
