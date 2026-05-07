"""Tests for scripts/doctor.py :: check_secrets_leaks."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


# doctor is now an importable package module — no path-based loading needed
from otaman_cli import doctor


def _git(path: Path, *args: str) -> None:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    subprocess.run(["git", "-C", str(path), *args], check=True, env=env,
                   capture_output=True)


@pytest.fixture
def maestro_git_repo(tmp_path):
    """Git-initialized maestro folder with .maestro/ subdir."""
    root = tmp_path / "my-maestro"
    root.mkdir()
    (root / ".maestro").mkdir()
    (root / "platform.yaml").write_text("project: test\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


class TestGitignoreCheck:
    def test_missing_gitignore_flagged(self, tmp_path):
        """No .gitignore at all → flagged as high-severity."""
        root = tmp_path / "no-gi"
        root.mkdir()
        result = doctor.check_secrets_leaks(root)
        assert result["status"] in ("warn", "fail")
        assert any(
            "gitignore" in i["issue"].lower()
            for i in result.get("issues", [])
        )

    def test_gitignore_without_entry_flagged(self, tmp_path):
        root = tmp_path / "partial-gi"
        root.mkdir()
        (root / ".gitignore").write_text(".agents/bus/\n", encoding="utf-8")
        result = doctor.check_secrets_leaks(root)
        assert any(".maestro/secrets.env" in i["issue"] for i in result.get("issues", []))

    def test_gitignore_with_entry_ok(self, tmp_path):
        root = tmp_path / "good-gi"
        root.mkdir()
        (root / ".gitignore").write_text(".maestro/secrets.env\n", encoding="utf-8")
        result = doctor.check_secrets_leaks(root)
        # No gitignore issue
        for i in result.get("issues", []):
            assert "gitignore" not in i["issue"].lower()


class TestGitHistoryCheck:
    def test_untracked_secrets_env_ok(self, maestro_git_repo):
        """secrets.env not in git at all → no leak issue."""
        (maestro_git_repo / ".maestro" / "secrets.env").write_text(
            "SECRET=value\n", encoding="utf-8",
        )
        (maestro_git_repo / ".gitignore").write_text(
            ".maestro/secrets.env\n", encoding="utf-8",
        )
        result = doctor.check_secrets_leaks(maestro_git_repo)
        for i in result.get("issues", []):
            assert "leak" not in i["issue"].lower()
            assert "secrets may leak" not in i["issue"]
            assert "past commit" not in i["issue"]

    def test_tracked_secrets_env_flagged_critical(self, maestro_git_repo):
        """If secrets.env is currently tracked, flag critical."""
        secrets = maestro_git_repo / ".maestro" / "secrets.env"
        secrets.write_text("LEAKED=token\n", encoding="utf-8")
        # Intentionally add + commit (simulating the mistake)
        _git(maestro_git_repo, "add", ".maestro/secrets.env")
        _git(maestro_git_repo, "commit", "-q", "-m", "oops")

        result = doctor.check_secrets_leaks(maestro_git_repo)
        assert result["status"] == "fail"
        critical = [i for i in result["issues"] if i["severity"] == "critical"]
        assert critical
        assert any("tracked in git" in i["issue"] for i in critical)

    def test_historic_secrets_env_flagged_critical(self, maestro_git_repo):
        """Even if removed, history retention triggers critical."""
        secrets = maestro_git_repo / ".maestro" / "secrets.env"
        secrets.write_text("LEAKED=token\n", encoding="utf-8")
        _git(maestro_git_repo, "add", ".maestro/secrets.env")
        _git(maestro_git_repo, "commit", "-q", "-m", "oops")
        _git(maestro_git_repo, "rm", "-q", ".maestro/secrets.env")
        _git(maestro_git_repo, "commit", "-q", "-m", "remove")

        result = doctor.check_secrets_leaks(maestro_git_repo)
        assert result["status"] == "fail"
        assert any(
            "past commit" in i["issue"] and i["severity"] == "critical"
            for i in result["issues"]
        )


class TestPermissionsCheck:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: chmod semantics don't apply on Windows",
    )
    def test_loose_mode_flagged_medium(self, tmp_path):
        root = tmp_path / "mode-check"
        root.mkdir()
        (root / ".maestro").mkdir()
        (root / ".gitignore").write_text(".maestro/secrets.env\n", encoding="utf-8")
        secrets = root / ".maestro" / "secrets.env"
        secrets.write_text("FOO=bar\n", encoding="utf-8")
        os.chmod(secrets, 0o644)

        result = doctor.check_secrets_leaks(root)
        mode_issues = [i for i in result.get("issues", []) if "mode" in i["issue"]]
        assert mode_issues
        assert mode_issues[0]["severity"] == "medium"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: chmod semantics don't apply on Windows",
    )
    def test_600_mode_ok(self, tmp_path):
        root = tmp_path / "good-mode"
        root.mkdir()
        (root / ".maestro").mkdir()
        (root / ".gitignore").write_text(".maestro/secrets.env\n", encoding="utf-8")
        secrets = root / ".maestro" / "secrets.env"
        secrets.write_text("FOO=bar\n", encoding="utf-8")
        os.chmod(secrets, 0o600)

        result = doctor.check_secrets_leaks(root)
        for i in result.get("issues", []):
            assert "mode" not in i["issue"]


class TestStatusAggregation:
    def test_clean_repo_returns_ok(self, maestro_git_repo):
        (maestro_git_repo / ".gitignore").write_text(
            ".maestro/secrets.env\n", encoding="utf-8",
        )
        result = doctor.check_secrets_leaks(maestro_git_repo)
        assert result["status"] == "ok"
        assert not result.get("issues")

    def test_warn_for_non_critical_only(self, tmp_path):
        """Only gitignore missing (high severity, not critical) → warn."""
        root = tmp_path / "warn-only"
        root.mkdir()
        result = doctor.check_secrets_leaks(root)
        assert result["status"] == "warn"

    def test_fail_when_critical_present(self, maestro_git_repo):
        secrets = maestro_git_repo / ".maestro" / "secrets.env"
        secrets.write_text("LEAKED=token\n", encoding="utf-8")
        _git(maestro_git_repo, "add", ".maestro/secrets.env")
        _git(maestro_git_repo, "commit", "-q", "-m", "oops")
        result = doctor.check_secrets_leaks(maestro_git_repo)
        assert result["status"] == "fail"
