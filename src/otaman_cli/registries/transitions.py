"""Shared ``transitions[]`` append helper (task 6.1).

Used by every status-mutating CLI command to record an audit-trail entry
matching Appendix A.5 schema. Entries are appended in place to a loaded
ruamel.yaml document.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_transition(
    *,
    actor: str,
    action: str,
    from_: str | None = None,
    to: str | None = None,
    field: str | None = None,
    old: Any = None,
    new: Any = None,
    note: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Build a transition entry dict matching Appendix A.5 schema."""
    entry: dict[str, Any] = {
        "at": at or utc_now_iso(),
        "by": actor,
        "action": action,
    }
    if from_ is not None:
        entry["from"] = from_
    if to is not None:
        entry["to"] = to
    if field is not None:
        entry["field"] = field
    if old is not None:
        entry["old"] = old
    if new is not None:
        entry["new"] = new
    if note:
        entry["note"] = note
    return entry


def append_transition(entity: dict[str, Any], transition: dict[str, Any]) -> None:
    """Append *transition* to ``entity['transitions']`` (creates list if absent).

    Mutates *entity* in place; safe to use on a ruamel.yaml CommentedMap.
    """
    if "transitions" not in entity or entity["transitions"] is None:
        entity["transitions"] = []
    entity["transitions"].append(transition)


__all__ = [
    "utc_now_iso",
    "make_transition",
    "append_transition",
]
