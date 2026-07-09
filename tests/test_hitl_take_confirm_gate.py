"""Tests for `otaman hitl take`'s TTY confirmation gate (2026-07-09).

`otaman hitl take <id>` produces a PRIVILEGED bus message (`human-decision`,
asserts `from: human`) via input()-based prompts with no TTY check -- same
forgery class as F012's pre-fix `otaman approve`. Now gated on
`safety.require_interactive_tty` before any prompt runs (see
test_hitl_commands.py's subprocess-level regression test for the
non-interactive-stdin-is-refused case; these tests exercise the TTY-true
success path and the wrong-shape-input edge case at the unit level, mocking
`builtins.input` directly, mirroring test_approve_human_confirm_gate.py).
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.hitl.commands import cmd_take


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / "platform.yaml").write_text("project: tst\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTAMAN_AGENT", "human")
    return tmp_path


def _stage_request(project: Path, stem: str) -> Path:
    msg = project / ".agents" / "bus" / "active" / f"{stem}.md"
    msg.write_text(
        "---\n"
        f"id: {stem}\n"
        "from: bridge-agent\n"
        "to: human\n"
        "priority: normal\n"
        "type: request-human-review\n"
        "timestamp: 2026-07-09T00:00:00Z\n"
        "status: pending\n"
        "session-id: sess-xyz\n"
        "decision-type: approve-reject\n"
        "---\n\n"
        "## Subject: Approve widget rollout?\n\nbody\n",
        encoding="utf-8",
    )
    return msg


class TestHitlTakeConfirmGate:
    def test_non_tty_refuses_and_writes_nothing(self, project: Path):
        stem = "20260709T000000-bridge-agent-to-human-request-human-review-x"
        _stage_request(project, stem)
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False), \
             mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            rc = cmd_take({"id": stem})
        assert rc != 0
        active = project / ".agents" / "bus" / "active"
        assert [f for f in active.glob("*human-decision*") if f.is_file()] == []
        assert not (active / "acks" / f"{stem}.human.ack").exists()

    def test_tty_succeeds_and_records_decision(self, project: Path):
        stem = "20260709T000000-bridge-agent-to-human-request-human-review-x"
        _stage_request(project, stem)
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["approve", "", "", ""]):
            rc = cmd_take({"id": stem})
        assert rc == 0
        active = project / ".agents" / "bus" / "active"
        decisions = [f for f in active.glob("*human-decision*") if f.is_file()]
        assert len(decisions) == 1
        text = decisions[0].read_text(encoding="utf-8")
        assert "type: human-decision" in text
        assert "from: human" in text
        assert "decision: approve" in text
        ack = active / "acks" / f"{stem}.human.ack"
        assert ack.is_file()
        assert ack.read_text(encoding="utf-8").strip() == "resolved"

    def test_no_matching_request_bails_before_tty_check(self, project: Path):
        """Missing target should error from the lookup, without even
        reaching (or needing) the TTY gate."""
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", side_effect=AssertionError("must not check tty")):
            rc = cmd_take({"id": "no-such-stem"})
        assert rc != 0
