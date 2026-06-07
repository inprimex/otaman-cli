"""`otaman project show <name>` (task 5.2)."""

from __future__ import annotations

from pathlib import Path

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import find_repo, is_git_repo, load_platform_yaml


def cmd_project_show(name: str) -> int:
    if not name:
        UI.error("Usage: otaman project show <name>")
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

    UI.header(f"Repo: {name}")
    print(f"  Name:        {entry.get('name')}")
    print(f"  Owner:       {entry.get('owner', '-')}")
    print(f"  Status:      {entry.get('status', 'active')}")
    print(f"  Path:        {entry.get('path', '-')}")
    if entry.get("url"):
        print(f"  URL:         {entry['url']}")
    if entry.get("description"):
        print(f"  Description: {entry['description']}")
    if entry.get("tech"):
        tech = entry["tech"]
        print(f"  Tech:        {', '.join(tech) if isinstance(tech, list) else tech}")

    # Resolved absolute path + local state
    path_rel = entry.get("path") or ""
    if path_rel:
        abs_path = (root / path_rel).expanduser().resolve()
        print()
        UI.subheader("Local state")
        print(f"  Resolved:    {abs_path}")
        print(f"  Exists:      {'yes' if abs_path.exists() else 'NO'}")
        if abs_path.exists():
            print(f"  Git init'd:  {'yes' if is_git_repo(abs_path) else 'no'}")
            otaman_marker = abs_path / ".otaman"
            if otaman_marker.exists():
                kind = "file" if otaman_marker.is_file() else "dir"
                print(f"  .otaman:     present ({kind})")
            else:
                print(f"  .otaman:     not yet (run `otaman init --update` to write)")
    return 0


__all__ = ["cmd_project_show"]
