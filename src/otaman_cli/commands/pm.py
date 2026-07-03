"""`otaman pm` — migrated from main.py's early special-case dispatch.

Consolidates the inline dispatch block that used to live in main()'s
early-special-case section with the identical (but previously dead,
since the inline block always intercepted first) `_cmd_pm_dispatch`
helper that only the legacy `commands = {...}` dict entry ever pointed
at -- one of F022's "duplicate dead dict entries" is this pair.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI


def cmd_pm(args: list[str]) -> int:
    """`otaman pm <action> [...]` — PM tool sync (Easy8 / Redmine)."""
    from otaman_cli.pm.cmd_init import cmd_pm_init
    from otaman_cli.pm.cmd_status import cmd_pm_status
    sub = args[0] if args else ""
    rest = args[1:] if args else []
    if sub == "configure":
        from otaman_cli.pm.cmd_configure import cmd_pm_configure
        return cmd_pm_configure(rest)
    elif sub == "init":
        return cmd_pm_init(rest)
    elif sub == "status":
        return cmd_pm_status(rest)
    else:
        UI.error(f"Unknown pm subcommand: {sub!r}. Use: pm configure | pm init | pm status")
        return 1


register(CommandSpec(name="pm", handler=cmd_pm, help="PM tool sync: configure <provider> | init <provider> | status"))
