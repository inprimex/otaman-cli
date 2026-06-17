"""`otaman pm status` command -- show per-repo PM sync state."""

from __future__ import annotations

from typing import Any

try:
    from otaman_core.pm_sync import load_pm_sync_config, PmSyncConfig
except ImportError:
    load_pm_sync_config = None  # type: ignore[assignment]
    PmSyncConfig = None  # type: ignore[assignment]

# Module-level import so tests can patch otaman_cli.pm.cmd_status.find_project_root
from otaman_cli.identity import find_project_root


def cmd_pm_status(args: list[str]) -> int:
    """otaman pm status -- show per-repo PM sync state."""
    from otaman_cli.main import UI

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    platform_yaml_path = root / "platform.yaml"
    if not platform_yaml_path.exists():
        UI.error(f"platform.yaml not found at {platform_yaml_path}")
        return 1

    # Read pm-sync block directly (don't require otaman-core for status display)
    try:
        import yaml
        text = platform_yaml_path.read_text(encoding="utf-8")
        doc: dict[str, Any] = yaml.safe_load(text) or {}
    except Exception as exc:
        UI.error(f"Failed to read platform.yaml: {exc}")
        return 1

    pm_sync = doc.get("pm-sync") or {}
    project_map: dict[str, Any] = pm_sync.get("project-map") or {}

    if not project_map:
        UI.info("PM sync not yet initialized -- run 'otaman pm init easy8'")
        return 0

    # Try to load adapter for live issue counts (best-effort)
    adapter = None
    provider = pm_sync.get("provider", "")
    if provider == "easy8":
        try:
            from otaman_adapters.easy8 import Easy8Adapter  # type: ignore[import]
            import os
            api_key = (
                os.environ.get(f"OTAMAN_PM_{provider.upper()}_API_KEY")
                or os.environ.get("OTAMAN_PM_ADMIN_KEY")
            )
            base_url = pm_sync.get("base-url") or pm_sync.get("base_url") or ""
            if api_key and base_url:
                adapter = Easy8Adapter(base_url=base_url, api_key=api_key)
        except Exception:
            pass  # operate without live counts

    # Build table rows
    headers = ["Repo", "PM Project ID", "Open Issues"]
    rows: list[list[str]] = []

    for repo, proj_id in project_map.items():
        if repo == "_root":
            continue
        open_count = "-"
        if adapter is not None:
            try:
                from otaman_core.pm_sync import PmIssueFilters  # type: ignore[import]
                issues = adapter.list_issues(PmIssueFilters(project_id=proj_id, status="open"))  # type: ignore[attr-defined]
                open_count = str(len(issues))
            except Exception:
                open_count = "?"
        rows.append([repo, str(proj_id), open_count])

    # Show root project separately
    root_id = project_map.get("_root", "(not set)")
    UI.kv("Root project ID", str(root_id))
    UI.kv("Provider", provider or "(not set)")
    print()

    if rows:
        UI.table(headers, rows)
    else:
        UI.muted("No subprojects in project-map.")

    return 0
