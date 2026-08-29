"""`otaman program <action>` — program lifecycle transitions (program-lifecycle-states 2.3).

Runtime enforcement of the org-level lifecycle registry (core step 1, D1):
state is READ only via ``otaman_core.lifecycle.read_program_state`` and WRITTEN
only via ``record_transition``. This command owns the authority tiers (D3) and
the ``lifecycle-change`` audit broadcast (D4); the per-service teardown +
folder mechanics of ``archive`` are a separate leg (this file lands the
status + limit/suspend/resume tiers; archive/unarchive orchestration follows).

Authority tiers (D3):
- ``limit`` / ``resume``: a roster ``approver`` human, plain CLI.
- ``suspend``: approver + interactive confirmation (it closes live sessions).
- ``archive`` / ``unarchive``: HUMAN-DECISION + HITL (separate change).
- Agents SHALL NOT perform transitions — an agent files an outcome-proposal.
"""

from __future__ import annotations

import os
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI

_TRANSITIONS = ("limit", "suspend", "resume", "archive", "unarchive")
_ACTIONS = ("status", *_TRANSITIONS)
# action → the target lifecycle state it records.
_TARGET_STATE = {
    "limit": "limited",
    "suspend": "suspended",
    "resume": "active",
    "unarchive": "active",
    "archive": "archived",
}


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _resolve_context():
    """(org_root, current_program) from the CE layout, or None with an error."""
    from otaman_cli.bus_target import derive_local_context

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return None
    ctx = derive_local_context(root)
    if ctx is None:
        UI.error(
            "Program lifecycle requires the declared org layout "
            "(orgs/<org>/programs/<program>/...) — could not derive org/program here."
        )
        return None
    return ctx


def _acting_human(program_root: Path) -> tuple[str | None, str | None]:
    """(resolved_approver_name, refusal) — one is always None.

    Resolves OTAMAN_HUMAN against the program roster and requires the approver
    role (the same shared grant as HITL/console). An agent session (no
    OTAMAN_HUMAN) is refused categorically; a resolved non-approver gets the
    actionable named refusal.
    """
    from otaman_cli.approver_eligibility import refusal_message, resolve_eligibility

    if not os.environ.get("OTAMAN_HUMAN", "").strip():
        if os.environ.get("OTAMAN_AGENT", "").strip():
            return None, (
                "agents cannot perform lifecycle transitions — file an "
                "outcome-proposal or ask your human to run this."
            )
        return None, (
            "no verified human identity (OTAMAN_HUMAN unset) — lifecycle "
            "transitions require a roster approver."
        )
    elig = resolve_eligibility(program_root / "platform.yaml")
    if not elig.resolved:
        return None, (
            f"{os.environ.get('OTAMAN_HUMAN', '').strip()!r} does not match any "
            "roster entry — lifecycle transitions require a roster approver."
        )
    if elig.refused:
        return None, refusal_message(elig)
    return (elig.entry_name or os.environ.get("OTAMAN_HUMAN", "").strip()), None


def _cmd_status(org_root: Path, program: str, *, as_json: bool) -> int:
    from otaman_core.lifecycle import lifecycle_registry_path, load_lifecycle

    registry = load_lifecycle(lifecycle_registry_path(org_root))
    entry = registry.get(program)
    state = getattr(entry, "state", None) or "active"
    if as_json:
        import json

        payload = {"program": program, "state": state}
        if entry is not None:
            for f in ("since", "by", "reason"):
                v = getattr(entry, f, None)
                if v:
                    payload[f] = v
        print(json.dumps(payload))
        return 0
    UI.header(f"Program: {program}")
    UI.kv("State", state)
    if entry is not None:
        for label, f in (("Since", "since"), ("By", "by"), ("Reason", "reason")):
            v = getattr(entry, f, None)
            if v:
                UI.kv(label, str(v))
    return 0


def _broadcast_transition(
    program_root: Path, program: str, from_state: str, to_state: str, by: str, reason: str
) -> None:
    """Write the D4 `lifecycle-change` audit broadcast to the bus (best-effort).

    Written directly (sender ``human``, actor in the body) like the other
    human-initiated broadcasts (emergency-halt, spec-change-approved) — a human
    runs the transition, so there's no agent sender for `otaman send` to
    resolve. The transition is already recorded; a failed broadcast never
    undoes it (the registry + git are the durable audit trails).
    """
    from datetime import datetime, timezone

    from otaman_cli.main import _resolve_bus_paths

    try:
        active_dir, _acks = _resolve_bus_paths(program_root)
        now = datetime.now(timezone.utc)
        ts, iso = now.strftime("%Y%m%dT%H%M%S"), now.isoformat()
        msg = (
            f"---\nfrom: human\nto: all\ntype: lifecycle-change\npriority: normal\n"
            f"timestamp: {iso}\nstatus: pending\n---\n\n"
            f"## Subject: lifecycle-change: {program} {from_state}→{to_state}\n\n"
            f"Program `{program}` lifecycle: {from_state} → {to_state}\n\n"
            f"- actor: {by}\n- reason: {reason or '(none)'}\n"
        )
        (active_dir / f"{ts}-human-to-all-lifecycle-change-{program}-{to_state}.md").write_text(
            msg, encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - transition already recorded; broadcast is best-effort
        UI.warn("lifecycle-change broadcast could not be written (transition already recorded).")


def _do_transition(action: str, program: str, *, reason: str, dry_run: bool) -> int:
    from otaman_core.lifecycle import (
        lifecycle_registry_path,
        read_program_state,
        record_transition,
    )

    ctx = _resolve_context()
    if ctx is None:
        return 1
    org_root, current_program = ctx.org_root, ctx.program
    program = program or current_program

    if action in ("archive", "unarchive"):
        # HUMAN-DECISION tier + teardown orchestration land in the follow-up.
        return _bail(
            f"`otaman program {action}` is not available yet — the HITL-confirmed "
            "teardown orchestration lands in a follow-up (program-lifecycle-states 2.3, part 2).",
            code=2,
        )

    target = _TARGET_STATE[action]
    from_state = read_program_state(org_root, program)

    if from_state == target:
        UI.info(f"{program} is already {target} — nothing to do.")
        return 0
    if from_state == "archived":
        return _bail(f"{program} is archived — run `otaman program unarchive` first.")

    # Authority (D3): approver for limit/resume; approver + confirm for suspend.
    by, refusal = _acting_human(ctx.program_root)
    if refusal is not None:
        return _bail(f"Refused — {refusal}")

    # A dry-run previews the plan without mutating — and without the
    # interactive confirmation the real suspend requires below.
    if dry_run:
        UI.info(f"[dry-run] would record {program}: {from_state} → {target} (by {by})")
        UI.muted("           and broadcast a lifecycle-change; no state written.")
        return 0

    if action == "suspend":
        from otaman_cli.safety import require_interactive_tty

        if not require_interactive_tty(
            f"About to SUSPEND `{program}` ({from_state} → suspended) — closes its live sessions."
        ):
            return _bail("Refusing to suspend without an interactive terminal.")

    record_transition(
        lifecycle_registry_path(org_root), program, target, by=by, reason=reason or None
    )
    UI.ok(f"{program}: {from_state} → {target} (by {by})")
    _broadcast_transition(ctx.program_root, program, from_state, target, by, reason)
    return 0


def cmd_program(args: list[str]) -> int:
    """`otaman program <action> [program] [--reason R] [--dry-run] [--json]`.

    Actions: status | limit | suspend | resume | archive | unarchive.
    """
    if not args or args[0] in ("-h", "--help"):
        UI.muted("Usage: otaman program <action> [program] [--reason R] [--dry-run] [--json]")
        UI.muted(f"Actions: {', '.join(_ACTIONS)}")
        return 0 if args else 1
    action = args[0]
    if action not in _ACTIONS:
        return _bail(f"Unknown action {action!r}. Actions: {', '.join(_ACTIONS)}")

    rest = args[1:]
    program: str | None = None
    reason = ""
    dry_run = False
    as_json = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--reason" and i + 1 < len(rest):
            reason = rest[i + 1]
            i += 2
        elif a == "--dry-run":
            dry_run = True
            i += 1
        elif a == "--json":
            as_json = True
            i += 1
        elif not a.startswith("-") and program is None:
            program = a
            i += 1
        else:
            return _bail(f"Unexpected argument: {a}")

    if action == "status":
        ctx = _resolve_context()
        if ctx is None:
            return 1
        return _cmd_status(ctx.org_root, program or ctx.program, as_json=as_json)
    return _do_transition(action, program, reason=reason, dry_run=dry_run)


register(
    CommandSpec(
        name="program",
        handler=cmd_program,
        help="Program lifecycle: status | limit | suspend | resume | archive | unarchive",
    )
)

__all__ = ["cmd_program"]
