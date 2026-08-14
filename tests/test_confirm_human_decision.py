"""Tests for `confirm_human_decision` (F012 security fix).

Gates commands that produce a PRIVILEGED bus message (`otaman approve`,
`otaman emergency-halt`). Unlike `confirm_destructive_operation`, there is
NO --yes/scripted bypass at all -- refusing outright on non-TTY stdin is
the entire point (a Bash-tool-driven agent session has no real TTY and
must not be able to satisfy this gate).
"""

from __future__ import annotations

from unittest import mock

from otaman_cli.safety import confirm_human_decision


class TestConfirmHumanDecision:
    def test_non_tty_refuses_without_prompting(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False),
            mock.patch("builtins.input", side_effect=AssertionError("must not prompt")),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is False

    def test_non_tty_refusal_message_mentions_interactive_terminal(self, capsys):
        with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
            confirm_human_decision("Approve X?")
        out = capsys.readouterr().err
        assert "interactive terminal" in out.lower()

    def test_tty_correct_phrase_confirms(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="CONFIRM"),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is True

    def test_tty_wrong_phrase_refuses(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="yes"),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is False

    def test_tty_case_sensitive_match_required(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="confirm"),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is False

    def test_custom_expected_phrase(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="HALT"),
        ):
            result = confirm_human_decision("Halt everything?", expected_phrase="HALT")
        assert result is True

    def test_eof_refuses(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=EOFError),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is False

    def test_keyboard_interrupt_refuses(self):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            result = confirm_human_decision("Approve X?")
        assert result is False

    def test_echoes_description(self, capsys):
        with (
            mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="CONFIRM"),
        ):
            confirm_human_decision("Approve the spec-change-request for foo?")
        out = capsys.readouterr().out
        assert "Approve the spec-change-request for foo?" in out

    def test_no_yes_bypass_parameter_exists(self):
        """The whole point: there must be no scriptable override. Confirm
        the function signature has no yes/force-style bypass kwarg."""
        import inspect

        sig = inspect.signature(confirm_human_decision)
        assert "yes" not in sig.parameters
        assert "force" not in sig.parameters
