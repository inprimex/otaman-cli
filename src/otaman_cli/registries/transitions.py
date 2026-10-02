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


def changes_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The field audit of *entry*, in one shape, whichever shape it was written in.

    Two shapes exist on purpose (Appendix A.5): historical rows carry the flat
    `field`/`old`/`new` trio, which could only describe ONE field; rows from core's
    access contract carry a `changes` list with an entry per changed field. Readers
    should ask here rather than branch, so a transition written before the pin and one
    written after answer the same question the same way.

    `old` is omitted — not None — when the field had no previous value, in both shapes.
    """
    raw = entry.get("changes")
    if isinstance(raw, list):
        return [dict(c) for c in raw if isinstance(c, dict)]
    if entry.get("field") is None:
        return []
    one: dict[str, Any] = {"field": entry["field"]}
    if "old" in entry and entry["old"] is not None:
        one["old"] = entry["old"]
    if "new" in entry:
        one["new"] = entry["new"]
    return [one]


def changed_field(entry: dict[str, Any], field: str) -> dict[str, Any] | None:
    """The change entry for *field* in *entry*, or None if that field did not change."""
    for change in changes_of(entry):
        if change.get("field") == field:
            return change
    return None
