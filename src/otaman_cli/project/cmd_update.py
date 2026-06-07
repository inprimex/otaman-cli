"""`otaman project update <name> [--owner|--path|--url|--description]` (task 6.1, 6.2)."""

from __future__ import annotations

from typing import Any

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import (
    find_repo,
    git_commit_platform_yaml,
    load_platform_yaml,
    save_platform_yaml,
    update_repo,
)


_UPDATABLE_FIELDS = ("owner", "path", "url", "description")


def cmd_project_update(
    name: str,
    *,
    owner: str | None = None,
    path: str | None = None,
    url: str | None = None,
    description: str | None = None,
) -> int:
    if not name:
        UI.error("Usage: otaman project update <name> [--owner|--path|--url|--description]")
        return 1
    # Build fields dict; map user-facing --url flag onto the schema-accepted
    # `remote:` key on the platform.yaml repo entry.
    fields: dict[str, Any] = {
        k: v for k, v in {
            "owner": owner, "path": path, "remote": url, "description": description,
        }.items() if v is not None
    }
    if not fields:
        UI.error("No field flags provided. At least one of --owner / --path / --url / --description required.")
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

    updated = update_repo(data, name, fields)
    if not updated:
        UI.error(f"Update failed for {name}")
        return 1
    save_platform_yaml(root, data)

    field_list = ", ".join(sorted(fields.keys()))
    UI.ok(f"Updated {name}: {field_list}")

    rc, out = git_commit_platform_yaml(
        root, f"chore(platform): update repo {name} fields ({field_list})",
    )
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")
    return 0


__all__ = ["cmd_project_update"]
