"""platform.yaml generation using ruamel.yaml (tasks.md 2.1, 5.2).

Two modes:
    CREATE  — Generate a fresh platform.yaml from the answers dict.
    UPDATE  — Round-trip edit an existing platform.yaml, preserving comments
              and formatting, merging only the keys that changed.

Design rules:
    - Use ruamel.yaml for all writes (preserves block scalars, comments,
      ordering).  PyYAML is fine for fast reads elsewhere, but never for
      writes that might clobber an existing file.
    - The generated schema conforms to all approved proposals (the full
      conformance layer is bridge-agent's task 5.1; we emit a correct but
      minimal set here for the spike).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ruamel.yaml may not be installed in the test environment yet; guard import
try:
    from ruamel.yaml import YAML as _YAML

    _RUAMEL_AVAILABLE = True
except ImportError:
    _RUAMEL_AVAILABLE = False


def _make_ruamel() -> Any:
    if not _RUAMEL_AVAILABLE:
        raise ImportError(
            "ruamel.yaml is required for platform.yaml generation.  "
            "Install it: pip install 'ruamel.yaml>=0.18'"
        )
    yaml = _YAML()
    yaml.default_flow_style = False
    yaml.preserve_quotes = True
    # width=120 folded long launch 'cmd || cmd' scalars on every --update
    # re-dump, re-dirtying the owner-managed otaman-meta checkout with no
    # semantic change (deploy-agent bug report 20260824T125215). Mirror the
    # ce-bootstrap platform.yaml patcher (width=4096, non-drifting): keep
    # long command scalars on one line so round-trips are byte-stable.
    yaml.width = 4096
    return yaml


# --------------------------------------------------------------------------- builders


def _build_platform_yaml(answers: dict[str, Any]) -> dict[str, Any]:
    """Convert the answers dict into a conforming platform.yaml structure.

    This is intentionally a *spike* — it covers the fields the interactive
    flow collects.  Bridge-agent task 5.1 adds the full schema conformance.
    """
    program_name: str = answers.get("program_name", "my-program")
    description: str = answers.get("description", "")
    mode: int = answers.get("mode", 1)
    edition: str = answers.get("active_edition", "ce")
    domains: list[str] = answers.get("domains", [])
    roles: list[str] = answers.get("roles", [])
    role_assignments: dict[str, str] = answers.get("role_assignments", {})
    processes: list[str] = answers.get("processes", [])
    currency_code: str = answers.get("currency_code", "USD")
    currency_symbol: str = answers.get("currency_symbol", "$")
    currency_decimals: int = int(answers.get("currency_decimals", 2))
    probability_scale: str = answers.get("probability_scale", "t-shirt")
    impact_scale: str = answers.get("impact_scale", "t-shirt")
    releases: list[str] = answers.get("releases", ["MVP"])
    skill_profile: str = answers.get("skill_profile", "software-development-default")
    extra_skills: list[str] = answers.get("extra_skills", [])
    primary_repo: str = answers.get("primary_repo", f"~/{program_name}/{program_name}-specs")
    git_platform: str = answers.get("git_platform", "local")
    companion_business: bool = answers.get("scaffold_business", False)
    companion_strategy: bool = answers.get("scaffold_strategy", False)

    doc: dict[str, Any] = {
        "project": program_name,
        "description": description,
        "version": "1.0",
        "edition": edition,
        "mode": mode,
    }

    if domains:
        doc["domains"] = domains

    doc["roles"] = roles
    if role_assignments:
        doc["role_assignments"] = role_assignments

    # Schema-correct nesting: processes live under `program:` per
    # outcome-and-solution-registries/design.md Appendix D, NOT at the top
    # level (platform-schema.yaml rejects unknown top-level keys).  Empty
    # list → omit the block entirely.
    if processes:
        program_block = doc.setdefault("program", {})
        if not isinstance(program_block, dict):
            program_block = {}
            doc["program"] = program_block
        # Each process gets the minimal {enabled: true} shape; the
        # registry-specific sub-config (path, triage, etc.) is filled in
        # later via `otaman init --update` or hand-edit per design.md.
        program_block["processes"] = {p: {"enabled": True} for p in processes}

    doc["currency"] = {
        "code": currency_code,
        "symbol": currency_symbol,
        "decimal_places": currency_decimals,
    }

    doc["triage"] = {
        "probability_scale": probability_scale,
        "impact_scale": impact_scale,
    }

    doc["releases"] = releases

    doc["skills"] = {
        "profile": skill_profile,
        "extra": extra_skills,
    }

    # Single-repo case (cwd is itself a git repo): wizard's primary_repo == ".";
    # use a generic main-agent owner and program-name (not -specs suffix).
    if primary_repo == ".":
        doc["repos"] = [{"name": program_name, "path": ".", "owner": "main-agent"}]
    else:
        doc["repos"] = [
            {"name": f"{program_name}-specs", "path": primary_repo, "owner": "spec-agent"}
        ]

    if companion_business:
        doc["repos"].append(
            {
                "name": f"{program_name}-business",
                "path": f"~/{program_name}/{program_name}-business",
                "owner": "cpo-agent",
            }
        )
    if companion_strategy:
        doc["repos"].append(
            {
                "name": f"{program_name}-strategy",
                "path": f"~/{program_name}/{program_name}-strategy",
                "owner": "cofounder-agent",
            }
        )

    if mode >= 2:
        doc["git_platform"] = git_platform

    # EE extras placeholder — empty for CE
    if edition == "ee":
        doc["ee"] = {
            "multi_tenant": answers.get("multi_tenant", False),
            "organisation": answers.get("organisation_name", ""),
        }

    # program-init-claude-profile: write live now that otaman-core PR #13
    # has landed (platform-schema.yaml accepts top-level `program:` since
    # commit 598a947 / 2026-06-03). Empty answer → field omitted.
    claude_config_dir = (answers.get("claude_config_dir") or "").strip()
    if claude_config_dir:
        program_block = doc.setdefault("program", {})
        if not isinstance(program_block, dict):
            program_block = {}
            doc["program"] = program_block
        program_block.setdefault("claude", {})["config_dir"] = claude_config_dir

    # pm-sync block — only written when the user opted in during the wizard.
    # Webhook target is set to the explicit URL when the user has a public bridge;
    # otherwise the key is omitted so the bridge falls back to its own address.
    if answers.get("pm_sync_enabled", False):
        pm_sync_block: dict[str, Any] = {
            "provider": answers.get("pm_sync_provider", "easy8"),
            "base-url": answers.get("pm_sync_url", ""),
            "tracker": answers.get("pm_sync_tracker", "Task"),
        }
        webhook_mode = answers.get("pm_sync_webhook_mode", "")
        if webhook_mode == "I have a public bridge URL (configure webhooks now)":
            pm_sync_block["webhook-target"] = answers.get("pm_sync_webhook_url", "")
        doc["pm-sync"] = pm_sync_block

    # human-roster (task 5.1) — only emitted when the wizard collected entries.
    # Each entry: name, email (optional), roles (list, non-empty),
    # pm-user-id (resolved later by `otaman pm init --roster`).
    roster = answers.get("human_roster")
    if isinstance(roster, list) and roster:
        doc["human-roster"] = roster

    # bus-cc-routing task 2.5 — default routing rules.  spec-agent always
    # CC'd on messages to human; cpo-agent additionally CC'd on high/urgent
    # only when a cpo-agent repo is configured.
    routing_rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
    has_cpo = any(
        isinstance(r, dict) and r.get("owner") == "cpo-agent" for r in doc.get("repos") or []
    )
    if has_cpo:
        routing_rules.append(
            {
                "when": {"to": "human", "priority": ["high", "urgent"]},
                "cc": ["cpo-agent"],
            }
        )
    doc["bus"] = {"routing_rules": routing_rules}

    # git-flow-branch-config task 2.2 — scaffold a default standards.git
    # block instead of leaving the section absent entirely. trunk-based is
    # the simplest correct default (every merge to main ships); no
    # `environments` block, since that requires the project to actually
    # know its branch/environment mapping, which a fresh wizard run
    # doesn't have.
    doc["standards"] = {"git": {"branching": "trunk-based"}}

    return doc


# --------------------------------------------------------------------------- public API


def write_platform_yaml(answers: dict[str, Any], output_path: Path) -> Path:
    """CREATE mode — write a fresh platform.yaml at *output_path*.

    Returns the path written.
    """
    yaml = _make_ruamel()
    import io

    from ruamel.yaml.comments import CommentedMap  # type: ignore[import-untyped]

    data = _build_platform_yaml(answers)
    # Wrap in CommentedMap to allow header comment
    root = CommentedMap(data)
    root.yaml_set_start_comment(
        f"Otaman Platform Configuration — {answers.get('program_name', 'my-program')}\n"
        f"Generated by `otaman onboard program-init`\n"
        f"Edit with care; round-trip safe via ruamel.yaml\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    yaml.dump(root, buf)
    output_path.write_text(buf.getvalue(), encoding="utf-8")
    return output_path


def update_platform_yaml(answers: dict[str, Any], existing_path: Path) -> Path:
    """UPDATE mode — merge *answers* into *existing_path* preserving formatting.

    Only keys present in the new answers override existing values; unknown
    keys in the existing file are left untouched (safe round-trip).

    Returns the path written.
    """
    yaml = _make_ruamel()
    import io

    # Load existing (preserves comments via ruamel.yaml)
    existing_text = existing_path.read_text(encoding="utf-8")
    buf = io.StringIO(existing_text)
    existing_doc = yaml.load(buf)

    # Build the fresh doc from answers, then merge top-level keys
    fresh = _build_platform_yaml(answers)
    if existing_doc is None:
        existing_doc = {}

    _deep_merge(existing_doc, fresh)

    out_buf = io.StringIO()
    yaml.dump(existing_doc, out_buf)
    existing_path.write_text(out_buf.getvalue(), encoding="utf-8")
    return existing_path


def _deep_merge(base: Any, overlay: Any) -> None:
    """Recursively merge *overlay* into *base* (in-place, dict-safe)."""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
