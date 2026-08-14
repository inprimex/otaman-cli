"""Tests for doctor.check_launch_commands_resume (M-13b).

Checks that platform.yaml repos whose launch_commands invoke claude without
-c / --continue / --resume get a WARN rather than being silently skipped.
"""

from __future__ import annotations

from otaman_cli.doctor import check_launch_commands_resume


def _repo(name: str, cmds=None) -> dict:
    r: dict = {"name": name}
    if cmds is not None:
        r["launch_commands"] = cmds
    return r


class TestNoRepos:
    def test_empty_list(self):
        result = check_launch_commands_resume([])
        assert result["status"] == "ok"
        assert result.get("issues", []) == []


class TestNoLaunchCommands:
    def test_repo_without_launch_commands_key(self):
        result = check_launch_commands_resume([_repo("backend")])
        assert result["status"] == "ok"

    def test_empty_string_skipped(self):
        result = check_launch_commands_resume([_repo("backend", "")])
        assert result["status"] == "ok"


class TestCommandWithResume:
    def test_short_flag_no_warn(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude -c --plugin-dir /opt/p ."),
            ]
        )
        assert result["status"] == "ok"

    def test_continue_no_warn(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude --continue --plugin-dir /opt/p ."),
            ]
        )
        assert result["status"] == "ok"

    def test_resume_no_warn(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude --resume --plugin-dir /opt/p ."),
            ]
        )
        assert result["status"] == "ok"

    def test_list_of_commands_with_one_having_c(self):
        result = check_launch_commands_resume(
            [
                _repo("api", ["claude -c .", "some-other-cmd"]),
            ]
        )
        assert result["status"] == "ok"


class TestCommandMissingResume:
    def test_warn_when_missing_c(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude --plugin-dir /opt/p ."),
            ]
        )
        assert result["status"] == "warn"
        assert len(result["issues"]) == 1
        issue = result["issues"][0]
        assert "api" in issue["issue"]
        assert "-c" in issue["issue"]
        assert issue["severity"] == "low"

    def test_warn_includes_fix(self):
        result = check_launch_commands_resume(
            [
                _repo("web", "claude --plugin-dir /opt/p ."),
            ]
        )
        fix = result["issues"][0]["fix"]
        assert "web" in fix
        assert "-c" in fix

    def test_non_claude_command_skipped(self):
        result = check_launch_commands_resume(
            [
                _repo("tools", "python3 run.py"),
            ]
        )
        assert result["status"] == "ok"

    def test_multiple_repos_each_get_issue(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude --plugin-dir /opt/p ."),
                _repo("web", "claude --plugin-dir /opt/q ."),
            ]
        )
        assert result["status"] == "warn"
        assert len(result["issues"]) == 2

    def test_one_ok_one_warn(self):
        result = check_launch_commands_resume(
            [
                _repo("api", "claude -c --plugin-dir /opt/p ."),
                _repo("web", "claude --plugin-dir /opt/q ."),
            ]
        )
        assert result["status"] == "warn"
        assert len(result["issues"]) == 1
        assert "web" in result["issues"][0]["issue"]

    def test_string_command(self):
        """launch_commands as a plain string (not a list)."""
        result = check_launch_commands_resume(
            [
                _repo("api", "claude --plugin-dir /opt/p ."),
            ]
        )
        assert result["status"] == "warn"
