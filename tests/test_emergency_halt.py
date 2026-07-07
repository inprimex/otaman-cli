"""Tests for `otaman emergency-halt` (F012 security fix, new command).

Broadcasts a PRIVILEGED `emergency-halt` bus message (`to: all`, asserts
`from: human`), gated on `confirm_human_decision` (real TTY + typed
phrase, no --yes/scripted bypass). Previously this type had no dedicated
producer at all -- the only path was the general `otaman send`, which let
any caller claim `from: human` unconditionally.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.commands.emergency_halt import cmd_emergency_halt


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / "platform.yaml").write_text("project: tst\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _broadcast_files(project: Path) -> list[Path]:
    return [
        f for f in (project / ".agents" / "bus" / "active").glob("*.md")
        if "emergency-halt" in f.name
    ]


class TestEmergencyHalt:
    def test_requires_reason(self, project: Path):
        rc = cmd_emergency_halt([])
        assert rc == 2
        assert _broadcast_files(project) == []

    def test_non_tty_refuses_and_writes_nothing(self, project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
            rc = cmd_emergency_halt(["--reason", "runaway agent"])
        assert rc != 0
        assert _broadcast_files(project) == []

    def test_tty_wrong_phrase_refuses(self, project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="yes"):
            rc = cmd_emergency_halt(["--reason", "runaway agent"])
        assert rc != 0
        assert _broadcast_files(project) == []

    def test_tty_correct_phrase_broadcasts(self, project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="CONFIRM"):
            rc = cmd_emergency_halt(["--reason", "runaway agent writing garbage"])
        assert rc == 0
        files = _broadcast_files(project)
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "from: human" in content
        assert "to: all" in content
        assert "type: emergency-halt" in content
        assert "priority: urgent" in content
        assert "runaway agent writing garbage" in content

    def test_not_in_project_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)  # no platform.yaml / .agents here
        rc = cmd_emergency_halt(["--reason", "x"])
        assert rc == 1
