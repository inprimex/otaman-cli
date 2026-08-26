"""Console approve/reject — reuse the privileged CLI writers (task 1.2).

The console is the human's own SSH-identified session with NO agent in the
loop, so the human's keypress IS the confirmation — there is no adapter
prompt. The decision routes through the SAME fail-closed, ledger-gated
writers as `otaman approve` (`_perform_approval` / `_perform_rejection`), so
there is exactly one privileged path, and the audit is stamped with the
SSH-derived identity via the writers' `comment` field.

The writers print CLI-style output; we redirect that during the TUI so it
can't corrupt the Textual display — the console surfaces its own result.
"""

from __future__ import annotations

import contextlib
import io
from datetime import datetime, timezone

from otaman_cli.console.bus import Program, Proposal
from otaman_cli.console.identity import ConsoleIdentity


def _now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y%m%dT%H%M%S")


def _target(proposal: Proposal) -> dict:
    """Adapt a console Proposal into the target dict the CLI writers expect."""
    return {
        "subject": proposal.subject,
        "stem": proposal.stem,
        "fm": {"from": proposal.from_agent},
    }


def approve(program: Program, proposal: Proposal, identity: ConsoleIdentity) -> tuple[bool, str]:
    """Approve *proposal* through the privileged writer, stamped with identity.

    Returns ``(ok, message)`` — never raises into the TUI.
    """
    from otaman_cli.commands.approve import _perform_approval

    active_dir, acks_dir = program.bus_paths()
    now_iso, now_ts = _now()
    comment = f"Confirmed in otaman -i by {identity.audit_label}"
    with contextlib.redirect_stdout(io.StringIO()):
        rc = _perform_approval(
            _target(proposal),
            active_dir=active_dir,
            acks_dir=acks_dir,
            root=program.root,
            comment=comment,
            now_ts=now_ts,
            now_iso=now_iso,
        )
    if rc == 0:
        return True, f"Approved {proposal.stem} — {comment}"
    return False, f"Approval failed for {proposal.stem} (ledger gate refused)."


def reject(
    program: Program, proposal: Proposal, identity: ConsoleIdentity, *, reason: str = ""
) -> tuple[bool, str]:
    """Reject *proposal* through the privileged writer, stamped with identity."""
    from otaman_cli.commands.approve import _perform_rejection

    active_dir, acks_dir = program.bus_paths()
    now_iso, now_ts = _now()
    tail = f"Rejected in otaman -i by {identity.audit_label}"
    comment = f"{reason} — {tail}" if reason else tail
    with contextlib.redirect_stdout(io.StringIO()):
        rc = _perform_rejection(
            _target(proposal),
            active_dir=active_dir,
            acks_dir=acks_dir,
            root=program.root,
            comment=comment,
            now_ts=now_ts,
            now_iso=now_iso,
        )
    if rc == 0:
        return True, f"Rejected {proposal.stem} — {comment}"
    return False, f"Rejection failed for {proposal.stem} (ledger gate refused)."


__all__ = ["approve", "reject"]
