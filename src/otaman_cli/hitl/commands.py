"""`otaman hitl <action>` subcommand implementations (tasks 3.2-3.4).

Three actions:
- `list` (headless) — render the HITL stack as a prioritised list
- `next` (interactive) — show the top-priority request in full
- `take <id>` (interactive) — collect a decision and emit `human-decision`

All three discover the bus via `find_project_root()` + `_resolve_bus_paths()`,
so they work from any subdirectory of an otaman-managed project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, _resolve_bus_paths
from otaman_cli.hitl.messages import (
    HumanDecisionPayload,
    RequestHumanReview,
    emit_human_decision,
    find_by_stem,
    list_pending,
    write_resolved_ack,
)


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _ctx() -> tuple[Path, Path] | None:
    root = find_project_root()
    if root is None:
        _bail("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return None
    active_dir, _acks = _resolve_bus_paths(root)
    return root, active_dir


def _human_id() -> str:
    """Resolve the human id from OTAMAN_HUMAN env, otherwise 'human'."""
    return os.environ.get("OTAMAN_HUMAN", "").strip() or "human"


def _format_request_summary(r: RequestHumanReview) -> str:
    deadline = f" deadline:{r.deadline}" if r.deadline else ""
    return (
        f"  [{r.priority:>6}] {r.msg_stem}\n"
        f"          from: {r.from_agent}  decision-type: {r.decision_type}{deadline}\n"
        f"          {r.subject}"
    )


# ---------------------------------------------------------------------------
# `otaman hitl list`


def cmd_list(args: dict[str, Any]) -> int:
    """Render the HITL stack ordered by priority + deadline."""
    ctx = _ctx()
    if ctx is None:
        return 1
    _root, active_dir = ctx
    human = _human_id()

    pending = list_pending(active_dir, human_id=human)
    if not pending:
        print(f"No pending human-review requests for {human!r}.")
        return 0

    UI.header(f"HITL stack — {len(pending)} pending request(s) for {human}")
    for req in pending:
        print()
        print(_format_request_summary(req))
    print()
    UI.muted(f"Use `otaman hitl next` to inspect the top item, or "
             f"`otaman hitl take <stem>` to respond.")
    return 0


# ---------------------------------------------------------------------------
# `otaman hitl next`


def cmd_next(args: dict[str, Any]) -> int:
    """Show the top-priority pending request in full."""
    ctx = _ctx()
    if ctx is None:
        return 1
    _root, active_dir = ctx
    human = _human_id()

    pending = list_pending(active_dir, human_id=human)
    if not pending:
        print(f"No pending human-review requests for {human!r}.")
        return 0

    top = pending[0]
    UI.header(f"Top HITL item — {top.msg_stem}")
    print(f"  From:           {top.from_agent}")
    print(f"  Priority:       {top.priority}")
    print(f"  Decision-type:  {top.decision_type}")
    print(f"  Session-id:     {top.session_id}")
    if top.deadline:
        print(f"  Deadline:       {top.deadline}")
    print(f"  Timestamp:      {top.timestamp}")
    print()
    UI.subheader("Subject")
    print(f"  {top.subject}")
    print()
    UI.subheader("Body")
    for line in top.body.splitlines():
        print(f"  {line}")
    print()
    UI.muted(f"Run `otaman hitl take {top.msg_stem}` to record a decision.")
    return 0


# ---------------------------------------------------------------------------
# `otaman hitl take <id>`


def _prompt(question: str, *, default: str = "") -> str:
    """Single-line prompt that returns default on EOF / Ctrl-C."""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"  ? {question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return raw or default


def _prompt_multiline(question: str) -> str:
    """Collect multi-line input; blank line terminates."""
    print(f"  ? {question} (blank line to end):")
    lines: list[str] = []
    while True:
        try:
            line = input("    ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def _decision_for(req: RequestHumanReview) -> str | None:
    """Run the decision-type-specific prompt. Returns decision string or
    None if the user bails out (Ctrl-C / empty in required slots)."""
    dt = req.decision_type
    if dt == "approve-reject":
        choices = ("approve", "reject", "approve-with-changes")
        while True:
            val = _prompt(f"Decision — one of {list(choices)}").lower()
            if val in choices:
                return val
            if not val:
                return None
            UI.warn(f"Invalid choice: {val!r}. Try again or empty to abort.")
    elif dt == "pick-from-options":
        val = _prompt("Decision — option-id (matches one of the originating options)")
        return val or None
    elif dt == "free-form-guidance":
        # Decision value is always 'provided' per design.md; the rationale carries the content
        return "provided"
    elif dt == "unblock-confirmation":
        choices = ("confirmed", "not-yet")
        while True:
            val = _prompt(f"Decision — one of {list(choices)}").lower()
            if val in choices:
                return val
            if not val:
                return None
            UI.warn(f"Invalid choice: {val!r}. Try again or empty to abort.")
    else:
        # Unknown decision-type — accept anything, log
        UI.warn(f"Unknown decision-type {dt!r}; accepting free-form input.")
        return _prompt("Decision (free-form)") or None


def cmd_take(args: dict[str, Any]) -> int:
    """Walk the user through replying to a `request-human-review`."""
    target = args.get("id")
    if not target:
        return _bail("Usage: otaman hitl take <msg-stem>")

    ctx = _ctx()
    if ctx is None:
        return 1
    _root, active_dir = ctx
    human = _human_id()

    req = find_by_stem(active_dir, target, human_id=human)
    if req is None:
        return _bail(f"No pending request matching {target!r}")

    # 2026-07-09 (same forgery class as F012): `take` produces a
    # `human-decision` message -- a PRIVILEGED type -- via input()-based
    # prompts with no TTY check, so a non-interactive/piped stdin could
    # forge one. Gate on a real interactive terminal before collecting
    # anything; deliberately no --yes/scripted bypass, same as
    # confirm_human_decision.
    from otaman_cli.safety import require_interactive_tty
    if not require_interactive_tty(
        f"About to take HITL item {req.msg_stem} ({req.subject!r}) -- "
        f"this will produce a `human-decision` message asserting `from: human`."
    ):
        return _bail("Refusing to record a decision without an interactive terminal.")

    UI.header(f"HITL — taking {req.msg_stem}")
    print(f"  From:          {req.from_agent}")
    print(f"  Priority:      {req.priority}")
    print(f"  Decision-type: {req.decision_type}")
    print(f"  Subject:       {req.subject}")
    print()

    decision = _decision_for(req)
    if decision is None:
        UI.muted("Aborted — no decision recorded.")
        return 0

    rationale = _prompt_multiline("Rationale (optional but recommended)")
    followup = _prompt_multiline("Followup actions (optional)")

    decided_by = _prompt("Decided by", default=human) or human

    payload = HumanDecisionPayload(
        in_reply_to=req.id,
        session_id=req.session_id,
        to_agent=req.from_agent,
        decision=decision,
        decided_by=decided_by,
        rationale=rationale,
        followup_actions=followup,
        subject=f"Re: {req.subject}",
    )
    out_path = emit_human_decision(payload, active_dir)
    ack_path = write_resolved_ack(active_dir, req.msg_stem, by=human)

    UI.ok(f"Recorded decision: {out_path.name}")
    UI.muted(f"  in-reply-to: {req.id}")
    UI.muted(f"  session-id:  {req.session_id}")
    UI.muted(f"  decision:    {decision}")
    UI.muted(f"Original request marked resolved: {ack_path.name}")
    return 0


# ---------------------------------------------------------------------------
# Dispatch


_ACTIONS = {
    "list": cmd_list,
    "next": cmd_next,
    "take": cmd_take,
}


def dispatch(action: str, args: dict[str, Any]) -> int:
    fn = _ACTIONS.get(action)
    if fn is None:
        UI.error(f"Unknown hitl action: {action}")
        UI.muted("Available: " + ", ".join(sorted(_ACTIONS.keys())))
        return 2
    return fn(args)


__all__ = ["dispatch"]
