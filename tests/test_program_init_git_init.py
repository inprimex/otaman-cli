"""Tests for git_init.py — git repo initialization + initial commit."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from otaman_cli.onboard.program_init.git_init import (
    create_gitignore,
    ensure_git_repo,
    initial_commit,
)


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


_SKIP_NO_GIT = pytest.mark.skipif(not _git_available(), reason="git not available")


class TestEnsureGitRepo:
    @_SKIP_NO_GIT
    def test_inits_new_repo(self, tmp_path):
        repo = tmp_path / "my-specs"
        repo.mkdir()
        err = ensure_git_repo(repo)
        assert err is None
        assert (repo / ".git").is_dir()

    @_SKIP_NO_GIT
    def test_noop_on_existing_repo(self, tmp_path):
        repo = tmp_path / "existing"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        err = ensure_git_repo(repo)
        assert err is None  # should not error

    def test_returns_error_when_git_missing(self, tmp_path):
        repo = tmp_path / "specs"
        repo.mkdir()
        with patch(
            "otaman_cli.onboard.program_init.git_init.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            err = ensure_git_repo(repo)
        assert err is not None
        assert "git not found" in err


class TestCreateGitignore:
    def test_creates_gitignore(self, tmp_path):
        create_gitignore(tmp_path)
        gi = tmp_path / ".gitignore"
        assert gi.is_file()
        content = gi.read_text()
        assert ".init-state.yaml" in content

    def test_does_not_overwrite_existing(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("# custom content\n")
        create_gitignore(tmp_path)
        assert gi.read_text() == "# custom content\n"


class TestInitialCommit:
    @_SKIP_NO_GIT
    def test_creates_initial_commit(self, tmp_path):
        repo = tmp_path / "specs"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo, capture_output=True,
        )

        (repo / "platform.yaml").write_text("project: test\n")
        answers = {"processes": ["outcomes"], "roles": ["CTO"], "active_edition": "ce", "mode": 1}
        err = initial_commit(repo, "test-app", answers)
        assert err is None

        rc = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=repo, capture_output=True, text=True,
        )
        assert "test-app" in rc.stdout or "init" in rc.stdout

    @_SKIP_NO_GIT
    def test_noop_if_commits_exist(self, tmp_path):
        repo = tmp_path / "existing"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "README.md").write_text("# existing")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "existing"], cwd=repo, capture_output=True)

        answers = {"processes": [], "roles": [], "active_edition": "ce", "mode": 1}
        err = initial_commit(repo, "existing-app", answers)
        assert err is None  # should succeed without adding another commit
