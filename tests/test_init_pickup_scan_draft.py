"""Tests for `otaman init` picking up an existing platform.yaml.draft.

Closes the rough edge Roman hit on 2026-06-03: after `otaman scan` writes
`<program>-otaman/platform.yaml.draft`, running `otaman init` from the
parent directory should detect the draft and offer to promote it, instead
of falling through to "re-scan / wizard" prompts.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from otaman_cli import main as cli_main


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_INIT_CWD_IS_GIT", raising=False)
    return tmp_path


def _draft(cwd: Path, subdir: str = "myprog-otaman", content: str = "project: myprog\n") -> Path:
    """Create a <subdir>/platform.yaml.draft."""
    sub = cwd / subdir
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / "platform.yaml.draft"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _detect_scan_draft


def test_detect_scan_draft_finds_single(tmp_cwd: Path):
    expected = _draft(tmp_cwd)
    drafts = cli_main._detect_scan_draft(tmp_cwd)
    assert drafts == [expected]


def test_detect_scan_draft_finds_multiple(tmp_cwd: Path):
    a = _draft(tmp_cwd, "alpha-otaman")
    b = _draft(tmp_cwd, "beta-otaman")
    drafts = cli_main._detect_scan_draft(tmp_cwd)
    assert sorted(drafts) == sorted([a, b])


def test_detect_scan_draft_empty_when_no_draft(tmp_cwd: Path):
    (tmp_cwd / "myprog-otaman").mkdir()
    # subdir without a draft
    assert cli_main._detect_scan_draft(tmp_cwd) == []


def test_detect_scan_draft_ignores_files_only(tmp_cwd: Path):
    """A platform.yaml.draft at cwd level (not in a subdir) shouldn't count."""
    (tmp_cwd / "platform.yaml.draft").write_text("oops", encoding="utf-8")
    assert cli_main._detect_scan_draft(tmp_cwd) == []


# ---------------------------------------------------------------------------
# _init_preflight — draft pickup happy path


def test_init_preflight_picks_up_draft_when_user_accepts(tmp_cwd: Path):
    draft = _draft(tmp_cwd)
    promoted = draft.with_name("platform.yaml")

    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"), \
         mock.patch("otaman_cli.main.cmd_init", return_value=0) as mock_init:
        rc = cli_main._init_preflight([])

    assert rc == 0
    # Draft was renamed
    assert not draft.exists()
    assert promoted.is_file()
    # cmd_init was called with the explicit path
    mock_init.assert_called_once()
    args = mock_init.call_args.args[0]
    assert args == [str(promoted)]


def test_init_preflight_picks_up_draft_user_declines_falls_through(tmp_cwd: Path):
    """User says n → draft is NOT promoted; flow falls back to existing logic."""
    draft = _draft(tmp_cwd)
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["n", "n"]), \
         mock.patch("otaman_cli.main.cmd_init") as mock_init, \
         mock.patch("otaman_cli.onboard.program_init.run_program_init") as mock_wizard:
        cli_main._init_preflight([])
    # Draft survives — user declined the promotion
    assert draft.is_file()
    # cmd_init not invoked (the promotion path didn't fire)
    mock_init.assert_not_called()


def test_init_preflight_refuses_to_overwrite_existing_platform_yaml(tmp_cwd: Path):
    """If <subdir>/platform.yaml already exists, refuse and surface a clear error."""
    draft = _draft(tmp_cwd)
    existing = draft.with_name("platform.yaml")
    existing.write_text("project: existing\n", encoding="utf-8")

    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"):
        rc = cli_main._init_preflight([])
    assert rc == 1
    # Draft and existing file BOTH preserved (no destructive action)
    assert draft.is_file()
    assert existing.is_file()
    assert existing.read_text(encoding="utf-8") == "project: existing\n"


# ---------------------------------------------------------------------------
# Multi-draft case


def test_init_preflight_multiple_drafts_lists_them_falls_through(tmp_cwd: Path, capsys):
    _draft(tmp_cwd, "alpha-otaman")
    _draft(tmp_cwd, "beta-otaman")
    # Both prompts (sibling repos? new project?) declined to keep test simple
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["n", "n"]):
        cli_main._init_preflight([])
    output = capsys.readouterr().out
    assert "Found 2 scan drafts" in output
    assert "alpha-otaman" in output
    assert "beta-otaman" in output


# ---------------------------------------------------------------------------
# Non-TTY


def test_init_preflight_non_tty_with_draft_mentions_it_in_error(tmp_cwd: Path, capsys):
    _draft(tmp_cwd)
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=False):
        rc = cli_main._init_preflight([])
    assert rc == 2
    output = capsys.readouterr().out
    assert "Existing scan draft" in output
    assert "myprog-otaman" in output


def test_init_preflight_non_tty_no_draft_unchanged_message(tmp_cwd: Path, capsys):
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=False):
        rc = cli_main._init_preflight([])
    assert rc == 2
    output = capsys.readouterr().out
    assert "Existing scan draft" not in output  # not mentioned when no draft


# ---------------------------------------------------------------------------
# Existing-platform.yaml short-circuit still wins


def test_existing_platform_yaml_skips_draft_check(tmp_cwd: Path):
    """If cwd has platform.yaml, draft detection doesn't even run."""
    (tmp_cwd / "platform.yaml").write_text("project: x\n")
    _draft(tmp_cwd)  # would normally trigger the prompt
    # _init_preflight returns None (skip preflight) before checking drafts
    with mock.patch("otaman_cli.main.sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
        rc = cli_main._init_preflight([])
    assert rc is None
