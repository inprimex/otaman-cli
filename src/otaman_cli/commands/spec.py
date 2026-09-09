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
from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.main import UI

_ACTIONS = ("status", "gate")
_GATES = ("dispatch", "archive", "merge")


def cmd_spec(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help"):
        UI.error("Usage: otaman spec <status|gate> [options]")
        UI.muted("  status [--json]                       — the lifecycle surface (D3)")
        UI.muted("  gate <change> [--at dispatch|archive|merge] [--no-delta] [--json]")
        return 0 if args and args[0] in ("-h", "--help") else 1
    action, *rest = args
    if action not in _ACTIONS:
        UI.error(f"Unknown spec action: {action}")
        UI.muted("Actions: " + " | ".join(_ACTIONS))
        return 2
    root = find_project_root()
    if root is None:
        UI.error(not_in_project_message())
        return 1
    if action == "gate":
        return _cmd_gate(root, rest)
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
        badge = "  [auto-delivery]" if getattr(r, "delivery", None) == "auto" else ""
        UI.bullet(f"{r.change}{_SEV_MARK.get(r.severity, '')}{badge}")
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


# ---------------------------------------------------------------------------
# gate — local (CI-less) lifecycle-gate runner + the shared dispatch check (2.2)


def _change_dir(root: Path, name: str) -> Path | None:
    changes = _specs_changes_dir(root)
    if changes is None:
        return None
    d = (changes / name).resolve()
    return d if d.is_dir() else None


def _run_gate(data: dict, policy, at: str, *, has_capability_delta: bool = True):
    """Run the requested lifecycle gate against a change's .openspec.yaml dict."""
    from otaman_core.spec_lifecycle import (
        check_archive_gate,
        check_dispatch_gate,
        check_merge_gate,
    )

    if at == "dispatch":
        return check_dispatch_gate(data, policy)
    if at == "archive":
        return check_archive_gate(data, policy, has_capability_delta=has_capability_delta)
    return check_merge_gate(data, policy, has_capability_delta=has_capability_delta)


def _gate_notice_lines(decision) -> list[str]:
    """Human lines for a GateDecision — self-waive/warn notices + violations."""
    lines: list[str] = []
    for n in decision.notices:  # self-waive: ... / proceeding despite: ...
        lines.append(n)
    if not decision.allowed:
        for v in decision.violations:
            lines.append(f"blocked: {v}")
    return lines


def dispatch_gate_check(root: Path, change_name: str) -> tuple[bool, list[str]]:
    """The dispatch-time gate for `otaman assign` (2.2). Returns (allowed, lines).

    A missing change / missing .openspec.yaml / unavailable core → (True, []) so
    dispatch is never blocked by absence — only an explicit block-mode policy on a
    tracked change refuses. The self-waive / warn notices are returned for display.
    """
    try:
        from otaman_core.spec_lifecycle import read_openspec
    except Exception:  # noqa: BLE001 - core unavailable → do not gate
        return True, []
    d = _change_dir(root, change_name)
    if d is None:
        return True, []
    data = read_openspec(d / ".openspec.yaml")
    if not data:
        return True, []  # legacy/unbackfilled change → not gated until it has a stage
    decision = _run_gate(data, _load_policy(root), "dispatch")
    return decision.allowed, _gate_notice_lines(decision)


def _cmd_gate(root: Path, rest: list[str]) -> int:
    from otaman_core.spec_lifecycle import read_openspec

    pos = [a for a in rest if not a.startswith("--")]
    at = "dispatch"
    if "--at" in rest:
        i = rest.index("--at")
        if i + 1 < len(rest):
            at = rest[i + 1]
    if at not in _GATES:
        UI.error(f"--at must be one of: {', '.join(_GATES)}")
        return 2
    if not pos:
        UI.error("Usage: otaman spec gate <change> [--at dispatch|archive|merge] [--no-delta]")
        return 1
    name = pos[0]
    d = _change_dir(root, name)
    if d is None:
        UI.error(f"No change named {name!r} under the specs repo")
        return 1
    data = read_openspec(d / ".openspec.yaml")
    decision = _run_gate(
        data, _load_policy(root), at, has_capability_delta="--no-delta" not in rest
    )

    if "--json" in rest:
        import json

        print(
            json.dumps(
                {
                    "gate": decision.gate,
                    "allowed": decision.allowed,
                    "mode": decision.mode,
                    "waived": decision.waived,
                    "violations": list(decision.violations),
                    "notices": list(decision.notices),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if decision.allowed else 1

    verdict = "ALLOWED" if decision.allowed else "BLOCKED"
    UI.header(f"spec gate: {at} — {name}")
    UI.kv("stage", str(data.get("stage") or "—"))
    UI.kv("mode", decision.mode + ("  (waived)" if decision.waived else ""))
    UI.kv("result", verdict)
    for line in _gate_notice_lines(decision):
        UI.muted(f"  {line}")
    return 0 if decision.allowed else 1


# ---------------------------------------------------------------------------
# ratify — human-only, mandatory reason, HUMAN-DECISION tier (D4)


def cmd_ratify(args: list[str]) -> int:
    """`otaman ratify <change> --reason "<why>"` — human-only approval (D4).

    HUMAN-DECISION tier (no --yes bypass): mints the ratified approval, sets the
    change to stage ``approved`` with a ``ratified: true`` marker + ``ratified_at``
    (the timestamp the doctor month-count reads), preserving other .openspec keys.
    """
    import os

    if not args or args[0] in ("-h", "--help"):
        UI.error('Usage: otaman ratify <change> --reason "<why this bypasses normal approval>"')
        return 0 if args and args[0] in ("-h", "--help") else 1
    pos = [a for a in args if not a.startswith("--")]
    reason = ""
    for flag in ("--reason", "-d"):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                reason = args[i + 1]
    if not pos:
        UI.error("ratify requires a change name")
        return 1
    name = pos[0]
    if not reason.strip():
        UI.error('ratify requires a reason: --reason "<why>"')
        return 1

    by = os.environ.get("OTAMAN_HUMAN", "").strip()
    if not by:
        UI.error("ratify is human-only: set OTAMAN_HUMAN to the ratifying human's identity")
        return 2

    root = find_project_root()
    if root is None:
        UI.error(not_in_project_message())
        return 1
    d = _change_dir(root, name)
    if d is None:
        UI.error(f"No change named {name!r} under the specs repo")
        return 1

    from otaman_core.spec_lifecycle import (
        SpecLifecycleError,
        apply_ratification,
        ratify,
        read_openspec,
    )

    from otaman_cli import safety

    # HUMAN-DECISION: ratification bypasses the normal HITL approval, so it must
    # be a genuine human at a TTY — no agent-session bypass (D4).
    if not safety.confirm_human_decision(
        f"Ratify change '{name}' as {by} (bypasses spec-approved HITL): {reason}"
    ):
        UI.error("Ratification aborted — not confirmed by a human.")
        return 2

    from datetime import datetime, timezone

    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        record = ratify(name, by=by, reason=reason, at=at)
    except SpecLifecycleError as exc:
        UI.error(str(exc))
        return 1

    data = read_openspec(d / ".openspec.yaml")
    updated = apply_ratification(data, record)
    updated["ratified_at"] = at  # the month-count marker doctor/status read
    _write_openspec(d / ".openspec.yaml", updated)

    UI.ok(f"Ratified {name!r} → stage=approved (ratified)")
    UI.kv("by", by)
    UI.kv("reason", reason)
    UI.kv("at", at)
    UI.muted("A rising ratification count is a process-health alarm (otaman spec status / doctor).")
    return 0


def _write_openspec(path: Path, data: dict) -> None:
    """Persist a change's .openspec.yaml (machine-owned key/value metadata), atomic."""
    import os
    import tempfile

    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


register(
    CommandSpec(
        name="spec",
        handler=cmd_spec,
        help="Spec lifecycle: status (surface) + gate (local dispatch/archive/merge check)",
    )
)
register(
    CommandSpec(
        name="ratify",
        handler=cmd_ratify,
        help="Human-only ratified approval of a change (HUMAN-DECISION, mandatory reason)",
    )
)
