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


def run_decision_action(
    app: object, *, action: str, target: str, fn, described: str = ""
) -> tuple[bool, str]:
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
    if ok:
        # console-undo 1.2 — remember what `u` would reverse. Only a SUCCEEDED
        # action is undoable: offering to undo something that failed would be
        # offering to reverse work that never happened.
        from otaman_cli.console.undo import UndoableAction

        try:
            app.last_action = UndoableAction(  # type: ignore[attr-defined]
                action=action, target=target, described=described or target
            )
        except Exception:  # noqa: BLE001 - a host without the attribute is fine
            pass
    _notify(app, msg, error=not ok)
    return ok, msg


#: Journal events the session-actions view renders, mapped to how they read.
_ACTION_EVENTS = {
    "action-result": "",
    "action-exception": "FAILED",
    "undo": "undone",
    "undo-refused": "undo refused",
    "undo-failed": "undo FAILED",
}


def read_session_actions(path: Path | None, *, limit: int = 30) -> list[dict]:
    """The recent decision actions from *path*, newest last (console-undo 1.3).

    Reads the session's own JSONL log rather than keeping a second in-memory
    list: the log is already the record, and a parallel list would be one more
    thing that can disagree with it. A malformed line is SKIPPED, not fatal —
    this view exists to answer "what did I just do" and must work when something
    else has gone wrong.
    """
    if path is None or not path.is_file():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("event") not in _ACTION_EVENTS:
            continue
        rows.append(record)
    return rows[-limit:]


def describe_action_row(record: dict) -> tuple[str, str, str, str]:
    """``(time, action, target, outcome)`` for one journal record."""
    event = str(record.get("event") or "")
    ts = str(record.get("ts") or "")[11:19]  # HH:MM:SS from the ISO stamp
    action = str(record.get("action") or "?")
    target = str(record.get("target") or "?")
    if event == "action-result":
        outcome = "ok" if record.get("ok") else f"refused — {record.get('message') or ''}"
    elif event == "action-exception":
        outcome = f"FAILED — {record.get('error') or ''}"
    else:
        outcome = _ACTION_EVENTS.get(event, event)
    return ts, action, target, outcome.strip()


__all__ = [
    "ConsoleLog",
    "describe_action_row",
    "read_session_actions",
    "run_decision_action",
]
