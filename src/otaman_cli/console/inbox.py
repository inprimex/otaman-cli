"""Textual-free messages-to-human inbox (console-ux-redesign wave 1, task 1.2).

The human's bus inbox: active messages addressed ``to: human`` that are NOT
decisions (spec-change-requests / outcome-proposals live in the queue, surfaced
by :mod:`otaman_cli.console.bus`). It deliberately includes human-to-human
messages — anything addressed to the human, from an agent OR another human,
belongs here. CC copies (``x-cc``) and already-acked items are excluded, mirroring
the queue's rules so the two never double-count.

Values-free, and it reuses the queue's bounded-head frontmatter parse so a
~3000-message active dir scans fast (5.1 finding #5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otaman_cli.console.bus import Program


@dataclass(frozen=True)
class InboxMessage:
    """One inbox message as the console displays it (values-free)."""

    stem: str
    subject: str
    from_agent: str
    timestamp: str
    priority: str
    msg_type: str
    path: Path
    body: str

    @property
    def from_human(self) -> bool:
        """Heuristic display flag: sender looks like a human (not an ``*-agent``)."""
        f = (self.from_agent or "").strip()
        return f == "human" or not f.endswith("-agent")


def list_inbox_messages(program: Program) -> list[InboxMessage]:
    """Active messages addressed to the human that are not decisions — newest first.

    Reads the bus through the SHARED index, and carries NO bodies: this list
    full-read all ~727 matching files just to pull a subject line out of each,
    the same waste the decision queue shed in 2.1. Subject comes from a bounded
    head; the body is read on open by ``bus.read_body``.
    """
    from otaman_cli.console.bus import _QUEUE_TYPES, _subject_head
    from otaman_cli.console.bus_index import active_entries

    _, acks_dir = program.bus_paths()
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    out: list[InboxMessage] = []
    for entry in active_entries(program):
        f = entry.path
        # Cheap tier first — only surviving rows get the authoritative parse.
        if entry.tag("to") != "human":
            continue
        if entry.tag("type") in _QUEUE_TYPES or entry.flag("x-cc"):
            continue  # decisions belong to the queue; CC copies aren't the primary
        if f"{f.stem}.human.ack" in acked:
            continue
        fm = entry.fm
        if not fm:
            continue
        out.append(
            InboxMessage(
                stem=f.stem,
                subject=_subject_head(f) or f.stem,
                from_agent=str(fm.get("from", "?")),
                timestamp=str(fm.get("timestamp", "")),
                priority=str(fm.get("priority", "normal")),
                msg_type=str(fm.get("type", "info")),
                path=f,
                body="",  # lazy — see bus.read_body()
            )
        )
    # newest first (timestamp string is ISO-ish; lexical sort is chronological)
    out.sort(key=lambda m: m.timestamp, reverse=True)
    return out


__all__ = ["InboxMessage", "list_inbox_messages"]
