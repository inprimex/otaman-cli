"""AgentStatus dataclass + State enum (task 1.1).

Schema mirrors design.md Q1 exactly:

    agent: runner-agent
    state: working          # working | blocked | waiting | idle
    task: "1.3 Implement GET /programs endpoint"   # null when idle
    change: programs-catalog-multi-session-ui      # null when idle
    outcome: null           # outcome slug; null until outcome tracking enabled
    blocked_by: null        # "human" | "<agent>" when blocked; null otherwise
    since: 2026-06-09T10:30:00Z    # when current state was entered
    updated_at: 2026-06-09T10:47:00Z  # last write (heartbeat); same as since on first write
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class State(str, Enum):
    """The four allowed agent presence states."""

    WORKING = "working"
    BLOCKED = "blocked"
    WAITING = "waiting"
    IDLE = "idle"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class AgentStatus:
    """Singleton status record for one agent."""

    agent: str
    state: State
    task: str | None = None
    change: str | None = None
    outcome: str | None = None
    blocked_by: str | None = None
    since: str = field(default_factory=_iso_now)
    updated_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "state": self.state.value if isinstance(self.state, State) else str(self.state),
            "task": self.task,
            "change": self.change,
            "outcome": self.outcome,
            "blocked_by": self.blocked_by,
            "since": self.since,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStatus:
        raw_state = data.get("state", "idle")
        try:
            state = State(raw_state)
        except ValueError:
            state = State.IDLE
        return cls(
            agent=str(data.get("agent", "")),
            state=state,
            task=data.get("task"),
            change=data.get("change"),
            outcome=data.get("outcome"),
            blocked_by=data.get("blocked_by"),
            since=str(data.get("since") or _iso_now()),
            updated_at=str(data.get("updated_at") or _iso_now()),
        )


__all__ = ["AgentStatus", "State"]
