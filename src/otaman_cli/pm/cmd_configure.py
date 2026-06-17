"""`otaman pm configure <provider>` — write pm-sync block to platform.yaml."""
from __future__ import annotations

import re
from pathlib import Path

from otaman_cli.identity import find_project_root


def cmd_pm_configure(args: list[str]) -> int:
    """otaman pm configure <provider> [--url URL] [--webhook URL] [--no-webhooks] [--tracker NAME]"""
    from otaman_cli.main import UI

    # Parse args
    provider: str | None = None
    base_url: str | None = None
    webhook_target: str | None = None
    no_webhooks = False
    tracker = "Task"

    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--url" and i + 1 < len(args):
            base_url = args[i + 1]; i += 2
        elif tok == "--webhook" and i + 1 < len(args):
            webhook_target = args[i + 1]; i += 2
        elif tok == "--no-webhooks":
            no_webhooks = True; i += 1
        elif tok == "--tracker" and i + 1 < len(args):
            tracker = args[i + 1]; i += 2
        elif not tok.startswith("-") and provider is None:
            provider = tok; i += 1
        else:
            i += 1

    if not provider:
        UI.error("Usage: otaman pm configure <provider> --url <base-url> [--webhook <url>] [--no-webhooks] [--tracker <name>]")
        UI.muted("Supported providers: easy8")
        return 1

    if not base_url:
        UI.error("--url <base-url> is required")
        return 1

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    platform_yaml_path = root / "platform.yaml"
    text = platform_yaml_path.read_text(encoding="utf-8")

    # Determine program name and key from platform.yaml
    import yaml
    doc = yaml.safe_load(text) or {}

    # Prefer existing pm-sync values (idempotent re-configure)
    existing_pm = doc.get("pm-sync") or {}
    if isinstance(existing_pm, dict) and existing_pm.get("program-name"):
        program_name_str = str(existing_pm["program-name"])
        program_key_str = str(existing_pm.get("program-key", ""))
    else:
        # Derive from top-level project key
        raw = doc.get("project", "") or ""
        if isinstance(raw, dict):
            raw = raw.get("name", "") or ""
        program_name_str = str(raw).strip()
        # Capitalise first letter for display name if it looks like a slug
        if program_name_str and program_name_str == program_name_str.lower():
            program_name_str = program_name_str.replace("-", " ").title()
        if not program_name_str:
            program_name_str = "My Program"
        program_key_str = ""

    if not program_key_str:
        program_key_str = re.sub(r"[^a-z0-9-]", "-", program_name_str.lower()).strip("-") or "program"

    # Build pm-sync block
    webhook_line = f"  webhook-target: {webhook_target}" if webhook_target and not no_webhooks else "  # webhook-target: https://<your-bridge>/pm-sync/easy8  # set when bridge is deployed"
    pm_sync_block = (
        f"pm-sync:\n"
        f"  provider: {provider}\n"
        f"  base-url: {base_url}\n"
        f"  identity-mode: system_user\n"
        f'  program-name: "{program_name_str}"\n'
        f"  program-key: {program_key_str}\n"
        f"  per-repo: true\n"
        f"  status-map:\n"
        f"    declared: New\n"
        f"    in_progress: In Progress\n"
        f"    blocked: Feedback\n"
        f"    done: Closed\n"
        f"  tracker: {tracker}\n"
        f"  {webhook_line.strip()}\n"
        f"  # Written by otaman pm init — do not edit manually\n"
        f"  project-map: {{}}\n"
    )

    # Check if pm-sync block already exists
    if re.search(r"^pm-sync:", text, re.MULTILINE):
        UI.warn("pm-sync block already exists in platform.yaml — overwriting it")
        # Remove existing pm-sync block (everything from pm-sync: until the next top-level key)
        text = re.sub(
            r"^pm-sync:.*?(?=^[a-z]|\Z)",
            "",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )

    new_text = text.rstrip() + "\n\n" + pm_sync_block
    platform_yaml_path.write_text(new_text, encoding="utf-8")
    UI.ok(f"pm-sync block written to platform.yaml (provider={provider}, base-url={base_url})")

    # Write Easy8 MCP server to .mcp.json if supported
    if provider == "easy8":
        _write_mcp_config(root, base_url, UI)

    UI.muted("Next: set OTAMAN_PM_EASY8_API_KEY and run: otaman pm init easy8")
    return 0


def _write_mcp_config(root: Path, base_url: str, UI) -> None:
    """Add Easy8 MCP server entry to .mcp.json (create if absent)."""
    import json
    mcp_path = root / ".mcp.json"
    try:
        config = json.loads(mcp_path.read_text()) if mcp_path.exists() else {}
    except Exception:
        config = {}

    config.setdefault("mcpServers", {})
    mcp_url = base_url.rstrip("/") + "/mcp"
    config["mcpServers"]["easy8"] = {
        "type": "http",
        "url": mcp_url,
        "description": "Easy8 MCP server — Tier 2 agent operations (issue queries, bulk transitions, project summaries)",
        "headers": {"X-Redmine-API-Key": "${OTAMAN_PM_EASY8_API_KEY}"},
    }

    mcp_path.write_text(json.dumps(config, indent=2) + "\n")
    UI.ok(f"Easy8 MCP server added to .mcp.json ({mcp_url})")
    UI.muted("Set OTAMAN_PM_EASY8_API_KEY in your environment to enable Tier 2 agent operations")
