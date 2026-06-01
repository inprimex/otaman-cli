"""Tests for cli-init-smart-entry-point (tasks 3.1, 3.2, 3.3).

Covers:
- Non-TTY stdin → improved error, exit 2, wizard NOT called
- TTY + sibling repos detected → scan prompt; Y answer routes to cmd_scan
- TTY + no platform.yaml + user accepts wizard prompt → run_program_init called
- Existing platform.yaml → preflight skips (normal init path)
- Single-repo default: OTAMAN_INIT_CWD_IS_GIT=1 → primary_repo default is "."
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest import mock

import pytest

from otaman_cli import main as cli_main


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean tmp directory used as cwd. No platform.yaml, no parent repos."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Task 3.2 — non-TTY stdin


def test_non_tty_stdin_prints_error_and_exits_2(tmp_cwd: Path, capsys) -> None:
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=False):
        rc = cli_main._init_preflight([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "No platform.yaml found" in captured.out


def test_non_tty_does_not_call_wizard(tmp_cwd: Path) -> None:
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=False), \
         mock.patch("otaman_cli.onboard.program_init.run_program_init") as mock_wizard:
        cli_main._init_preflight([])
    mock_wizard.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3.1 — TTY + empty dir → wizard


def test_tty_no_repos_user_accepts_wizard_called(tmp_cwd: Path) -> None:
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value=""), \
         mock.patch("otaman_cli.onboard.program_init.run_program_init", return_value=0) as mock_wizard:
        rc = cli_main._init_preflight([])
    assert rc == 0
    mock_wizard.assert_called_once()
    # Wizard was called with an argparse.Namespace (has required attrs for runner)
    ns = mock_wizard.call_args.args[0]
    assert isinstance(ns, argparse.Namespace)
    assert hasattr(ns, "program")
    assert hasattr(ns, "questions_yaml")
    assert hasattr(ns, "mode")
    assert hasattr(ns, "dry_run")


def test_tty_no_repos_user_declines_wizard_not_called(tmp_cwd: Path) -> None:
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="n"), \
         mock.patch("otaman_cli.onboard.program_init.run_program_init") as mock_wizard:
        rc = cli_main._init_preflight([])
    assert rc == 0
    mock_wizard.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3.3 — sibling git repos detected → scan prompt


def test_sibling_repos_user_accepts_calls_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "platform"
    parent.mkdir()
    cwd = parent / "starter"
    cwd.mkdir()
    sibling = parent / "svc"
    sibling.mkdir()
    (sibling / ".git").mkdir()

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)

    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"), \
         mock.patch("otaman_cli.main.cmd_scan", return_value=0) as mock_scan:
        rc = cli_main._init_preflight([])
    assert rc == 0
    mock_scan.assert_called_once()
    # cmd_scan called with cwd as the scan path
    args = mock_scan.call_args.args[0]
    assert args == [str(cwd)]


def test_sibling_repos_user_declines_falls_through_to_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "platform"
    parent.mkdir()
    cwd = parent / "starter"
    cwd.mkdir()
    sibling = parent / "svc"
    sibling.mkdir()
    (sibling / ".git").mkdir()

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)

    # Two inputs: "n" to scan prompt, then "y" to wizard prompt
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["n", "y"]), \
         mock.patch("otaman_cli.main.cmd_scan") as mock_scan, \
         mock.patch("otaman_cli.onboard.program_init.run_program_init", return_value=0) as mock_wizard:
        rc = cli_main._init_preflight([])
    assert rc == 0
    mock_scan.assert_not_called()
    mock_wizard.assert_called_once()


# ---------------------------------------------------------------------------
# Pre-flight skip cases


def test_explicit_config_arg_skips_preflight(tmp_cwd: Path) -> None:
    """Passing a config arg means user knows what they want — no pre-flight."""
    rc = cli_main._init_preflight(["some-config.yaml"])
    assert rc is None


def test_existing_platform_yaml_skips_preflight(tmp_cwd: Path) -> None:
    (tmp_cwd / "platform.yaml").write_text("project: x\n")
    rc = cli_main._init_preflight([])
    assert rc is None


# ---------------------------------------------------------------------------
# Task 1.3 — single-repo default propagation


def test_cwd_is_git_sets_env_var_when_calling_wizard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = tmp_path / "myrepo"
    cwd.mkdir()
    (cwd / ".git").mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)

    captured_env: dict[str, str] = {}

    def _capture_env(ns):
        captured_env["OTAMAN_INIT_CWD_IS_GIT"] = os.environ.get("OTAMAN_INIT_CWD_IS_GIT", "")
        return 0

    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"), \
         mock.patch("otaman_cli.onboard.program_init.run_program_init", side_effect=_capture_env):
        cli_main._init_preflight([])

    assert captured_env["OTAMAN_INIT_CWD_IS_GIT"] == "1"
    # And it's cleaned up afterwards
    assert os.environ.get("OTAMAN_INIT_CWD_IS_GIT") is None


def test_cwd_not_git_does_not_set_env_var(tmp_cwd: Path) -> None:
    captured_env: dict[str, str] = {}

    def _capture_env(ns):
        captured_env["val"] = os.environ.get("OTAMAN_INIT_CWD_IS_GIT", "<unset>")
        return 0

    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"), \
         mock.patch("otaman_cli.onboard.program_init.run_program_init", side_effect=_capture_env):
        cli_main._init_preflight([])

    assert captured_env["val"] == "<unset>"


# ---------------------------------------------------------------------------
# Wizard primary_repo default reflects env var


def test_builtin_questions_primary_repo_default_when_cwd_is_git(monkeypatch: pytest.MonkeyPatch) -> None:
    from otaman_cli.onboard.program_init.runner import _builtin_questions

    monkeypatch.setenv("OTAMAN_INIT_CWD_IS_GIT", "1")
    questions = _builtin_questions()
    primary = next(q for q in questions if q["id"] == "primary_repo")
    assert primary["default"] == "."


def test_builtin_questions_primary_repo_default_when_cwd_not_git(monkeypatch: pytest.MonkeyPatch) -> None:
    from otaman_cli.onboard.program_init.runner import _builtin_questions

    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)
    questions = _builtin_questions()
    primary = next(q for q in questions if q["id"] == "primary_repo")
    assert primary["default"] == ""


# ---------------------------------------------------------------------------
# Platform-gen single-repo mode produces main-agent owner


def test_platform_gen_single_repo_uses_main_agent() -> None:
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    answers = {
        "program_name": "myproj",
        "primary_repo": ".",
        "mode": 1,
        "active_edition": "ce",
        "roles": [],
        "processes": [],
    }
    doc = _build_platform_yaml(answers)
    assert doc["repos"][0]["name"] == "myproj"
    assert doc["repos"][0]["path"] == "."
    assert doc["repos"][0]["owner"] == "main-agent"


def test_platform_gen_classic_path_uses_spec_agent() -> None:
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

    answers = {
        "program_name": "myproj",
        "primary_repo": "~/myproj/myproj-specs",
        "mode": 1,
        "active_edition": "ce",
        "roles": [],
        "processes": [],
    }
    doc = _build_platform_yaml(answers)
    assert doc["repos"][0]["name"] == "myproj-specs"
    assert doc["repos"][0]["owner"] == "spec-agent"


# ---------------------------------------------------------------------------
# Task 1.4 — --update path skips pre-flight entirely


def test_update_flag_does_not_invoke_preflight(tmp_cwd: Path) -> None:
    """`otaman init --update` early-returns at the top of cmd_init; pre-flight
    never runs. We verify by patching _cmd_init_update and _init_preflight.
    """
    with mock.patch("otaman_cli.main._cmd_init_update", return_value=0) as mock_update, \
         mock.patch("otaman_cli.main._init_preflight") as mock_pre:
        rc = cli_main.cmd_init([], update=True)
    assert rc == 0
    mock_update.assert_called_once()
    mock_pre.assert_not_called()


def test_shell_flag_does_not_invoke_preflight(tmp_cwd: Path) -> None:
    with mock.patch("otaman_cli.main._cmd_init_shell", return_value=0) as mock_shell, \
         mock.patch("otaman_cli.main._init_preflight") as mock_pre:
        rc = cli_main.cmd_init([], shell=True)
    assert rc == 0
    mock_shell.assert_called_once()
    mock_pre.assert_not_called()
