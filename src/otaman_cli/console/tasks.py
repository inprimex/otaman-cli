"""Per-agent assigned-tasks view (interactive-human-console — assigned tasks).

Roman wants to SEE the list of tasks per agent in ``otaman -i`` — the work each
agent has been assigned. That data already exists as the per-program queue files
``<program>/.agents/queue/<agent>.md`` (human-authored markdown with
``## Active`` / ``## Queued`` / ``## Blocked`` / ``## Done`` sections). This is a
plain READ of those files — it is NOT the "captured items / backlog" concept
(suspend-refs + raw ideas), which is a separate spec-level capability.

Textual-free so it is unit-testable; the parse is lenient (loose human format)
and counts only top-level bullets under the live buckets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from otaman_cli.console.bus import Program

_LIVE_BUCKETS = ("active", "queued", "blocked")


@dataclass(frozen=True)
class AgentTasks:
    agent: str
    active: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    done: int = 0  # count only — the live buckets carry the titles

    @property
    def open_count(self) -> int:
        return len(self.active) + len(self.queued) + len(self.blocked)


def _classify(header: str) -> str:
    """Map a ``## <header>`` to a bucket by its leading word (case-insensitive)."""
    h = header.strip().lower()
    for key in ("active", "queued", "blocked", "done"):
        if h.startswith(key):
            return key
    return "other"


def _parse_queue(text: str) -> tuple[dict[str, list[str]], int]:
    buckets: dict[str, list[str]] = {b: [] for b in _LIVE_BUCKETS}
    done = 0
    current = "other"
    for line in text.splitlines():
        if line.startswith("## "):
            current = _classify(line[3:])
            continue
        # top-level bullet only (continuation lines are indented)
        if line.startswith("- "):
            title = line[2:].strip().replace("**", "").strip()
            if not title:
                continue
            if current in buckets:
                buckets[current].append(title)
            elif current == "done":
                done += 1
    return buckets, done


def list_agent_tasks(program: Program) -> list[AgentTasks]:
    """One :class:`AgentTasks` per queue file under the program, agents sorted
    by name. Agents with open work first is a view concern — this returns all."""
    qdir = program.root / ".agents" / "queue"
    if not qdir.is_dir():
        return []
    out: list[AgentTasks] = []
    for f in sorted(qdir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        buckets, done = _parse_queue(text)
        out.append(
            AgentTasks(
                agent=f.stem,
                active=buckets["active"],
                queued=buckets["queued"],
                blocked=buckets["blocked"],
                done=done,
            )
        )
    return out


__all__ = ["AgentTasks", "list_agent_tasks"]
