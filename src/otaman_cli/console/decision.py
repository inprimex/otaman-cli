"""Console approve / reject / defer — the human's SSH-identified decisions (1.2).

The console is the human's own SSH-identified session with NO agent in the loop,
so the human's keypress IS the confirmation — there is no adapter prompt.

Signal parity (D4): approving a spec-change-request routes through the SAME
fail-closed, ledger-gated writer as `/otaman:approve` (`_perform_approval`), so
the `spec-change-approved` broadcast is indistinguishable across surfaces;
rejecting routes through `_perform_rejection`. Outcome-proposals have no
`/otaman:approve` signal (that command is SCR-only), so their approve/reject is
recorded as a human-decision AUDIT entry on the bus and acked — no invented
cross-domain signal. Defer records an audit entry for either type and leaves the
item pending (a postponement, not a resolution). Every verb, both types, leaves
a bus audit entry, each with the human's recorded reason.
"""

from __future__ import annotations

import contextlib
import io
import re
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


def _approver_refusal(program: Program) -> str | None:
    """The named refusal iff the acting human is a resolved non-approver.

    hitl-default-approver 2.2 — the console decision path shares the SAME
    eligibility resolution as HITL (`otaman hitl take`), so "may approve" and
    "may confirm" are one grant. An unresolved OTAMAN_HUMAN returns None (its
    existing unverified-stamp behavior is unchanged); only a roster human who
    lacks the `approver` role is refused.
    """
    from otaman_cli.approver_eligibility import refusal_message, resolve_eligibility

    elig = resolve_eligibility(program.root / "platform.yaml")
    return refusal_message(elig) if elig.refused else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "item"


def _write_audit(
    program: Program,
    proposal: Proposal,
    identity: ConsoleIdentity,
    *,
    verb: str,
    reason: str,
    ack: bool,
) -> str:
    """Write a human-decision audit message to the bus (from human) and, when the
    decision resolves the item (`ack`), drop it from the pending queue. Returns
    the audit stem. Used for defer (no ack) and outcome-proposal approve/reject
    (ack) — a values-free record, not a privileged spec-change signal."""
    active_dir, acks_dir = program.bus_paths()
    now_iso, now_ts = _now()
    stem = f"{now_ts}-human-to-all-console-{verb}-{_slug(proposal.subject)}"
    reason_section = f"\n### Reason\n{reason}\n" if reason else ""
    content = (
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\ntype: info\n"
        f"timestamp: {now_iso}\nstatus: pending\n---\n\n"
        f"## Subject: {verb.capitalize()}: {proposal.subject}\n\n"
        f"The {proposal.msg_type} **{proposal.stem}** from **{proposal.from_agent}** "
        f"was **{verb}** in otaman -i by {identity.audit_label}.\n{reason_section}"
    )
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / f"{stem}.md").write_text(content, encoding="utf-8")
    if ack:
        acks_dir.mkdir(parents=True, exist_ok=True)
        (acks_dir / f"{proposal.stem}.human.ack").write_text(f"{verb}\n", encoding="utf-8")
    return stem


def approve(
    program: Program, proposal: Proposal, identity: ConsoleIdentity, *, reason: str = ""
) -> tuple[bool, str]:
    """Approve *proposal*, stamped with identity. SCR → the privileged
    `spec-change-approved` writer (parity with `/otaman:approve`); outcome-proposal
    → an audit sign-off. Returns ``(ok, message)`` — never raises into the TUI."""
    refusal = _approver_refusal(program)
    if refusal is not None:
        return False, f"Approval refused — {refusal}."

    if proposal.msg_type != "spec-change-request":
        stem = _write_audit(program, proposal, identity, verb="approved", reason=reason, ack=True)
        return True, f"Signed off {proposal.stem} — audit {stem}"

    from otaman_cli.commands.approve import _perform_approval

    active_dir, acks_dir = program.bus_paths()
    now_iso, now_ts = _now()
    tail = f"Confirmed in otaman -i by {identity.audit_label}"
    comment = f"{reason} — {tail}" if reason else tail
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
    """Reject *proposal*, stamped with identity. SCR → the privileged
    `spec-change-rejected` writer; outcome-proposal → an audit rejection."""
    refusal = _approver_refusal(program)
    if refusal is not None:
        return False, f"Rejection refused — {refusal}."

    if proposal.msg_type != "spec-change-request":
        stem = _write_audit(program, proposal, identity, verb="rejected", reason=reason, ack=True)
        return True, f"Rejected {proposal.stem} — audit {stem}"

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


def defer(
    program: Program, proposal: Proposal, identity: ConsoleIdentity, *, reason: str = ""
) -> tuple[bool, str]:
    """Defer *proposal* — record a bus audit entry with the reason and LEAVE it
    pending (a postponement, not a resolution). No approver gate: deferring is a
    note, not a privileged decision. Works for both SCRs and outcome-proposals."""
    stem = _write_audit(program, proposal, identity, verb="deferred", reason=reason, ack=False)
    return True, f"Deferred {proposal.stem} (still pending) — audit {stem}"


__all__ = ["approve", "defer", "reject"]
