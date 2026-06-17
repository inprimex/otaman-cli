"""`otaman pm init <provider>` command — initialize PM tool sync (Easy8 / Redmine)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from otaman_core.pm_sync import load_pm_sync_config, PmSyncConfig, PmAdapterCapabilities
except ImportError:
    load_pm_sync_config = None  # type: ignore[assignment]
    PmSyncConfig = None  # type: ignore[assignment]
    PmAdapterCapabilities = None  # type: ignore[assignment]

# Module-level import so tests can patch otaman_cli.pm.cmd_init.find_project_root
from otaman_cli.identity import find_project_root


def cmd_pm_init(args: list[str]) -> int:
    """otaman pm init <provider> [--url URL] [--dry-run] [--seed-backlog] [--no-webhooks] [--admin-key KEY]"""
    from otaman_cli.main import UI, C

    # -----------------------------------------------------------------------
    # Parse args
    # -----------------------------------------------------------------------
    provider: str | None = None
    url_override: str | None = None
    dry_run: bool = False
    seed_backlog: bool = False
    no_webhooks: bool = False
    admin_key: str | None = None

    i = 0
    while i < len(args):
        token = args[i]
        if token == "--url" and i + 1 < len(args):
            url_override = args[i + 1]
            i += 2
        elif token == "--dry-run":
            dry_run = True
            i += 1
        elif token == "--seed-backlog":
            seed_backlog = True
            i += 1
        elif token == "--no-webhooks":
            no_webhooks = True
            i += 1
        elif token == "--admin-key" and i + 1 < len(args):
            admin_key = args[i + 1]
            i += 2
        elif not token.startswith("-"):
            if provider is None:
                provider = token
            i += 1
        else:
            i += 1

    if not provider:
        UI.error("Usage: otaman pm init <provider> [--url URL] [--dry-run] [--seed-backlog] [--no-webhooks] [--admin-key KEY]")
        UI.muted("Supported providers: easy8")
        return 1

    if dry_run:
        UI.action(f"[dry-run] Would initialize PM sync for provider: {provider}")

    # -----------------------------------------------------------------------
    # Step 1: Find platform.yaml + load config
    # -----------------------------------------------------------------------
    UI.action("Step 1: Read + validate config")
    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    platform_yaml_path = root / "platform.yaml"
    if not platform_yaml_path.exists():
        UI.error(f"platform.yaml not found at {platform_yaml_path}")
        return 1

    if load_pm_sync_config is None:
        UI.error("otaman-core is not installed or does not export pm_sync. Cannot load PM config.")
        return 1

    try:
        config = load_pm_sync_config(platform_yaml_path)
    except Exception as exc:
        UI.error(f"Failed to load pm-sync config from platform.yaml: {exc}")
        return 1

    if config is None:
        UI.error("No 'pm-sync' block found in platform.yaml. Add a pm-sync section first.")
        return 1

    if url_override and config is not None:
        from dataclasses import replace as dc_replace
        config = dc_replace(config, base_url=url_override)

    UI.ok(f"Config loaded -- provider: {provider}, base-url: {getattr(config, 'base_url', '(not set)')}")

    # -----------------------------------------------------------------------
    # Step 2: Validate identity-mode against adapter capabilities
    # -----------------------------------------------------------------------
    UI.action("Step 2: Validate identity-mode against adapter capabilities")
    try:
        if provider == "easy8":
            from otaman_adapters.easy8 import EASY8_CAPABILITIES  # type: ignore[import]
            identity_mode = getattr(config, "identity_mode", None)
            if identity_mode == "user" and not EASY8_CAPABILITIES.agent_identity_user:
                UI.error(f"Adapter '{provider}' does not support identity-mode 'user'.")
                return 1
            if identity_mode == "group" and not EASY8_CAPABILITIES.agent_identity_group:
                UI.error(f"Adapter '{provider}' does not support identity-mode 'group'.")
                return 1
            UI.ok(f"Identity-mode validated: {identity_mode or '(default)'}")
        else:
            UI.muted(f"Identity-mode validation not available for provider '{provider}' -- skipping")
    except ImportError:
        UI.muted("otaman-adapters not installed -- skipping identity-mode validation")
    except Exception as exc:
        UI.warn(f"Could not validate identity-mode: {exc}")

    # -----------------------------------------------------------------------
    # Step 3: Load adapter + resolve API key
    # -----------------------------------------------------------------------
    UI.action("Step 3: Load adapter + resolve API key")
    adapter = None
    if provider == "easy8":
        try:
            from otaman_adapters.easy8 import Easy8Adapter  # type: ignore[import]
        except ImportError:
            Easy8Adapter = None  # type: ignore[assignment,misc]

        if Easy8Adapter is None:
            UI.warn("otaman-adapters is not installed -- operating in dry-run / stub mode")
        elif dry_run:
            # Dry-run: no HTTP calls — skip API key requirement
            UI.muted("[dry-run] Skipping adapter instantiation (no HTTP calls in dry-run mode)")
        else:
            env_key = f"OTAMAN_PM_{provider.upper()}_API_KEY"
            api_key = (
                admin_key
                or os.environ.get(env_key)
                or os.environ.get("OTAMAN_PM_ADMIN_KEY")
            )
            if not api_key:
                UI.error(
                    f"No API key found. Set {env_key} or OTAMAN_PM_ADMIN_KEY, "
                    f"or pass --admin-key KEY."
                )
                return 1
            base_url = getattr(config, "base_url", "") or ""
            try:
                adapter = Easy8Adapter(base_url=base_url, api_key=api_key)
            except Exception as exc:
                UI.error(f"Failed to instantiate Easy8Adapter: {exc}")
                return 1
            UI.ok(f"Adapter ready: Easy8Adapter (base_url={base_url})")
    else:
        UI.warn(f"Unknown provider '{provider}' -- only 'easy8' is supported. Continuing in stub mode.")

    # -----------------------------------------------------------------------
    # Load platform.yaml raw doc for repos list (used in steps 4-7)
    # -----------------------------------------------------------------------
    try:
        import yaml
        text = platform_yaml_path.read_text(encoding="utf-8")
        doc: dict[str, Any] = yaml.safe_load(text) or {}
    except Exception as exc:
        UI.error(f"Failed to read platform.yaml: {exc}")
        return 1

    exclude_repos: list[str] = []
    if config is not None:
        exclude_repos = list(getattr(config, "exclude_repos", []) or [])

    repos: list[str] = [
        r["name"]
        for r in doc.get("repos", [])
        if isinstance(r, dict) and r.get("name") not in exclude_repos
    ]

    # Detect GitHub org from git remote if possible (fall back to hardcoded)
    gh_org = _detect_gh_org() or "inprimex"

    # -----------------------------------------------------------------------
    # Step 4: Create root project (idempotent)
    # -----------------------------------------------------------------------
    UI.action("Step 4: Create root project")
    root_project_id: str | None = None
    project_entry = doc.get("project", {})
    if isinstance(project_entry, dict):
        project_name = project_entry.get("name", "otaman")
    else:
        project_name = str(project_entry) if project_entry else "otaman"
    identifier = _to_identifier(project_name)

    if dry_run or adapter is None:
        UI.muted(f"[dry-run] Would create root project: identifier={identifier!r}, name={project_name!r}")
        root_project_id = "dry-run-root"
    else:
        try:
            root_project_id = adapter.ensure_project(identifier=identifier, name=project_name)  # type: ignore[attr-defined]
            UI.ok(f"Root project ready -- id={root_project_id}")
        except Exception as exc:
            UI.error(f"Failed to create root project: {exc}")
            return 1

    # -----------------------------------------------------------------------
    # Step 5: Create subprojects for each repo
    # -----------------------------------------------------------------------
    UI.action(f"Step 5: Create subprojects for {len(repos)} repos")
    subprojects: list[tuple[str, str]] = []

    for repo_name in repos:
        repo_identifier = _to_identifier(repo_name)
        homepage = f"https://github.com/{gh_org}/{repo_name}"
        if dry_run or adapter is None:
            UI.muted(f"[dry-run] Would create subproject: {repo_identifier!r} (parent={root_project_id}, homepage={homepage})")
            subprojects.append((repo_name, f"dry-run-{repo_identifier}"))
        else:
            try:
                proj_id = adapter.ensure_project(  # type: ignore[attr-defined]
                    identifier=repo_identifier,
                    name=repo_name,
                    parent_id=root_project_id,
                    homepage=homepage,
                )
                UI.ok(f"  Subproject ready: {repo_name} -> id={proj_id}")
                subprojects.append((repo_name, proj_id))
            except Exception as exc:
                UI.warn(f"  Could not create subproject for {repo_name}: {exc}")

    # -----------------------------------------------------------------------
    # Step 6: Create Otaman workflow statuses
    # -----------------------------------------------------------------------
    UI.action("Step 6: Create Otaman workflow statuses")
    otaman_statuses = ["Declared", "In-Progress", "Blocked", "Done"]
    if dry_run or adapter is None:
        UI.muted(f"[dry-run] Would create statuses: {', '.join(otaman_statuses)}")
    else:
        for status_name in otaman_statuses:
            try:
                adapter.ensure_issue_status(status_name)  # type: ignore[attr-defined]
                UI.ok(f"  Status: {status_name}")
            except Exception as exc:
                UI.warn(f"  Manual step required -- could not create status '{status_name}': {exc}")
                UI.muted("  Creating issue statuses requires Redmine admin rights. Add them manually via Admin > Issue Statuses.")

    # -----------------------------------------------------------------------
    # Step 7: Create issue custom fields
    # -----------------------------------------------------------------------
    UI.action("Step 7: Create issue custom fields")
    custom_fields = ["jtbd-id", "otaman-agent", "spec-path"]
    if dry_run or adapter is None:
        UI.muted(f"[dry-run] Would create custom fields: {', '.join(custom_fields)}")
    else:
        for field_name in custom_fields:
            try:
                adapter.ensure_custom_field(field_name)  # type: ignore[attr-defined]
                UI.ok(f"  Custom field: {field_name}")
            except Exception as exc:
                UI.warn(f"  Could not create custom field '{field_name}': {exc}")
                UI.muted("  Custom fields require Redmine admin rights. Add them manually via Admin > Custom Fields.")

    # -----------------------------------------------------------------------
    # Step 8: Register + activate webhooks
    # -----------------------------------------------------------------------
    if not no_webhooks:
        UI.action("Step 8: Register webhooks")
        webhook_url = getattr(config, "webhook_target", None)
        if not webhook_url:
            UI.muted("No webhook_target configured in pm-sync block -- skipping webhook registration.")
        elif dry_run or adapter is None:
            UI.muted(f"[dry-run] Would register webhook: {webhook_url}")
        else:
            try:
                adapter.ensure_webhook(webhook_url)  # type: ignore[attr-defined]
                UI.ok(f"Webhook registered: {webhook_url}")
            except Exception as exc:
                UI.warn(f"Could not register webhook: {exc}")
    else:
        UI.muted("Step 8: Webhooks skipped (--no-webhooks)")

    # -----------------------------------------------------------------------
    # Step 9: Write project-map to platform.yaml
    # -----------------------------------------------------------------------
    UI.action("Step 9: Write project-map to platform.yaml")
    if dry_run:
        UI.muted("[dry-run] Would write project-map to platform.yaml")
    else:
        try:
            import yaml
            text = platform_yaml_path.read_text(encoding="utf-8")
            doc2: dict[str, Any] = yaml.safe_load(text) or {}
            if "pm-sync" not in doc2:
                doc2["pm-sync"] = {}
            doc2["pm-sync"]["project-map"] = {
                "_root": root_project_id,
                **{repo: proj_id for repo, proj_id in subprojects},
            }
            platform_yaml_path.write_text(
                yaml.dump(doc2, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            UI.ok("project-map written to platform.yaml")
        except Exception as exc:
            UI.error(f"Failed to write project-map to platform.yaml: {exc}")
            return 1

    # -----------------------------------------------------------------------
    # Step 10: Seed backlog (placeholder)
    # -----------------------------------------------------------------------
    if seed_backlog:
        UI.action("Step 10: Seed backlog")
        UI.muted("Backlog seeding not yet implemented.")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    UI.ok(f"PM sync initialized for provider: {provider}")
    if dry_run:
        UI.muted("(dry-run -- no changes were made)")
    else:
        if os.environ.get("OTAMAN_PM_ADMIN_KEY"):
            UI.warn(
                "OTAMAN_PM_ADMIN_KEY is still set in your environment. "
                "Rotate or unset it now that initialization is complete."
            )

    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_identifier(name: str) -> str:
    """Convert a project name to a Redmine-safe identifier (lowercase, hyphens)."""
    import re
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"


def _detect_gh_org() -> str | None:
    """Try to detect the GitHub organisation from 'git remote get-url origin'."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # https://github.com/ORG/REPO.git  or  git@github.com:ORG/REPO.git
            import re
            m = re.search(r"github\.com[:/]([^/]+)/", url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None
