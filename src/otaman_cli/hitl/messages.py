"""Bus-message readers + builders for the HITL stack (tasks 3.2-3.4).

Two message types per `auto-session-spawn-on-bus-events/design.md` Q4
Resolved 2026-05-21:

- `request-human-review` — agent → human, requesting a decision
- `human-decision` — human → agent, the response (with `in-reply-to`)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from otaman_cli.identity import find_project_root
from otaman_cli.main import _resolve_bus_paths


PRIORITY_RANK: dict[str, int] = {"urgent": 4, "high": 3, "normal": 2, "low": 1}

# All four decision-type values per design.md Q4 Resolved schemas
DecisionType = Literal[
    "approve-reject",
    "pick-from-options",
    "free-form-guidance",
    "unblock-confirmation",
]


@dataclass
class RequestHumanReview:
    """One pending `request-human-review` message."""

    msg_stem: str           # filename without .md
    path: Path
    id: str
    from_agent: str
    to_human: str
    priority: str
    decision_type: str
    session_id: str
    deadline: str | None
    timestamp: str
    subject: str
    body: str               # full markdown body after the frontmatter

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 0)


def _parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter_dict, body)."""
    m = re.match(r"^---\n(.+?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text
    if not isinstance(fm, dict):
        return None, text
    return fm, m.group(2)


def _extract_subject(body: str) -> str:
    m = re.search(r"^##\s+Subject:\s*(.+?)$", body, re.MULTILINE)
    return m.group(1).strip() if m else "(no subject)"


def list_pending(
    bus_active_dir: Path, *, human_id: str | None = None,
) -> list[RequestHumanReview]:
    """Return all pending `request-human-review` messages addressed to *human_id*.

    Pending = no ack file in ``acks/<stem>.<human_id>.ack``.
    When *human_id* is None, accepts any human recipient (matches `to: human`
    or `to: <specific human>`).
    """
    acks_dir = bus_active_dir / "acks"
    out: list[RequestHumanReview] = []
    if not bus_active_dir.is_dir():
        return out

    for f in sorted(bus_active_dir.glob("*.md")):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _parse_frontmatter(text)
        if fm is None:
            continue
        if fm.get("type") != "request-human-review":
            continue
        to_field = str(fm.get("to") or "")
        if human_id and to_field != human_id and to_field != "human":
            continue

        # Check ack files — any acked message is excluded
        if any(acks_dir.glob(f"{f.stem}.*.ack")) if acks_dir.is_dir() else False:
            continue

        out.append(RequestHumanReview(
            msg_stem=f.stem,
            path=f,
            id=str(fm.get("id") or f.stem),
            from_agent=str(fm.get("from") or ""),
            to_human=to_field,
            priority=str(fm.get("priority") or "normal"),
            decision_type=str(fm.get("decision-type") or ""),
            session_id=str(fm.get("session-id") or ""),
            deadline=fm.get("deadline"),
            timestamp=str(fm.get("timestamp") or ""),
            subject=_extract_subject(body),
            body=body.strip(),
        ))

    # Sort: priority DESC, then deadline ASC (sooner = higher), then timestamp ASC
    def _sort_key(r: RequestHumanReview):
        deadline_key = r.deadline or "9999"
        return (-r.priority_rank, deadline_key, r.timestamp)
    out.sort(key=_sort_key)
    return out


def find_by_stem(
    bus_active_dir: Path, stem_or_id: str, *, human_id: str | None = None,
) -> RequestHumanReview | None:
    """Locate a single pending request by stem or by frontmatter id."""
    for req in list_pending(bus_active_dir, human_id=human_id):
        if stem_or_id in (req.msg_stem, req.id):
            return req
        # Tolerant: also match prefix
        if req.msg_stem.startswith(stem_or_id):
            return req
    return None


# ---------------------------------------------------------------------------
# human-decision builder + emitter


@dataclass
class HumanDecisionPayload:
    """Frontmatter + body content for a `human-decision` message."""

    in_reply_to: str
    session_id: str
    to_agent: str
    decision: str
    decided_by: str
    rationale: str = ""
    followup_actions: str = ""
    subject: str = ""

    def render(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        fm = {
            "id": f"{ts_id}-human-decision-{self.in_reply_to[:24]}",
            "from": "human",
            "to": self.to_agent,
            "priority": "high",
            "type": "human-decision",
            "in-reply-to": self.in_reply_to,
            "session-id": self.session_id,
            "decision": self.decision,
            "decided-by": self.decided_by,
            "timestamp": ts,
            "status": "pending",
        }
        fm_yaml = yaml.dump(fm, sort_keys=False, default_flow_style=False)
        subj = self.subject or f"Re: {self.in_reply_to}"
        body = [
            "---",
            fm_yaml.rstrip(),
            "---",
            "",
            f"## Subject: {subj}",
            "",
            "### Decision",
            self.decision,
        ]
        if self.rationale:
            body += ["", "### Rationale", self.rationale]
        if self.followup_actions:
            body += ["", "### Followup actions", self.followup_actions]
        body.append("")
        return "\n".join(body)


def emit_human_decision(
    payload: HumanDecisionPayload, bus_active_dir: Path,
) -> Path:
    """Write the `human-decision` message file. Returns the absolute path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = (
        f"{ts}-human-to-{payload.to_agent.replace('/', '-')}-human-decision-"
        f"{payload.in_reply_to[:30]}.md"
    )
    bus_active_dir.mkdir(parents=True, exist_ok=True)
    out = bus_active_dir / filename
    out.write_text(payload.render(), encoding="utf-8")
    return out


def write_resolved_ack(
    bus_active_dir: Path, msg_stem: str, *, by: str = "human",
) -> Path:
    """Mark a `request-human-review` resolved by writing the ack sentinel.

    Mirrors the existing otaman bus ack convention:
    ``<active_dir>/acks/<msg_stem>.<actor>.ack`` whose contents indicate the
    resolution kind (`resolved` here).
    """
    acks_dir = bus_active_dir / "acks"
    acks_dir.mkdir(parents=True, exist_ok=True)
    ack_path = acks_dir / f"{msg_stem}.{by}.ack"
    ack_path.write_text("resolved\n", encoding="utf-8")
    return ack_path


__all__ = [
    "PRIORITY_RANK",
    "DecisionType",
    "RequestHumanReview",
    "HumanDecisionPayload",
    "list_pending",
    "find_by_stem",
    "emit_human_decision",
    "write_resolved_ack",
]
