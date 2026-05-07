"""Tests for scripts/doctor.py :: check_git_platform — CLI vs PAT severity.

The check predates the native `git_host:` API integration. Once a PAT is
configured + validates, the standalone CLI (glab/gh/bb/az) is no longer
*required* for maestro PR features, so a CLI install/auth gap should
downgrade from failure to a warning.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# doctor + git_host now in package form
from otaman_cli import doctor
from otaman_core import git_host as gh


# ---------------------------------------------------------------------------


@pytest.fixture
def gitlab_repo(tmp_path):
    """A repo with a GitLab origin — doctor will pick gitlab as the primary."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # We don't need a real git repo; _run shells out to `git -C ... remote -v`.
    # Patching _run in the test instead.
    return [{"name": "repo", "path": "repo"}]


@pytest.fixture
def with_gitlab_remote(monkeypatch):
    """Make _run return a fake `git remote -v` output pointing at gitlab.com."""
    def fake_run(cmd, timeout=10):
        if "remote" in cmd:
            return 0, "origin\tgit@gitlab.com:foo/bar.git (push)", ""
        # Default: CLI auth checks return non-zero (not authenticated).
        return 1, "", "not logged in"
    monkeypatch.setattr(doctor, "_run", fake_run)


# ---------------------------------------------------------------------------


class TestApiLiveHelper:
    def test_no_config_returns_false(self, tmp_path):
        assert doctor._git_host_pat_is_live(tmp_path) is False

    def test_config_with_valid_token_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_TEST_TOK", "glpat-x")
        (tmp_path / "platform.yaml").write_text(
            "project: test\n"
            "git_host:\n"
            "  provider: gitlab\n"
            "  token: DOC_TEST_TOK\n",
            encoding="utf-8",
        )
        # Mock the validation network call.
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"username": "roman"}).encode("utf-8"), {},
        )):
            assert doctor._git_host_pat_is_live(tmp_path) is True

    def test_config_with_invalid_token_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_TEST_TOK", "glpat-bad")
        (tmp_path / "platform.yaml").write_text(
            "project: test\n"
            "git_host:\n"
            "  provider: gitlab\n"
            "  token: DOC_TEST_TOK\n",
            encoding="utf-8",
        )
        with patch.object(gh, "_do_get", return_value=(401, b"{}", {})):
            assert doctor._git_host_pat_is_live(tmp_path) is False


class TestCliNotInstalled:
    def test_fail_when_no_pat(self, gitlab_repo, tmp_path, with_gitlab_remote, monkeypatch):
        """Without git_host: configured, missing glab is still a hard fail."""
        monkeypatch.setattr(doctor, "_which", lambda name: None)
        result = doctor.check_git_platform(gitlab_repo, tmp_path)
        assert result["status"] == "fail"
        issues = result["issues"]
        assert any("glab CLI not installed" in i["issue"] for i in issues)
        assert issues[0]["severity"] == "high"

    def test_warn_when_pat_live(
        self, gitlab_repo, tmp_path, with_gitlab_remote, monkeypatch,
    ):
        """With a live PAT, missing glab downgrades to warn."""
        monkeypatch.setenv("DOC_TOK", "x")
        (tmp_path / "platform.yaml").write_text(
            "project: test\ngit_host:\n  provider: gitlab\n  token: DOC_TOK\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(doctor, "_which", lambda name: None)
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"username": "roman"}).encode("utf-8"), {},
        )):
            result = doctor.check_git_platform(gitlab_repo, tmp_path)
        assert result["status"] == "warn"
        issue = result["issues"][0]
        assert "optional" in issue["issue"]
        assert "maestro git-host" in issue["issue"]
        assert issue["severity"] == "medium"
        # pr_enabled should still be True because the API covers it.
        assert result["details"]["pr_enabled"] is True


class TestCliNotAuthenticated:
    def test_fail_when_no_pat(self, gitlab_repo, tmp_path, with_gitlab_remote, monkeypatch):
        # glab installed (so _which returns a path) but unauthenticated.
        monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/" + name)
        result = doctor.check_git_platform(gitlab_repo, tmp_path)
        assert result["status"] == "fail"
        issue = result["issues"][0]
        assert "not authenticated" in issue["issue"]
        assert issue["severity"] == "high"

    def test_warn_when_pat_live(
        self, gitlab_repo, tmp_path, with_gitlab_remote, monkeypatch,
    ):
        """The exact Roman case: glab installed but not logged in,
        maestro PAT works — should show warn + an explanatory hint."""
        monkeypatch.setenv("DOC_TOK", "x")
        (tmp_path / "platform.yaml").write_text(
            "project: test\ngit_host:\n  provider: gitlab\n  token: DOC_TOK\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/" + name)
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"username": "roman"}).encode("utf-8"), {},
        )):
            result = doctor.check_git_platform(gitlab_repo, tmp_path)
        assert result["status"] == "warn"
        issue = result["issues"][0]
        assert "optional" in issue["issue"]
        assert "git_host:" in issue["issue"]
        assert issue["severity"] == "medium"
        # The api_live flag should be surfaced in details.
        assert result["details"]["git_host_api_live"] is True


class TestCliHealthy:
    def test_all_ok_no_pat(
        self, gitlab_repo, tmp_path, monkeypatch,
    ):
        """CLI authenticated, no PAT — still OK, just on the glab path."""
        def fake_run(cmd, timeout=10):
            if "remote" in cmd:
                return 0, "origin\tgit@gitlab.com:foo/bar.git (push)", ""
            # glab auth status succeeds.
            return 0, "logged in as roman", ""
        monkeypatch.setattr(doctor, "_run", fake_run)
        monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/" + name)
        result = doctor.check_git_platform(gitlab_repo, tmp_path)
        assert result["status"] == "ok"
        assert "issues" not in result or not result["issues"]
        assert result["details"]["pr_enabled"] is True
