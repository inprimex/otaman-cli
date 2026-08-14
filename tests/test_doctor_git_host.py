"""Tests for scripts/doctor.py :: check_git_host."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from otaman_core import git_host as gh

# doctor + git_host now in package form
from otaman_cli import doctor

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def maestro_root(tmp_path):
    """Empty maestro folder with no platform.yaml."""
    return tmp_path


@pytest.fixture
def configured_root(tmp_path, monkeypatch):
    """Maestro with a git_host: block + env-backed token."""
    monkeypatch.setenv("MAESTRO_GH_TOKEN_DOCTEST", "ghp_fake_token")
    (tmp_path / "platform.yaml").write_text(
        "project: test\n"
        "git_host:\n"
        "  provider: github\n"
        "  token: MAESTRO_GH_TOKEN_DOCTEST\n"
        "repos: []\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------


class TestNoConfig:
    def test_no_platform_yaml_is_ok(self, maestro_root):
        result = doctor.check_git_host(maestro_root)
        assert result["status"] == "ok"
        assert result["details"]["configured"] is False

    def test_no_git_host_block_is_ok(self, tmp_path):
        (tmp_path / "platform.yaml").write_text(
            "project: test\nrepos: []\n",
            encoding="utf-8",
        )
        result = doctor.check_git_host(tmp_path)
        assert result["status"] == "ok"
        assert result["details"]["configured"] is False


class TestConfigured:
    def test_valid_token_reports_ok(self, configured_root):
        with patch.object(
            gh,
            "_do_get",
            return_value=(
                200,
                json.dumps({"login": "octocat"}).encode("utf-8"),
                {"X-OAuth-Scopes": "repo"},
            ),
        ):
            result = doctor.check_git_host(configured_root)
        assert result["status"] == "ok"
        assert result["details"]["configured"] is True
        assert result["details"]["provider"] == "github"
        assert result["details"]["authenticated_as"] == "octocat"
        assert result["details"]["scopes"] == ["repo"]

    def test_rejected_token_is_warn_not_fail(self, configured_root):
        """Token rejection shouldn't fail the doctor run overall — git
        operations still work, just no PR enrichment."""
        with patch.object(gh, "_do_get", return_value=(401, b"{}", {})):
            result = doctor.check_git_host(configured_root)
        assert result["status"] == "warn"
        assert result["details"]["token_ok"] is False
        assert any("rejected" in i["issue"] for i in result["issues"])

    def test_missing_env_var_is_warn(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAESTRO_GH_MISSING_VAR", raising=False)
        (tmp_path / "platform.yaml").write_text(
            "project: test\ngit_host:\n  provider: github\n  token: MAESTRO_GH_MISSING_VAR\n",
            encoding="utf-8",
        )
        result = doctor.check_git_host(tmp_path)
        assert result["status"] == "warn"
        assert any("not resolvable" in i["issue"] for i in result["issues"])


class TestRemotesSummary:
    def test_lists_remotes_even_without_config(self, tmp_path):
        # A repo with a parseable origin.
        (tmp_path / "platform.yaml").write_text(
            "project: test\nrepos:\n  - name: r1\n    path: ../r1\n",
            encoding="utf-8",
        )
        # Don't actually init a git repo; detect_remote_for_repo returns None.
        result = doctor.check_git_host(tmp_path)
        assert result["status"] == "ok"
        assert len(result["details"]["remotes"]) == 1
        assert result["details"]["remotes"][0]["repo"] == "r1"
        assert result["details"]["remotes"][0]["remote"] is None
