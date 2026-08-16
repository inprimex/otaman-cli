"""Tests for `otaman approve`'s F012 human-confirmation gate.

`otaman approve approve|reject` produces a PRIVILEGED bus message
(`spec-change-approved`/`-rejected`, asserts `from: human`) — before this
fix it wrote the ack + broadcast immediately with no confirmation at all,
so any Bash-tool-driven agent session could forge a human decision simply
by shelling out to `otaman approve approve <stem>`. Now gated on
`confirm_human_decision` (real TTY + typed phrase, no bypass).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.commands.approve import cmd_approve


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text("project: tst\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Resolve this fixture tree (walk-up from cwd), not the isolate_bus sandbox.
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    return tmp_path


def _stage_pending_request(
    project: Path, stem: str = "20260101T000000-cli-agent-to-human-spec-change-request"
) -> Path:
    msg = project / ".agents" / "bus" / "active" / f"{stem}.md"
    msg.write_text(
        "---\n"
        "id: req-1\n"
        "from: cli-agent\n"
        "to: human\n"
        "priority: normal\n"
        "type: spec-change-request\n"
        "timestamp: 2026-01-01T00:00:00Z\n"
        "status: pending\n"
        "---\n\n"
        "## Subject: Spec change request: widget support\n\nbody\n",
        encoding="utf-8",
    )
    return msg


class TestApproveConfirmGate:
    def test_non_tty_refuses_and_writes_nothing(self, project: Path):
        stem = "20260101T000000-cli-agent-to-human-spec-change-request"
        _stage_pending_request(project, stem)
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
            rc = cmd_approve(["approve", stem])
        assert rc != 0
        ack_dir = project / ".agents" / "bus" / "active" / "acks"
        assert list(ack_dir.glob("*.ack")) == []
        broadcast_files = [
            f
            for f in (project / ".agents" / "bus" / "active").glob("*.md")
            if "spec-change-approved" in f.name
        ]
        assert broadcast_files == []

    def test_tty_wrong_phrase_refuses_and_writes_nothing(self, project: Path):
        stem = "20260101T000000-cli-agent-to-human-spec-change-request"
        _stage_pending_request(project, stem)
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="yes"),
        ):
            rc = cmd_approve(["approve", stem])
        assert rc != 0
        ack_dir = project / ".agents" / "bus" / "active" / "acks"
        assert list(ack_dir.glob("*.ack")) == []

    def test_tty_correct_phrase_approves(self, project: Path):
        stem = "20260101T000000-cli-agent-to-human-spec-change-request"
        _stage_pending_request(project, stem)
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="CONFIRM"),
        ):
            rc = cmd_approve(["approve", stem])
        assert rc == 0
        ack_file = project / ".agents" / "bus" / "active" / "acks" / f"{stem}.human.ack"
        assert ack_file.is_file()
        assert ack_file.read_text(encoding="utf-8").strip() == "approved"
        broadcast_files = [
            f
            for f in (project / ".agents" / "bus" / "active").glob("*.md")
            if "spec-change-approved" in f.name
        ]
        assert len(broadcast_files) == 1
        content = broadcast_files[0].read_text(encoding="utf-8")
        assert "from: human" in content
        assert "type: spec-change-approved" in content

    def test_tty_correct_phrase_rejects(self, project: Path):
        stem = "20260101T000000-cli-agent-to-human-spec-change-request"
        _stage_pending_request(project, stem)
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="CONFIRM"),
        ):
            rc = cmd_approve(["reject", stem, "-d", "not now"])
        assert rc == 0
        ack_file = project / ".agents" / "bus" / "active" / "acks" / f"{stem}.human.ack"
        assert ack_file.is_file()
        assert ack_file.read_text(encoding="utf-8").strip() == "rejected"
        reject_files = [
            f
            for f in (project / ".agents" / "bus" / "active").glob("*.md")
            if "spec-change-rejected" in f.name
        ]
        assert len(reject_files) == 1
        content = reject_files[0].read_text(encoding="utf-8")
        assert "from: human" in content
        assert "type: spec-change-rejected" in content

    def test_list_action_needs_no_confirmation(self, project: Path):
        """Read-only listing must not be gated -- only approve/reject write."""
        stem = "20260101T000000-cli-agent-to-human-spec-change-request"
        _stage_pending_request(project, stem)
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False),
            mock.patch("builtins.input", side_effect=AssertionError("must not prompt")),
        ):
            rc = cmd_approve(["list"])
        assert rc == 0
