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
    """Active messages addressed to the human that are not decisions — newest first."""
    import yaml

    from otaman_cli.console.bus import _QUEUE_TYPES, _frontmatter_head

    active_dir, acks_dir = program.bus_paths()
    if not active_dir.is_dir():
        return []
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    out: list[InboxMessage] = []
    for f in sorted(active_dir.glob("*.md")):
        fm_text = _frontmatter_head(f)
        if fm_text is None or "human" not in fm_text:
            continue
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict) or fm.get("to") != "human":
            continue
        if fm.get("type") in _QUEUE_TYPES or fm.get("x-cc"):
            continue  # decisions belong to the queue; CC copies aren't the primary
        if f"{f.stem}.human.ack" in acked:
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
        subject = ""
        for line in body.splitlines():
            if line.strip().startswith("## Subject:"):
                subject = line.strip().replace("## Subject:", "").strip()
                break
        out.append(
            InboxMessage(
                stem=f.stem,
                subject=subject or f.stem,
                from_agent=str(fm.get("from", "?")),
                timestamp=str(fm.get("timestamp", "")),
                priority=str(fm.get("priority", "normal")),
                msg_type=str(fm.get("type", "info")),
                path=f,
                body=body.strip(),
            )
        )
    # newest first (timestamp string is ISO-ish; lexical sort is chronological)
    out.sort(key=lambda m: m.timestamp, reverse=True)
    return out


__all__ = ["InboxMessage", "list_inbox_messages"]
