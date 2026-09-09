"""Console observability — the silent-approval-loss defect fix.

The 2026-09-09 incident: a spec-approve in ``otaman -i`` left ZERO trace (no
write, no commit, no broadcast, no error) because an exception in the decision
action vanished into Textual's message pump — the ``notify`` after the call was
never reached, and Roman's seat has no tmux scrollback to recover the stack.

These are the Textual-free tests for the two nets: the per-session LOG FILE and
:func:`run_decision_action`. The loud-not-silent test is the one that was RED
before the wrapper existed (the exception used to escape uncaught).
"""

from __future__ import annotations

import json

from otaman_cli.console.journal import ConsoleLog, run_decision_action


class _FakeApp:
    """Stand-in for OtamanConsole: records notifications, carries a session_log."""

    def __init__(self, session_log=None) -> None:
        self.session_log = session_log
        self.notes: list[tuple[str, str]] = []  # (severity, message)

    def notify(self, message, *, severity="information", timeout=None) -> None:
        self.notes.append((severity, message))


def _records(log: ConsoleLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text("utf-8").splitlines()]


# ---------------------------------------------------------------------------
# ConsoleLog


def test_open_writes_session_start(tmp_path):
    log = ConsoleLog.open(log_dir=tmp_path / "logs", identity="roman (cto)", programs=["demo"])
    assert log.path is not None and log.path.exists()
    (rec,) = _records(log)
    assert rec["event"] == "session-start"
    assert rec["identity"] == "roman (cto)"
    assert rec["programs"] == ["demo"]


def test_event_appends_jsonl(tmp_path):
    log = ConsoleLog.open(log_dir=tmp_path / "logs")
    log.event("screen", to="LifecycleScreen")
    log.event("action-result", action="ratify", target="x", ok=True)
    recs = _records(log)
    assert [r["event"] for r in recs] == ["session-start", "screen", "action-result"]
    assert recs[1]["to"] == "LifecycleScreen"
    assert "ts" in recs[2]  # every record is timestamped


def test_log_degrades_when_dir_unwritable(tmp_path):
    # log_dir path is blocked by an existing FILE of the same name → mkdir fails.
    blocker = tmp_path / "logs"
    blocker.write_text("i am a file, not a dir\n", encoding="utf-8")
    log = ConsoleLog.open(log_dir=blocker)
    assert log.path is None and log.degraded is True
    log.event("action-intent", action="approve", target="x")  # no-op, must not raise


# ---------------------------------------------------------------------------
# run_decision_action


def test_success_notifies_info_and_journals_result(tmp_path):
    app = _FakeApp(ConsoleLog.open(log_dir=tmp_path / "logs"))
    ok, msg = run_decision_action(app, action="approve", target="c", fn=lambda: (True, "done"))
    assert ok is True and msg == "done"
    assert app.notes == [("information", "done")]
    recs = _records(app.session_log)
    kinds = [r["event"] for r in recs]
    assert "action-intent" in kinds and "action-result" in kinds
    assert recs[-1]["ok"] is True


def test_clean_failure_notifies_error(tmp_path):
    app = _FakeApp(ConsoleLog.open(log_dir=tmp_path / "logs"))
    ok, msg = run_decision_action(
        app, action="approve", target="c", fn=lambda: (False, "gate refused")
    )
    assert ok is False and msg == "gate refused"
    assert app.notes == [("error", "gate refused")]
    assert _records(app.session_log)[-1] == {
        **_records(app.session_log)[-1],
        "ok": False,
    }  # result recorded, ok False


def test_exception_is_loud_not_silent(tmp_path):
    # THE regression: before the wrapper, this exception escaped uncaught and
    # the decision left no trace. Now it must be caught, journaled WITH its
    # traceback, and surfaced as a loud error — never swallowed.
    app = _FakeApp(ConsoleLog.open(log_dir=tmp_path / "logs"))

    def boom():
        raise RuntimeError("openspec yaml mid-rewrite")

    ok, msg = run_decision_action(app, action="approve (spec-approved)", target="cux", fn=boom)
    assert ok is False
    assert "FAILED" in msg and "RuntimeError" in msg and "console log" in msg
    (severity, _) = app.notes[-1]
    assert severity == "error"  # loud
    exc = [r for r in _records(app.session_log) if r["event"] == "action-exception"]
    assert exc and "RuntimeError" in exc[0]["error"]
    assert "openspec yaml mid-rewrite" in exc[0]["traceback"]  # full trace preserved


def test_works_without_a_session_log():
    # Journal absent (log born degraded) → still notifies, never raises.
    app = _FakeApp(session_log=None)
    ok, _ = run_decision_action(app, action="nudge", target="c", fn=lambda: (True, "ok"))
    assert ok is True and app.notes == [("information", "ok")]

    ok, _ = run_decision_action(app, action="nudge", target="c", fn=lambda: 1 / 0)
    assert ok is False and app.notes[-1][0] == "error"  # exception still loud
