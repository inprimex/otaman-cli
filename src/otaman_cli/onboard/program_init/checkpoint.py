"""Checkpoint read/write for failure recovery (tasks.md 2.3).

The init flow writes ``~/.otaman/<program-slug>/.init-state.yaml`` after
each step completes.  On re-run the runner detects the checkpoint and offers
to resume from the last successful step or restart from scratch.

Checkpoint format::

    program: my-program
    completed_steps: [identity, roles, processes, currency, scales,
                      releases, skill_profile, git_platform, zitadel]
    answers:
      program_name: my-program
      description: "My program"
      ...

The ``answers`` block is the cumulative answers dict that can be fed
directly back into the flow.
"""
from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_STATE_DIR_BASE = Path.home() / ".otaman"
_CHECKPOINT_FILENAME = ".init-state.yaml"


def _checkpoint_path(program_slug: str) -> Path:
    return _STATE_DIR_BASE / program_slug / _CHECKPOINT_FILENAME


@dataclass
class Checkpoint:
    program: str
    completed_steps: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ r/w

    def save(self) -> Path:
        path = _checkpoint_path(self.program)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Restrict directory + file to owner-only (security: checkpoint may
        # contain email addresses or Zitadel admin user from answers dict)
        path.parent.chmod(0o700)
        data = {
            "program": self.program,
            "completed_steps": list(self.completed_steps),
            "answers": dict(self.answers),
        }
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def mark_step(self, step_id: str, step_answers: dict[str, Any]) -> None:
        """Record a completed step and merge its answers; then persist."""
        if step_id not in self.completed_steps:
            self.completed_steps.append(step_id)
        self.answers.update(step_answers)
        self.save()

    def clear(self) -> None:
        """Remove checkpoint on successful completion."""
        path = _checkpoint_path(self.program)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------ class

    @classmethod
    def load(cls, program_slug: str) -> "Checkpoint | None":
        """Return a Checkpoint if one exists for *program_slug*, else None."""
        path = _checkpoint_path(program_slug)
        if not path.is_file():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return cls(
                program=data.get("program", program_slug),
                completed_steps=list(data.get("completed_steps") or []),
                answers=dict(data.get("answers") or {}),
            )
        except yaml.YAMLError:
            return None  # corrupt checkpoint — treat as missing

    @classmethod
    def new(cls, program_slug: str) -> "Checkpoint":
        return cls(program=program_slug)
