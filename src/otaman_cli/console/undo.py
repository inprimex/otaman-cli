"""Undo classification and the inverse audit entry (console-undo 1.2).

Roman deferred a decision item and could not tell which one. Damage was zero
only because defer happens to be non-destructive; reconstructing what happened
meant grepping console logs and the bus from outside the console.

Undo here never erases history. It writes an INVERSE entry — the record says a
thing was done and then undone, because a decision that reached the bus was real
even if it was a mistake, and a log that can be rewritten is not an audit trail.

**The classification is a table, not a rule.** 1.2 says so explicitly, and the
reason is that every inference I could write is wrong somewhere: "approve is
irreversible" is about the broadcast, not the word; "defer is reversible" is
about the ack staying False, not the verb. So each action is listed, with why.

The default for an UNLISTED action is IRREVERSIBLE. A new verb that nobody
classified must not become undoable by accident — the failure mode of guessing
wrong in that direction is undoing something that already left the console.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Actions whose effect is local to the console and can be inverted: the item
#: returns to the state it was in, and nothing downstream acted on it.
#:
#: Each value is WHY it is reversible, shown nowhere but read by whoever edits
#: this table next — the justification is the point, not the boolean.
REVERSIBLE: dict[str, str] = {
    "defer": "an audit note with ack=False; the item stays pending",
    "defer-auto": "same as defer — the -auto suffix is a delivery mode, not a verb",
    "reject": "outcome-proposal rejection is a console-local disposition",
    "reject-auto": "same as reject",
    "choose": "records a chosen solution; re-choosing supersedes it",
    "discard": "marks a candidate discarded; the artifact is untouched",
    "nudge": "an unread nudge message — undo marks it withdrawn",
}

#: Actions whose signal has LEFT the console. Agents may already be acting on
#: them, so the protection is the naming confirmation (1.1), not undo.
IRREVERSIBLE: dict[str, str] = {
    "approve": "the approval broadcast has gone to the fleet",
    "approve-auto": "the approval broadcast has gone to the fleet",
    "approve (spec-approved)": "the spec-approved broadcast has gone to the fleet",
    "spec-approve": "the spec-approved broadcast has gone to the fleet",
    "ratify": "ratification is recorded on the change and agents dispatch from it",
    "archive": "the change has been archived and its folder moved",
    "accept-cost": "the accepted cost is committed to the outcome record",
    "request-changes": "the request has been delivered to the authoring agent",
}

#: What an unlisted action is treated as, and why the default points this way.
UNKNOWN_REASON = (
    "this action is not in the reversibility table, so it is treated as "
    "irreversible — a verb nobody classified must not become undoable by accident"
)


@dataclass(frozen=True)
class UndoableAction:
    """The last decision action taken in this session."""

    action: str
    target: str
    #: Human-facing description of what was acted on, for the refusal/confirmation.
    described: str = ""

    @property
    def reversible(self) -> bool:
        return is_reversible(self.action)


def is_reversible(action: str) -> bool:
    """Whether *action* can be undone. Unlisted actions are NOT."""
    return (action or "").strip() in REVERSIBLE


def why_irreversible(action: str) -> str:
    """The reason undo refuses *action* — named, never a bare refusal."""
    key = (action or "").strip()
    if key in IRREVERSIBLE:
        return IRREVERSIBLE[key]
    return UNKNOWN_REASON


def why_reversible(action: str) -> str:
    """The reason *action* is undoable (for the table's own tests)."""
    return REVERSIBLE.get((action or "").strip(), "")


def classify(action: str) -> tuple[bool, str]:
    """``(reversible, reason)`` for *action* — one call, both halves."""
    if is_reversible(action):
        return True, why_reversible(action)
    return False, why_irreversible(action)


def inverse_verb(action: str) -> str:
    """The verb recorded for the inverse entry (`defer` → `undo-defer`)."""
    return f"undo-{(action or 'action').strip()}"


def refusal_message(item: UndoableAction) -> str:
    """What the console says when undo is refused."""
    return (
        f"Cannot undo {item.action} on {item.described or item.target} — "
        f"{why_irreversible(item.action)}."
    )


def confirmation_message(item: UndoableAction) -> str:
    """What the console says when undo succeeds."""
    return f"Undid {item.action} on {item.described or item.target}."


def write_inverse_entry(program, item: UndoableAction, identity, *, reason: str = "") -> str:
    """Record that *item* was undone — an INVERSE entry, never a deletion.

    A decision that reached the bus was real even if it was a mistake, so undo
    adds a record rather than removing one. Same writer and same stem convention
    as the original decision (`console-<verb>-<slug>`), so the pair reads as a
    sequence in the bus rather than as two unrelated events.
    """
    from datetime import datetime, timezone

    from otaman_cli.bus_stem_gate import bus_stem
    from otaman_cli.bus_write import write_message_exclusive

    active_dir, _acks = program.bus_paths()
    now = datetime.now(timezone.utc)
    stem_api = bus_stem()
    verb = inverse_verb(item.action)
    stem = stem_api.build_stem(
        timestamp=now.strftime("%Y%m%dT%H%M%S"),
        sender="human",
        recipient="all",
        slug=f"console-{verb}-{stem_api.slugify(item.target, max_len=30)}",
    )
    label = getattr(identity, "audit_label", "") or "the console operator"
    reason_section = f"\n### Reason\n{reason}\n" if reason else ""
    content = (
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\ntype: announce\n"
        f"timestamp: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\nstatus: pending\n---\n\n"
        f"## Subject: Undo: {item.action} on {item.described or item.target}\n\n"
        f"The **{item.action}** recorded against **{item.target}** was UNDONE in "
        f"otaman -i by {label}. The original entry stands; this reverses it.\n"
        f"{reason_section}"
    )
    active_dir.mkdir(parents=True, exist_ok=True)
    return write_message_exclusive(active_dir / f"{stem}.md", content).stem


def table_is_exhaustive(actions: list[str]) -> list[str]:
    """Actions in *actions* classified by neither table — the drift check.

    A new console verb that nobody added here still WORKS; it is simply not
    undoable. This exists so a test can say so out loud rather than letting the
    omission be discovered by an operator pressing `u` and being told no.
    """
    return [a for a in actions if a not in REVERSIBLE and a not in IRREVERSIBLE]


__all__ = [
    "IRREVERSIBLE",
    "REVERSIBLE",
    "UNKNOWN_REASON",
    "UndoableAction",
    "classify",
    "confirmation_message",
    "inverse_verb",
    "is_reversible",
    "refusal_message",
    "table_is_exhaustive",
    "why_irreversible",
    "why_reversible",
    "write_inverse_entry",
]
