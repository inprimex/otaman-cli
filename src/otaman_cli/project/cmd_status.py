"""`otaman project disable <name>` + `otaman project enable <name>` (task 7.1, 7.2)."""

from __future__ import annotations

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import (
    find_repo,
    git_commit_platform_yaml,
    load_platform_yaml,
    save_platform_yaml,
)


def _toggle(name: str, *, disable: bool) -> int:
    """Shared body for disable/enable.

    Uses the schema-accepted `disabled: bool` field on the repo entry.
    `disable=True` sets `disabled: true`; `disable=False` removes the key
    (default-active is implicit). Spec uses `status:` terminology in the
    CLI surface, but the platform-schema only accepts `disabled:` — see
    cli-agent → spec-agent message 20260607T-status-vs-disabled-mismatch.
    """
    if not name:
        UI.error("Usage: otaman project disable|enable <name>")
        return 1
    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project")
        return 1
    try:
        data = load_platform_yaml(root)
    except FileNotFoundError as exc:
        UI.error(str(exc))
        return 2
    entry = find_repo(data, name)
    if entry is None:
        UI.error(f"Repo not found: {name}")
        return 1

    if not disable:
        # enable: drop the key entirely so default-active is implicit
        if "disabled" in entry:
            del entry["disabled"]
        commit_msg = f"chore(platform): enable repo {name}"
        action = "enable"
    else:
        entry["disabled"] = True
        commit_msg = f"chore(platform): disable repo {name}"
        action = "disable"

    save_platform_yaml(root, data)
    UI.ok(f"{action.capitalize()}d: {name}")
    rc, out = git_commit_platform_yaml(root, commit_msg)
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")
    return 0


def cmd_project_disable(name: str) -> int:
    return _toggle(name, disable=True)


def cmd_project_enable(name: str) -> int:
    return _toggle(name, disable=False)


__all__ = ["cmd_project_disable", "cmd_project_enable"]
