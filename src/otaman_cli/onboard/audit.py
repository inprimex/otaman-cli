"""CloudEvents-shaped JSONL audit log for onboarding actions.

Same envelope shape as otaman-runner's audit log so a unified audit
viewer can read both streams. Lives in otaman-cli for now; consider
moving to otaman-core if a third consumer appears (B-42-ish).
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CE_SOURCE = "otaman-onboard"
CE_SPECVERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OnboardAudit:
    """Append-only JSONL writer with daily rotation and thread safety."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _current_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.base_dir / f"{today}.jsonl"

    def emit(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        actor: str | None = None,
        result: str = "success",
        event_id: str | None = None,
        time: str | None = None,
    ) -> Path:
        """Append one event.

        Onboarding events get an ``actor`` field at the top of ``data``
        (the operator running the command) and a ``result`` (success /
        failure) for fast filtering in audit reviews.
        """
        body = {"actor": actor, "result": result, **data}
        envelope = {
            "specversion": CE_SPECVERSION,
            "id": event_id or str(uuid.uuid4()),
            "source": CE_SOURCE,
            "type": event_type,
            "time": time or utc_now_iso(),
            "data": body,
        }
        path = self._current_path()
        line = json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as fp:
                fp.write(line)
        return path

    # ---- Convenience helpers ----------------------------------------

    def user_added(self, *, actor: str | None, subject: str, roles: list[str]) -> None:
        self.emit(
            "otaman.onboard.user_added",
            {"subject": subject, "roles": roles},
            actor=actor,
        )

    def user_add_failed(self, *, actor: str | None, subject: str, error: str) -> None:
        self.emit(
            "otaman.onboard.user_add_failed",
            {"subject": subject, "error": error},
            actor=actor,
            result="failure",
        )

    def doctor_run(self, *, actor: str | None, fail_count: int, warn_count: int) -> None:
        self.emit(
            "otaman.onboard.doctor_run",
            {"fail_count": fail_count, "warn_count": warn_count},
            actor=actor,
            result="success" if fail_count == 0 else "failure",
        )
