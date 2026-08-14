"""`otaman emergency-halt` — F012 security fix (2026-07-04).

Broadcasts a PRIVILEGED `emergency-halt` bus message (`to: all`, asserts
`from: human`), gated on a real interactive confirmation
(`confirm_human_decision` — no --yes/scripted bypass). Previously this
type had no dedicated producer at all; the only way to send it was the
general `otaman send` path, which let ANY caller claim `from: human` with
no check.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
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
        UI.error("Not in an otaman project")
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
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:30]

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
    msg_file = active_dir / f"{now_ts}-human-to-all-emergency-halt.md"
    msg_file.write_text(msg, encoding="utf-8")

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
