"""Inter-agent request/response contract — sort + deadline + ack advisory.

Implements tasks 2.1-2.3 from `inter-agent-request-response-contract`:

- 2.1: `otaman check` sort tiebreaker within each priority band:
    expects-response: True before False/absent → response-effort XS→XL → timestamp
- 2.2: `[DEADLINE]` indicator when `response-deadline` is within 2 hours
- 2.3: `otaman ack --resolved` advisory when message expects a response and
    no outbound `reply-to: <this-id>` exists

Source of truth: `auto-session-spawn-on-bus-events`-adjacent change
`inter-agent-request-response-contract/design.md` Q1-Q4.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Type → default response-effort (Q4 §49-65 of design.md)
TYPE_DEFAULT_EFFORT: dict[str, str] = {
    "question": "S",
    "task-assignment": "M",
    "spec-change-request": "L",
    "contract-change": "M",
    "info": "XS",
    "fyi": "XS",
    "review-request": "M",
    "spec-change-approved": "XS",
    "task-complete": "XS",
    "proposal": "L",
}

EFFORT_ORDER: dict[str, int] = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}
PRIORITY_ORDER: dict[str, int] = {"urgent": 4, "high": 3, "normal": 2, "low": 1}

# Deadline window: alert when within 2 hours (or past)
DEADLINE_WINDOW = timedelta(hours=2)


def resolve_response_effort(msg_type: str, explicit_effort: str | None) -> str:
    """Return the effective response-effort for a message.

    Sender's explicit field wins over the type-default. Unknown types fall
    back to ``M`` (medium) so unrecognized message kinds don't sort last by
    accident.
    """
    if explicit_effort:
        return str(explicit_effort).upper()
    return TYPE_DEFAULT_EFFORT.get(msg_type, "M")


def make_sort_key(msg: dict[str, Any]):
    """Return a sortable tuple for messages within a priority band.

    Sort order (ascending — lower tuples come first):
      1. Priority DESC (urgent > high > normal > low)
      2. expects-response True before False/absent
      3. response-effort XS → XL ascending (using type-default when missing)
      4. timestamp ascending
    """
    priority = str(msg.get("priority") or "normal")
    return (
        -PRIORITY_ORDER.get(priority, 0),
        0 if msg.get("expects_response") else 1,
        EFFORT_ORDER.get(
            resolve_response_effort(
                str(msg.get("type") or ""),
                msg.get("response_effort"),
            ),
            99,
        ),
        str(msg.get("timestamp") or ""),
    )


def deadline_is_imminent(
    deadline_iso: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True when *deadline_iso* is within ``DEADLINE_WINDOW`` of *now*.

    Returns True for deadlines in the past (they're even more urgent than
    "within 2h"). Returns False when *deadline_iso* is absent or unparseable.
    """
    if not deadline_iso:
        return False
    try:
        dt = datetime.fromisoformat(str(deadline_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (dt - now) <= DEADLINE_WINDOW


def has_outbound_reply(
    active_dir: Path,
    *,
    in_reply_to_id: str,
    from_agent: str,
) -> bool:
    """Check whether *from_agent* has sent a reply with ``reply-to: <id>``.

    Scans every .md in *active_dir*'s frontmatter for the (from, reply-to) pair.
    """
    if not active_dir.is_dir():
        return False
    for f in active_dir.glob("*.md"):
        try:
            head = f.read_text(encoding="utf-8")[:1024]
        except OSError:
            continue
        fm_match = re.match(r"^---\n(.+?)\n---", head, re.DOTALL)
        if not fm_match:
            continue
        fm_text = fm_match.group(1)
        # Cheap line-by-line scan — frontmatter is small + flat
        from_match = re.search(r"^from:\s*(\S+)", fm_text, re.MULTILINE)
        reply_match = re.search(r"^reply-to:\s*(\S+)", fm_text, re.MULTILINE)
        if not from_match or not reply_match:
            continue
        if from_match.group(1).strip() != from_agent:
            continue
        if reply_match.group(1).strip() == in_reply_to_id:
            return True
    return False


__all__ = [
    "TYPE_DEFAULT_EFFORT",
    "EFFORT_ORDER",
    "PRIORITY_ORDER",
    "DEADLINE_WINDOW",
    "resolve_response_effort",
    "make_sort_key",
    "deadline_is_imminent",
    "has_outbound_reply",
]
