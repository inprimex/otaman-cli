"""`otaman project list [--status active|inactive|all]` (task 5.1)."""

from __future__ import annotations

from typing import Any

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import load_platform_yaml


def _normalised_status(entry: dict[str, Any]) -> str:
    """Map the schema-accepted `disabled: bool` to a user-facing status string.

    `disabled: true` → 'inactive'.  Absent or `disabled: false` → 'active'.
    The CLI surface keeps the `--status active|inactive|all` terminology
    (more readable than `--disabled`); the on-disk representation uses
    `disabled:` because that's what platform-schema.yaml accepts.
    """
    return "inactive" if entry.get("disabled") else "active"


def cmd_project_list(status: str = "active") -> int:
    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project")
        return 1
    try:
        data = load_platform_yaml(root)
    except FileNotFoundError as exc:
        UI.error(str(exc))
        return 2

    repos = [r for r in (data.get("repos") or []) if isinstance(r, dict)]
    filter_s = str(status).lower()
    if filter_s not in ("active", "inactive", "all"):
        UI.error(f"Invalid --status: {status!r}. Use active | inactive | all.")
        return 1
    if filter_s != "all":
        repos = [r for r in repos if _normalised_status(r) == filter_s]

    if not repos:
        print("No repos registered" + (f" (status={filter_s})" if filter_s != "all" else ""))
        return 0

    UI.header(f"Repos ({len(repos)}, filter: {filter_s})")
    print(f"  {'NAME':<28}  {'OWNER':<22}  {'STATUS':<10}  PATH")
    for r in repos:
        name = r.get("name", "?")
        owner = r.get("owner", "?")
        st = _normalised_status(r)
        path_rel = r.get("path", "")
        # Surface [missing] when local dir doesn't exist
        marker = ""
        if path_rel:
            abs_path = (root / path_rel).expanduser()
            if not abs_path.exists():
                marker = "  [missing]"
        print(f"  {name:<28}  {owner:<22}  {st:<10}  {path_rel}{marker}")
    return 0


__all__ = ["cmd_project_list"]
