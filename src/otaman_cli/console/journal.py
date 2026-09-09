"""Console observability — the silent-approval-loss defect fix.

Two loud, exception-proof traces so a console decision can NEVER fail silently.
The 2026-09-09 incident: Roman spec-approved ``console-ux-redesign`` in
``otaman -i`` and nothing landed — no stage write, no commit, no broadcast, and
no error. His seat runs without tmux, so there was no scrollback to even find a
stack trace. The root cause class: a decision action raised inside a Textual
screen-dismiss callback, and an unguarded callback exception vanishes into the
message pump — the ``self.app.notify(...)`` line after the call is never
reached. This module closes that hole two ways:

- a per-session **LOG FILE** (``~/.otaman/console-logs/<start-ts>.log``):
  session start + identity, every decision action with its result, every
  caught exception with its traceback — the full session trace (spec-agent
  addendum 3, Roman's observability point: "no tmux, nothing to scroll").
- the **ACTION JOURNAL**: :func:`run_decision_action` wraps every decision so
  it records intent-then-result around the write. A swallowed failure becomes
  impossible — either the journal shows the failure loudly, or the action
  never reached the handler (a different, still-visible finding).

Logging is best-effort and never raises into the TUI: an observability layer
that could crash the console would be worse than the gap it closes.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

#: How long an error notification lingers (seconds). Errors outlive the 8s
#: success toast so a failed decision can't scroll away unseen.
_ERROR_TIMEOUT = 12.0
_OK_TIMEOUT = 8.0


def _default_log_dir() -> Path:
    return Path.home() / ".otaman" / "console-logs"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConsoleLog:
    """Append-only per-session log. Every write is best-effort; a filesystem
    failure degrades to the in-memory ``degraded`` flag rather than raising into
    the console (observability must never be the thing that crashes)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.degraded = path is None

    @classmethod
    def open(
        cls,
        *,
        log_dir: Path | None = None,
        identity: str = "",
        programs: object = (),
    ) -> ConsoleLog:
        """Create the session log and write the ``session-start`` record.

        *log_dir* defaults to ``~/.otaman/console-logs`` (overridable for tests
        and for a relocated home). If the directory can't be created the log is
        born degraded — no path — and every :meth:`event` is a silent no-op,
        but the console still runs.
        """
        started = datetime.now(timezone.utc)
        directory = log_dir or _default_log_dir()
        path: Path | None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{started.strftime('%Y%m%dT%H%M%S')}.log"
        except OSError:
            path = None
        log = cls(path)
        log.event(
            "session-start",
            identity=identity or "(unresolved)",
            programs=list(programs) if programs else [],
            log=str(path) if path else "(unavailable)",
        )
        return log

    def event(self, kind: str, **fields: object) -> None:
        """Append one timestamped JSONL record. Never raises."""
        if self.path is None:
            return
        try:
            record = {"ts": _stamp(), "event": kind, **fields}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except (OSError, TypeError, ValueError):
            self.degraded = True


def _notify(app: object, message: str, *, error: bool) -> None:
    """Fire the loud channel — the notification toast. Never re-raises: notify
    is the last line of defense, so a broken notify can't itself go silent."""
    try:
        app.notify(  # type: ignore[attr-defined]
            message,
            severity="error" if error else "information",
            timeout=_ERROR_TIMEOUT if error else _OK_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 - notify is the loud channel; degrade quietly
        pass


def run_decision_action(app: object, *, action: str, target: str, fn) -> tuple[bool, str]:
    """Run a console decision action LOUDLY and JOURNALED — the silent-loss net.

    Records intent before the write and the outcome after, into the app's
    session log (``app.session_log``, absent → no-op). Any exception *fn* raises
    is CAUGHT, journaled with its traceback, and turned into a loud error
    notification plus a ``(False, message)`` return — it can never vanish into
    Textual's message pump the way an unguarded dismiss-callback exception does
    (the silent-approval-loss root cause). A clean ``(False, msg)`` result is
    surfaced just as loudly as success is surfaced quietly. Returns
    ``(ok, message)``.
    """
    log = getattr(app, "session_log", None)
    if log is not None:
        log.event("action-intent", action=action, target=target)
    try:
        ok, msg = fn()
    except Exception as exc:  # noqa: BLE001 - the whole point: nothing escapes silently
        if log is not None:
            log.event(
                "action-exception",
                action=action,
                target=target,
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
        message = f"{action} FAILED — {type(exc).__name__}: {exc} (see console log)"
        _notify(app, message, error=True)
        return False, message
    ok = bool(ok)
    if log is not None:
        log.event("action-result", action=action, target=target, ok=ok, message=msg)
    _notify(app, msg, error=not ok)
    return ok, msg


__all__ = ["ConsoleLog", "run_decision_action"]
