"""Tests for the onboard doctor diagnostic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otaman_cli.onboard.doctor import (
    _check_audit_writable,
    _check_duplicate_emails,
    _check_each_user_valid,
    _check_state_dir_exists,
    _check_users_yaml_parseable,
    cmd_doctor,
    run_doctor,
)
from otaman_cli.onboard.state import User, save_users


def _doctor_args(state_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(state_dir=str(state_dir))


class TestStateDirExists:
    def test_missing_dir_is_warn(self, tmp_path):
        ghost = tmp_path / "ghost"
        result = _check_state_dir_exists(ghost)
        assert result.status == "WARN"

    def test_existing_dir_is_ok(self, tmp_path):
        result = _check_state_dir_exists(tmp_path)
        assert result.status == "OK"


class TestUsersYamlParseable:
    def test_missing_file_is_warn(self, tmp_path):
        result = _check_users_yaml_parseable(tmp_path)
        assert result.status == "WARN"
        assert "does not exist" in result.detail

    def test_valid_file_is_ok(self, tmp_path):
        save_users(
            [
                User(email="a@x.com", display_name="A", roles=["otaman:developer"]),
            ],
            tmp_path,
        )
        result = _check_users_yaml_parseable(tmp_path)
        assert result.status == "OK"
        assert "1 user(s)" in result.detail

    def test_malformed_file_is_fail(self, tmp_path):
        (tmp_path / "users.yaml").write_text("not: valid: yaml: :::")
        result = _check_users_yaml_parseable(tmp_path)
        assert result.status == "FAIL"


class TestEachUserValid:
    def test_all_valid(self, tmp_path):
        save_users(
            [
                User(email="a@x.com", display_name="A", roles=["otaman:developer"]),
                User(email="b@x.com", display_name="B", roles=["otaman:viewer"]),
            ],
            tmp_path,
        )
        results = _check_each_user_valid(tmp_path)
        assert all(r.status == "OK" for r in results)
        assert len(results) == 2


class TestDuplicateEmails:
    def test_no_duplicates(self, tmp_path):
        save_users(
            [
                User(email="a@x.com", display_name="A", roles=["otaman:developer"]),
                User(email="b@x.com", display_name="B", roles=["otaman:viewer"]),
            ],
            tmp_path,
        )
        result = _check_duplicate_emails(tmp_path)
        assert result.status == "OK"

    def test_with_duplicate(self, tmp_path):
        # Hand-write duplicate (bypasses upsert_user's check)
        (tmp_path / "users.yaml").write_text(
            "users:\n"
            "  - email: a@x.com\n    display_name: A\n    roles: [otaman:developer]\n"
            "  - email: a@x.com\n    display_name: A2\n    roles: [otaman:viewer]\n"
        )
        result = _check_duplicate_emails(tmp_path)
        assert result.status == "FAIL"
        assert "a@x.com" in result.detail


class TestAuditWritable:
    def test_missing_dir_is_warn(self, tmp_path):
        result = _check_audit_writable(tmp_path)
        assert result.status == "WARN"

    def test_existing_writable_dir_is_ok(self, tmp_path):
        (tmp_path / "audit").mkdir()
        result = _check_audit_writable(tmp_path)
        assert result.status == "OK"


class TestRunDoctor:
    def test_clean_state(self, tmp_path):
        save_users(
            [
                User(email="a@x.com", display_name="A", roles=["otaman:developer"]),
            ],
            tmp_path,
        )
        (tmp_path / "audit").mkdir()
        results = run_doctor(tmp_path)
        statuses = [r.status for r in results]
        assert "FAIL" not in statuses

    def test_fail_counts_non_zero(self, tmp_path):
        # Force a fail
        (tmp_path / "users.yaml").write_text("not: valid: yaml: :::")
        results = run_doctor(tmp_path)
        fail_count = sum(1 for r in results if r.status == "FAIL")
        assert fail_count > 0


class TestCmdDoctor:
    def test_clean_state_returns_0(self, tmp_path, capsys):
        save_users(
            [
                User(email="a@x.com", display_name="A", roles=["otaman:developer"]),
            ],
            tmp_path,
        )
        (tmp_path / "audit").mkdir()
        rc = cmd_doctor(_doctor_args(tmp_path))
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" in out
        # Doctor emits a run event
        audit_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert audit_files
        types = [
            json.loads(line)["type"]
            for f in audit_files
            for line in f.read_text(encoding="utf-8").splitlines()
        ]
        assert "otaman.onboard.doctor_run" in types

    def test_failure_state_returns_1(self, tmp_path, capsys):
        (tmp_path / "users.yaml").write_text("not: valid: yaml: :::")
        rc = cmd_doctor(_doctor_args(tmp_path))
        assert rc == 1
