"""`otaman project` — migrated from main.py's early special-case dispatch."""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI


def cmd_project(args: list[str]) -> int:
    """`otaman project <action> [...]` — project/repo registry commands.

    Subcommands:
      add        — create remote repo + register (CVS, gated on otaman-core 1.x)
      assign     — register an existing local git repo (local-only; works now)
      list       — list registered repos
      show       — show one repo's full detail
      update     — modify repos[] entry fields
      disable    — set status: inactive
      enable     — restore to active (drop status field)
      remove     — deregister from platform.yaml; optional --delete-remote
    """
    if not args:
        UI.error("Usage: otaman project <action> [options]")
        UI.muted("Actions: add | assign | list | show | update | disable | enable | remove")
        return 1
    action, *rest_args = args
    rest = list(rest_args)

    # Pull flag values (simple consumer; flags can interleave with positional)
    def _take(flag: str) -> str | None:
        if flag in rest:
            i = rest.index(flag)
            if i + 1 < len(rest):
                value = rest[i + 1]
                del rest[i:i + 2]
                return value
        return None

    def _take_bool(flag: str) -> bool:
        if flag in rest:
            rest.remove(flag)
            return True
        return False

    # Common flags consumed first so positional pickup is clean
    owner = _take("--owner")
    name_flag = _take("--name")
    path_flag = _take("--path")
    url_flag = _take("--url")
    description_flag = _take("--description")
    status_flag = _take("--status")
    delete_remote = _take_bool("--delete-remote")

    # Remaining positional after flag stripping
    positional = [a for a in rest if not a.startswith("-")]
    primary = positional[0] if positional else ""

    if action == "assign":
        from otaman_cli.project.cmd_assign import cmd_project_assign
        return cmd_project_assign(primary, owner=owner, name=name_flag)
    if action == "list":
        from otaman_cli.project.cmd_list import cmd_project_list
        return cmd_project_list(status=status_flag or "active")
    if action == "show":
        from otaman_cli.project.cmd_show import cmd_project_show
        return cmd_project_show(primary)
    if action == "update":
        from otaman_cli.project.cmd_update import cmd_project_update
        return cmd_project_update(
            primary, owner=owner, path=path_flag,
            url=url_flag, description=description_flag,
        )
    if action == "disable":
        from otaman_cli.project.cmd_status import cmd_project_disable
        return cmd_project_disable(primary)
    if action == "enable":
        from otaman_cli.project.cmd_status import cmd_project_enable
        return cmd_project_enable(primary)
    if action == "remove":
        from otaman_cli.project.cmd_remove import cmd_project_remove
        return cmd_project_remove(primary, delete_remote=delete_remote)
    if action == "add":
        UI.error("`otaman project add` is not yet implemented in this phase.")
        UI.muted("Depends on otaman-core 1.x (GitHostAdapter.create_repo). Use `otaman project assign` for existing local repos.")
        return 2

    UI.error(f"Unknown project action: {action}")
    UI.muted("Available: add | assign | list | show | update | disable | enable | remove")
    return 2


register(CommandSpec(name="project", handler=cmd_project, help="Repo registry: assign / list / show / update / disable / enable / remove"))
