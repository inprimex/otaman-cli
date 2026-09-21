"""`otaman emergency-halt` — F012 security fix (2026-07-04).

Broadcasts a PRIVILEGED `emergency-halt` bus message (`to: all`, asserts
`from: human`), gated on a real interactive confirmation
(`confirm_human_decision` — no --yes/scripted bypass). Previously this
type had no dedicated producer at all; the only way to send it was the
general `otaman send` path, which let ANY caller claim `from: human` with
no check.
"""

from __future__ import annotations

from datetime import datetime, timezone

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.main import UI, _resolve_bus_paths
from otaman_cli.safety import confirm_human_decision


def cmd_emergency_halt(args: list[str]) -> int:
    """Broadcast an emergency-halt to every agent.

    Usage:
      otaman emergency-halt --reason "<why>"
    """
    reason = ""
    i = 0
    while i < len(args):
        if args[i] == "--reason" and i + 1 < len(args):
            reason = args[i + 1]
            i += 2
        else:
            UI.error(f"Unknown argument: {args[i]!r}")
            UI.muted('Usage: otaman emergency-halt --reason "<why>"')
            return 2

    if not reason.strip():
        UI.error("--reason is required")
        UI.muted('Usage: otaman emergency-halt --reason "<why>"')
        return 2

    root = find_project_root()
    if not root:
        UI.error(not_in_project_message())
        return 1

    active_dir, _acks_dir = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)

    if not confirm_human_decision(
        f"About to broadcast an EMERGENCY HALT to every agent.\nReason: {reason}",
    ):
        UI.error("Emergency halt cancelled — not confirmed.")
        return 1

    now = datetime.now(timezone.utc)
    now_ts = now.strftime("%Y%m%dT%H%M%S")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    from otaman_cli.bus_stem_gate import bus_stem

    slug = bus_stem().slugify(reason, max_len=30)

    msg = f"""---
id: {now_ts}-emergency-halt-{slug}
from: human
to: all
priority: urgent
type: emergency-halt
timestamp: {now_iso}
status: pending
---

## Subject: EMERGENCY HALT

**All agents must stop current work immediately.**

**Reason**: {reason}

Do not start new tasks, do not commit, do not push. Await further
instructions from a human before resuming.
"""
    # bus-test-isolation 2.1 — no ledger record, no bus file. The 2026-08-16
    # forged-halt incident is exactly a halt file with no matching record.
    from otaman_cli.safety import record_privileged_confirmation

    if not record_privileged_confirmation(
        message_id=f"{now_ts}-emergency-halt-{slug}",
        content=msg,
        command="emergency-halt",
    ):
        return 1

    from otaman_cli.bus_write import write_message_exclusive

    # Two halts in one second is unlikely, but a halt broadcast that silently
    # replaces another one is not a failure mode worth keeping for one line.
    msg_file = write_message_exclusive(
        active_dir
        / bus_stem().build_filename(
            timestamp=now_ts, sender="human", recipient="all", slug="emergency-halt"
        ),
        msg,
    )

    UI.header("EMERGENCY HALT BROADCAST")
    UI.ok(f"Broadcast sent: {msg_file.relative_to(root)}")
    UI.kv("Reason", reason)
    return 0


register(
    CommandSpec(
        name="emergency-halt",
        handler=cmd_emergency_halt,
        help="Broadcast an emergency halt to every agent (requires interactive confirmation)",
    )
)
