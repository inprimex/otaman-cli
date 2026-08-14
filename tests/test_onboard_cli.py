"""Argparse wiring + end-to-end smoke for `otaman onboard <sub>`."""

from __future__ import annotations

import json

import pytest

from otaman_cli.onboard.cli import main as onboard_main


class TestArgparseWiring:
    def test_no_subcommand_errors(self, capsys):
        with pytest.raises(SystemExit):
            onboard_main([])

    def test_help(self, capsys):
        with pytest.raises(SystemExit):
            onboard_main(["--help"])
        out = capsys.readouterr().out
        assert "add-user" in out
        assert "list-users" in out
        assert "whoami" in out
        assert "doctor" in out

    def test_add_user_help_shows_role_flag(self, capsys):
        with pytest.raises(SystemExit):
            onboard_main(["add-user", "--help"])
        out = capsys.readouterr().out
        assert "--role" in out
        assert "--apply" in out
        assert "--state-dir" in out


class TestEndToEnd:
    def test_add_then_list(self, tmp_path, capsys):
        # add-user with --apply
        rc = onboard_main(
            [
                "add-user",
                "alice@example.com",
                "--role",
                "developer",
                "--state-dir",
                str(tmp_path),
                "--apply",
            ]
        )
        assert rc == 0
        capsys.readouterr()  # clear

        # list-users
        rc = onboard_main(["list-users", "--state-dir", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice@example.com" in out

    def test_add_user_dry_run_default(self, tmp_path, capsys):
        # No --apply → dry-run
        rc = onboard_main(
            [
                "add-user",
                "alice@example.com",
                "--role",
                "developer",
                "--state-dir",
                str(tmp_path),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        # list-users should be empty
        rc = onboard_main(["list-users", "--state-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert "no users registered" in out

    def test_add_user_then_doctor(self, tmp_path, capsys):
        onboard_main(
            [
                "add-user",
                "alice@example.com",
                "--role",
                "developer,approver",
                "--state-dir",
                str(tmp_path),
                "--apply",
            ]
        )
        capsys.readouterr()  # clear
        rc = onboard_main(["doctor", "--state-dir", str(tmp_path)])
        # WARN allowed; FAIL is exit 1
        assert rc == 0
        out = capsys.readouterr().out
        assert "summary:" in out

    def test_list_users_json(self, tmp_path, capsys):
        onboard_main(
            [
                "add-user",
                "alice@example.com",
                "--role",
                "developer",
                "--state-dir",
                str(tmp_path),
                "--apply",
            ]
        )
        capsys.readouterr()
        rc = onboard_main(["list-users", "--state-dir", str(tmp_path), "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed[0]["email"] == "alice@example.com"

    def test_whoami_unregistered(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("USER", "stranger")
        rc = onboard_main(["whoami", "--state-dir", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "stranger" in out
        assert "not registered" in out
