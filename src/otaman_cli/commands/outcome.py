"""`otaman outcome` — migrated from main.py's early special-case dispatch
(F020/F022: this command has rich per-action flags the generic flag loop
would mishandle, so it always bypassed the loop; now it's a registry
entry instead of a hand-maintained if-branch).
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.commands._flag_parsing import _parse_flag_value
from otaman_cli.main import UI


def cmd_outcome(args: list[str]) -> int:
    """`otaman outcome <action> [...]` — dispatches to cli_outcome.dispatch."""
    if not args:
        UI.error("Usage: otaman outcome <action> [options]")
        UI.muted(
            "Actions: add | list | show | history | promote | demote | "
            "retire | request-estimate | accept-cost | reject-cost"
        )
        return 1

    action, *rest_args = args
    rest = list(rest_args)
    parsed: dict[str, object] = {}

    # Common: positional <id> for show/history/promote/demote/retire/etc.
    if rest and not rest[0].startswith("-"):
        parsed["id"] = rest.pop(0)

    # action-specific flags
    parsed["as_a"] = _parse_flag_value(rest, "--as-a")
    parsed["i_want_to"] = _parse_flag_value(rest, "--i-want-to")
    parsed["incremental_outcome"] = _parse_flag_value(rest, "--incremental-outcome")
    parsed["so_i_can"] = _parse_flag_value(rest, "--so-i-can")
    parsed["ultimate_outcome"] = _parse_flag_value(rest, "--ultimate-outcome")
    parsed["category"] = _parse_flag_value(rest, "--category")
    parsed["persona"] = _parse_flag_value(rest, "--persona")
    parsed["impact"] = _parse_flag_value(rest, "--impact")
    parsed["priority"] = _parse_flag_value(rest, "--priority")
    parsed["product_notes"] = _parse_flag_value(rest, "--product-notes")
    parsed["release"] = _parse_flag_value(rest, "--release")
    parsed["status"] = _parse_flag_value(rest, "--status")
    parsed["reason"] = _parse_flag_value(rest, "--reason") or _parse_flag_value(rest, "--note")
    parsed["solution"] = _parse_flag_value(rest, "--solution")
    # `add` accepts an explicit --id if positional wasn't used
    if not parsed.get("id"):
        parsed["id"] = _parse_flag_value(rest, "--id")

    if rest:
        UI.warn(f"Unrecognised arguments ignored: {rest}")

    from otaman_cli.registries import cli_outcome

    return cli_outcome.dispatch(action, parsed)


register(CommandSpec(name="outcome", handler=cmd_outcome, help="Program outcome registry (JTBD)"))
