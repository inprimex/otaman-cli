"""Tests for destructive-command-safety tasks 1.1, 1.2, 1.5.

Covers:
- confirm_destructive_operation: yes bypasses prompt, non-TTY refuses
  without --yes, TTY prompts and honors the answer
- otaman migrate: identity echo, --dry-run makes zero writes, non-TTY
  without --yes refuses (subprocess, naturally has no TTY), --yes
  proceeds

Triggered by the 2026-07-04 `otaman migrate` incident. See
openspec/changes/destructive-command-safety/ for the full spec.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.safety import confirm_destructive_operation


# ---------------------------------------------------------------------------
# Task 1.1 — confirm_destructive_operation


class TestConfirmDestructiveOperation:

    def test_yes_bypasses_prompt_without_reading_stdin(self):
        with mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            result = confirm_destructive_operation("desc", "/some/path", yes=True)
        assert result is True

    def test_non_tty_without_yes_refuses(self, capsys):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
            result = confirm_destructive_operation("desc", "/some/path", yes=False)
        assert result is False
        captured = capsys.readouterr()
        assert "--yes" in (captured.out + captured.err)

    def test_tty_prompts_and_accepts_y(self):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="y"):
            result = confirm_destructive_operation("desc", "/some/path", yes=False)
        assert result is True

    def test_tty_prompts_and_rejects_other_answers(self):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            result = confirm_destructive_operation("desc", "/some/path", yes=False)
        assert result is False

    def test_tty_empty_answer_defaults_to_refuse(self):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value=""):
            result = confirm_destructive_operation("desc", "/some/path", yes=False)
        assert result is False

    def test_eof_refuses(self):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=EOFError):
            result = confirm_destructive_operation("desc", "/some/path", yes=False)
        assert result is False

    def test_echoes_description_and_targets(self, capsys):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            confirm_destructive_operation("Moving stuff", ["/a", "/b"], yes=False)
        output = capsys.readouterr().out
        assert "Moving stuff" in output
        assert "/a" in output
        assert "/b" in output

    def test_accepts_a_single_string_target(self, capsys):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            confirm_destructive_operation("desc", "/single/path", yes=False)
        assert "/single/path" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Task 1.2 — otaman migrate


def _setup_migrate_project(tmp_path: Path) -> Path:
    """Minimal legacy-layout otaman project: platform.yaml at tmp_path root,
    no .git/ (not yet migrated)."""
    (tmp_path / "platform.yaml").write_text(
        "project: sampleproj\nrepos:\n  - name: svc\n    path: ../svc\n    owner: cli-agent\n",
        encoding="utf-8",
    )
    (tmp_path / ".agents").mkdir()
    return tmp_path


@pytest.fixture
def migrate_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _setup_migrate_project(tmp_path)
    monkeypatch.chdir(root)
    return root


class TestMigrateDryRun:

    def test_dry_run_makes_zero_writes(self, migrate_project: Path):
        from otaman_cli.commands.migrate import cmd_migrate

        before_entries = set(migrate_project.iterdir())
        rc = cmd_migrate(["--dry-run"])
        assert rc == 0
        after_entries = set(migrate_project.iterdir())
        assert before_entries == after_entries, "dry-run must not create/move/delete anything"
        # platform.yaml itself untouched
        assert (migrate_project / "platform.yaml").is_file()

    def test_dry_run_echoes_identity(self, migrate_project: Path, capsys):
        from otaman_cli.commands.migrate import cmd_migrate

        cmd_migrate(["--dry-run"])
        output = capsys.readouterr().out
        assert "sampleproj" in output
        assert "1" in output  # repo count

    def test_dry_run_reports_planned_moves(self, migrate_project: Path, capsys):
        from otaman_cli.commands.migrate import cmd_migrate

        cmd_migrate(["--dry-run"])
        output = capsys.readouterr().out
        assert "would move" in output.lower()
        assert "would git init" in output.lower() or "git init" in output.lower()

    def test_dry_run_reports_mcp_json_and_claude_md_when_present(self, migrate_project: Path, capsys):
        (migrate_project / ".mcp.json").write_text("{}\n", encoding="utf-8")
        (migrate_project / "CLAUDE.md").write_text("# Otaman Agent Context\n", encoding="utf-8")
        from otaman_cli.commands.migrate import cmd_migrate

        cmd_migrate(["--dry-run"])
        output = capsys.readouterr().out
        assert "would move .mcp.json" in output.lower()
        assert "would move claude.md" in output.lower()


class TestMigrateConfirmGate:

    def test_non_interactive_without_yes_refuses(self, migrate_project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
            from otaman_cli.commands.migrate import cmd_migrate
            rc = cmd_migrate([])
        assert rc != 0
        # Nothing moved
        assert (migrate_project / "platform.yaml").is_file()
        assert not (migrate_project / "sampleproj-otaman").exists()

    def test_yes_flag_bypasses_prompt(self, migrate_project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False), \
             mock.patch("subprocess.run") as mock_run:
            from otaman_cli.commands.migrate import cmd_migrate
            rc = cmd_migrate(["--yes"])
        assert rc == 0
        maestro_dir = migrate_project / "sampleproj-otaman"
        assert maestro_dir.is_dir()
        assert (maestro_dir / "platform.yaml").is_file()
        assert not (migrate_project / "platform.yaml").exists()
        mock_run.assert_called()  # git init/add/commit invoked

    def test_yes_flag_also_moves_mcp_json_and_claude_md(self, migrate_project: Path):
        """uniform-ce-directory-layout 1.6b: .mcp.json and CLAUDE.md must land
        inside the dedicated otaman folder too, matching ce-bootstrap.sh's
        fresh-install scaffold (PROGRAM_OTAMAN_DIR contents) exactly."""
        (migrate_project / ".mcp.json").write_text("{}\n", encoding="utf-8")
        (migrate_project / "CLAUDE.md").write_text("# Otaman Agent Context\n", encoding="utf-8")
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False), \
             mock.patch("subprocess.run"):
            from otaman_cli.commands.migrate import cmd_migrate
            rc = cmd_migrate(["--yes"])
        assert rc == 0
        maestro_dir = migrate_project / "sampleproj-otaman"
        assert (maestro_dir / ".mcp.json").is_file()
        assert (maestro_dir / "CLAUDE.md").is_file()
        assert not (migrate_project / ".mcp.json").exists()
        assert not (migrate_project / "CLAUDE.md").exists()

    def test_interactive_decline_makes_zero_writes(self, migrate_project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            from otaman_cli.commands.migrate import cmd_migrate
            rc = cmd_migrate([])
        assert rc != 0
        assert (migrate_project / "platform.yaml").is_file()
        assert not (migrate_project / "sampleproj-otaman").exists()

    def test_interactive_accept_proceeds(self, migrate_project: Path):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="y"), \
             mock.patch("subprocess.run"):
            from otaman_cli.commands.migrate import cmd_migrate
            rc = cmd_migrate([])
        assert rc == 0
        assert (migrate_project / "sampleproj-otaman").is_dir()
