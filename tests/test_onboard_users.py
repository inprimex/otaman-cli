"""Tests for the users subcommand handlers (add-user, list-users, whoami)."""

from __future__ import annotations

import argparse
import json

from otaman_cli.onboard.state import User, load_users, save_users
from otaman_cli.onboard.users import (
    _parse_roles,
    cmd_add_user,
    cmd_list_users,
    cmd_whoami,
)


def _args(**kw) -> argparse.Namespace:
    """Build an argparse Namespace with the fields cmd_add_user expects."""
    defaults = dict(
        email="alice@example.com",
        role="developer",
        display_name=None,
        unix_user=None,
        telegram_id=None,
        state_dir=None,
        apply=True,
        json=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestParseRoles:
    def test_single_bare_name_gets_prefix(self):
        assert _parse_roles("developer") == ["otaman:developer"]

    def test_fully_qualified_passes_through(self):
        assert _parse_roles("otaman:developer") == ["otaman:developer"]

    def test_mixed_csv(self):
        result = _parse_roles("developer,otaman:approver")
        assert result == ["otaman:developer", "otaman:approver"]

    def test_whitespace_trimmed(self):
        assert _parse_roles("  developer ,  approver  ") == [
            "otaman:developer",
            "otaman:approver",
        ]

    def test_empty_pieces_skipped(self):
        assert _parse_roles("developer,,") == ["otaman:developer"]


class TestAddUser:
    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        rc = cmd_add_user(_args(state_dir=str(tmp_path), apply=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        # No users.yaml written
        assert load_users(tmp_path) == []

    def test_apply_persists(self, tmp_path):
        rc = cmd_add_user(_args(state_dir=str(tmp_path), apply=True))
        assert rc == 0
        users = load_users(tmp_path)
        assert len(users) == 1
        assert users[0].email == "alice@example.com"
        assert users[0].roles == ["otaman:developer"]

    def test_idempotent_re_add(self, tmp_path, capsys):
        cmd_add_user(_args(state_dir=str(tmp_path), apply=True))
        capsys.readouterr()  # clear
        rc = cmd_add_user(_args(state_dir=str(tmp_path), apply=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already present" in out
        users = load_users(tmp_path)
        assert len(users) == 1

    def test_conflicting_re_add_errors(self, tmp_path, capsys):
        cmd_add_user(_args(state_dir=str(tmp_path), role="developer", apply=True))
        rc = cmd_add_user(_args(state_dir=str(tmp_path), role="admin", apply=True))
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists with different fields" in err

    def test_invalid_email_returns_2(self, tmp_path, capsys):
        rc = cmd_add_user(_args(state_dir=str(tmp_path), email="not-an-email"))
        assert rc == 2
        err = capsys.readouterr().err
        assert "not a valid email" in err

    def test_invalid_role_returns_2(self, tmp_path, capsys):
        rc = cmd_add_user(_args(state_dir=str(tmp_path), role="wizard"))
        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown role" in err

    def test_emits_audit_event_on_success(self, tmp_path):
        cmd_add_user(_args(state_dir=str(tmp_path), apply=True))
        audit_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert len(audit_files) == 1
        lines = audit_files[0].read_text(encoding="utf-8").splitlines()
        types = [json.loads(line)["type"] for line in lines]
        assert "otaman.onboard.user_added" in types

    def test_emits_audit_event_on_failure(self, tmp_path):
        cmd_add_user(_args(state_dir=str(tmp_path), email="bad-email"))
        audit_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert len(audit_files) == 1
        lines = audit_files[0].read_text(encoding="utf-8").splitlines()
        types = [json.loads(line)["type"] for line in lines]
        assert "otaman.onboard.user_add_failed" in types

    def test_display_name_defaults_to_email_local_part(self, tmp_path):
        cmd_add_user(_args(state_dir=str(tmp_path), apply=True))
        users = load_users(tmp_path)
        assert users[0].display_name == "alice"

    def test_custom_display_name_used(self, tmp_path):
        cmd_add_user(_args(state_dir=str(tmp_path), display_name="Alice Engineer", apply=True))
        users = load_users(tmp_path)
        assert users[0].display_name == "Alice Engineer"


class TestListUsers:
    def test_empty_state(self, tmp_path, capsys):
        rc = cmd_list_users(_args(state_dir=str(tmp_path), json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "no users registered" in out

    def test_human_format(self, tmp_path, capsys):
        save_users(
            [
                User(email="alice@example.com", display_name="Alice", roles=["otaman:developer"]),
                User(email="bob@example.com", display_name="Bob", roles=["otaman:viewer"]),
            ],
            tmp_path,
        )
        rc = cmd_list_users(_args(state_dir=str(tmp_path), json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice@example.com" in out
        assert "bob@example.com" in out
        assert "otaman:developer" in out

    def test_json_format(self, tmp_path, capsys):
        save_users(
            [
                User(email="alice@example.com", display_name="Alice", roles=["otaman:developer"]),
            ],
            tmp_path,
        )
        rc = cmd_list_users(_args(state_dir=str(tmp_path), json=True))
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert parsed[0]["email"] == "alice@example.com"


class TestWhoami:
    def test_unregistered_user(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("USER", "stranger")
        rc = cmd_whoami(_args(state_dir=str(tmp_path)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "unix_user: stranger" in out
        assert "not registered" in out

    def test_registered_user_by_unix_user(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("USER", "alice")
        save_users(
            [
                User(
                    email="alice@example.com",
                    display_name="Alice",
                    roles=["otaman:developer"],
                    unix_user="alice",
                ),
            ],
            tmp_path,
        )
        rc = cmd_whoami(_args(state_dir=str(tmp_path)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice@example.com" in out
        assert "otaman:developer" in out

    def test_registered_user_by_email_local_part(self, tmp_path, monkeypatch, capsys):
        """When unix_user isn't set, match falls back to email local part."""
        monkeypatch.setenv("USER", "alice")
        save_users(
            [
                User(email="alice@example.com", display_name="Alice", roles=["otaman:developer"]),
            ],
            tmp_path,
        )
        rc = cmd_whoami(_args(state_dir=str(tmp_path)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice@example.com" in out
