"""`otaman spec status` — the truthful spec-lifecycle surface (SLE 2.1 / D3).

Per change: stage (from ``.openspec.yaml`` — repo is truth, D1), catchable state,
days-in-state, owner, next actor, and a severity bucket (WARN day 1 / ERROR day 3
for the stalled states). Archive-aware, and sharing ONE derivation with the
console lifecycle view (:mod:`otaman_cli.lifecycle`). Also renders the effective
spec policy and the month's ratification count (a rising count is a process-health
alarm — D4). Values-free; no runner, no CI, no git-host dependency.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI

_ACTIONS = ("status",)


def cmd_spec(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help"):
        UI.error("Usage: otaman spec status [--json]")
        UI.muted("Actions: " + " | ".join(_ACTIONS))
        return 0 if args and args[0] in ("-h", "--help") else 1
    action, *rest = args
    if action != "status":
        UI.error(f"Unknown spec action: {action}")
        UI.muted("Actions: " + " | ".join(_ACTIONS))
        return 2
    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return 1
    return _cmd_status(root, rest)


def _specs_changes_dir(root: Path) -> Path | None:
    import yaml

    try:
        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    specs = cfg.get("specs") if isinstance(cfg, dict) else None
    path = specs.get("path") if isinstance(specs, dict) else None
    if not path:
        return None
    changes = (root / path / "openspec" / "changes").resolve()
    return changes if changes.is_dir() else None


def _load_policy(root: Path):
    """Effective SpecPolicy from platform.yaml's ``spec_policy`` (program) block."""
    import yaml
    from otaman_core.spec_lifecycle import resolve_spec_policy

    try:
        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    program_block = cfg.get("spec_policy") if isinstance(cfg, dict) else None
    return resolve_spec_policy(None, program_block)


def _collect_ratifications(changes_dir: Path | None):
    """Ratification records reconstructed from change ``.openspec.yaml`` markers.

    ``apply_ratification`` marks ``ratified: true`` + ``approved_by``; the cli
    ratify verb (2.2) also stamps ``ratified_at`` so this count is meaningful.
    Absent ``ratified_at`` → excluded (its month is unknown).
    """
    from otaman_core.spec_lifecycle import Ratification, read_openspec

    records = []
    if not changes_dir:
        return records
    roots = [changes_dir]
    archive = changes_dir / "archive"
    if archive.is_dir():
        roots.append(archive)
    for base in roots:
        for d in base.iterdir():
            if not d.is_dir() or d.name == "archive":
                continue
            data = read_openspec(d / ".openspec.yaml")
            if data.get("ratified") and isinstance(data.get("ratified_at"), str):
                records.append(
                    Ratification(
                        change=d.name,
                        by=str(data.get("approved_by", "")),
                        reason="",
                        at=data["ratified_at"],
                        ratified=True,
                    )
                )
    return records


_SEV_MARK = {"ok": "", "warn": "  [WARN]", "error": "  [ERROR]"}


def _cmd_status(root: Path, rest: list[str]) -> int:
    from datetime import datetime, timezone

    from otaman_core.spec_lifecycle import ratifications_in_month

    from otaman_cli.lifecycle import derive_lifecycle

    changes_dir = _specs_changes_dir(root)
    active_bus, _ = _bus_active(root)
    now = datetime.now(timezone.utc)
    rows = derive_lifecycle(changes_dir=changes_dir, bus_active_dir=active_bus, now=now)
    policy = _load_policy(root)
    ratif_count = ratifications_in_month(
        _collect_ratifications(changes_dir), year=now.year, month=now.month
    )

    if "--json" in rest:
        import json

        payload = {
            "policy": {
                "process_level": policy.process_level,
                "enforcement": policy.enforcement,
            },
            "ratifications_this_month": ratif_count,
            "changes": [
                {
                    "change": r.change,
                    "stage": r.stage,
                    "state": r.state,
                    "age": r.age,
                    "age_days": r.age_days,
                    "owner": r.owner,
                    "next_actor": r.next_actor,
                    "severity": r.severity,
                }
                for r in rows
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if any(r.severity == "error" for r in rows) else 0

    UI.header("Spec lifecycle")
    UI.kv("policy", f"level={policy.process_level}  enforcement={policy.enforcement}")
    UI.kv("ratifications this month", str(ratif_count))
    if not rows:
        UI.muted("No changes in flight, awaiting authoring, or awaiting archive — all clear.")
        return 0

    for r in rows:
        stage = f"stage={r.stage} " if r.stage else ""
        UI.bullet(f"{r.change}{_SEV_MARK.get(r.severity, '')}")
        UI.kv("  state", f"{stage}{r.state} ({r.age} in state)")
        UI.kv("  next", r.next_actor)
    n_err = sum(1 for r in rows if r.severity == "error")
    n_warn = sum(1 for r in rows if r.severity == "warn")
    if n_err or n_warn:
        UI.muted(f"({n_err} ERROR, {n_warn} WARN — stalled ≥3d / ≥1d)")
    return 1 if n_err else 0


def _bus_active(root: Path):
    from otaman_cli.main import _resolve_bus_paths

    active, acks = _resolve_bus_paths(root)
    return (active if active.is_dir() else None), acks


register(
    CommandSpec(
        name="spec",
        handler=cmd_spec,
        help="Spec lifecycle: status (stage, stalled buckets, ratifications)",
    )
)
