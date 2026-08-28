"""Integration tests for runner.py — end-to-end program-init flow (tasks.md 2.1-2.3)."""

from __future__ import annotations

import argparse

import pytest

from otaman_cli.onboard.program_init.runner import _VALID_ROSTER_ROLES, run_program_init


def test_approver_is_a_valid_human_roster_role():
    # hitl-default-approver 2.3: the wizard writes roster roles including
    # `approver` (the proposal-rights grant), not a parallel vocabulary.
    assert "approver" in _VALID_ROSTER_ROLES


def _args(**kw) -> argparse.Namespace:
    """Minimal Namespace matching the CLI parser output."""
    defaults = {
        "program": None,
        "questions_yaml": None,
        "mode": None,
        "dry_run": False,
        "output_dir": None,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestRunnerEndToEnd:
    """Full flow tests with all I/O mocked.

    questionary is mocked via _Q_AVAILABLE=False + input() patching so tests
    run non-interactively.
    """

    def _disable_questionary(self):
        """Context manager that disables questionary in the questions module."""
        import otaman_cli.onboard.program_init.questions as qmod

        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False
        return orig

    def _restore_questionary(self, orig):
        import otaman_cli.onboard.program_init.questions as qmod

        qmod._Q_AVAILABLE = orig

    def test_full_flow_ce_mode1(self, tmp_path, monkeypatch):
        """Happy-path: CE edition, Mode 1, no existing platform.yaml."""
        # Redirect state dir + input
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path / "state",
        )
        orig = self._disable_questionary()

        responses = iter(
            [
                "test-app",  # program_name
                "Test application",  # description
                str(tmp_path / "test-app" / "test-app-specs"),  # primary_repo
                "",  # claude_config_dir (optional; blank = absent)
                "",  # domains (blank = defaults)
                "",  # roles
                "",  # processes
                "USD",  # currency_code
                "$",  # currency_symbol
                "2",  # currency_decimals
                "t-shirt",  # probability_scale (select → blank = default)
                "t-shirt",  # impact_scale
                "MVP",  # releases
                "",  # skill_profile (select → blank = default)
                "",  # git_platform (select → blank = default)
                "",  # secret_backend (select → blank = default)
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(responses, ""))

        try:
            args = _args(program="test-app")
            rc = run_program_init(args)
        finally:
            self._restore_questionary(orig)

        # Exit code 0 (success)
        assert rc == 0

        # platform.yaml was generated
        platform = tmp_path / "test-app" / "test-app-specs" / "platform.yaml"
        assert platform.is_file()
        content = platform.read_text()
        assert "test-app" in content

        # Checkpoint was cleared on success
        ckpt = tmp_path / "state" / "test-app" / ".init-state.yaml"
        assert not ckpt.exists()

        # otaman-init-dev-scaffold: launcher/ generated alongside platform.yaml
        launcher = platform.parent / "launcher"
        assert (launcher / "launch-settings.yaml").is_file()
        assert (launcher / "launch.sh").is_file()
        assert (launcher / "launch.ps1").is_file()
        assert (launcher / ".gitignore").is_file()

    def test_resume_from_checkpoint(self, tmp_path, monkeypatch):
        """Checkpoint with identity step completed → should skip identity questions."""
        from otaman_cli.onboard.program_init.checkpoint import Checkpoint

        state_base = tmp_path / "state"
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            state_base,
        )

        # Plant a checkpoint
        ckpt = Checkpoint.new("resume-app")
        ckpt.mark_step(
            "identity",
            {
                "program_name": "resume-app",
                "description": "From checkpoint",
                "primary_repo": str(tmp_path / "resume-app" / "specs"),
                "domains": [],
            },
        )

        orig = self._disable_questionary()
        # "y" to resume from checkpoint, then remaining questions
        responses = iter(
            [
                "y",  # resume from checkpoint?
                "",  # roles
                "",  # processes
                "EUR",  # currency_code
                "€",  # currency_symbol
                "2",  # currency_decimals
                "",  # probability scale
                "",  # impact scale
                "MVP",  # releases
                "",  # skill profile
                "",  # git platform
                "",  # secret backend
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(responses, ""))

        try:
            args = _args(program="resume-app")
            rc = run_program_init(args)
        finally:
            self._restore_questionary(orig)

        assert rc == 0
        # Description came from checkpoint
        platform = tmp_path / "resume-app" / "specs" / "platform.yaml"
        assert platform.is_file()
        content = platform.read_text()
        assert "resume-app" in content
        assert "EUR" in content  # answered post-checkpoint

    def test_keyboard_interrupt_saves_checkpoint(self, tmp_path, monkeypatch):
        """KeyboardInterrupt mid-flow → checkpoint saved, rc == 1."""
        state_base = tmp_path / "state"
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            state_base,
        )

        import otaman_cli.onboard.program_init.questions as qmod

        orig_qa = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False

        call_count = {"n": 0}

        def _input_interrupt(prompt):
            call_count["n"] += 1
            if call_count["n"] > 2:
                raise KeyboardInterrupt
            return ["interrupt-app", "Testing interrupt"][call_count["n"] - 1]

        monkeypatch.setattr("builtins.input", _input_interrupt)

        try:
            args = _args(program="interrupt-app")
            rc = run_program_init(args)
        finally:
            qmod._Q_AVAILABLE = orig_qa

        assert rc == 1


class TestStrategyOptIn:
    """Verify strategy_opt_in merges into processes list."""

    def test_strategy_opt_in_true_adds_to_processes(self, tmp_path, monkeypatch):
        """When cofounder selected and strategy_opt_in=Y, processes gets 'strategy'."""
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path / "state",
        )
        import otaman_cli.onboard.program_init.questions as qmod

        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False

        responses = iter(
            [
                "strat-app",  # program_name
                "Strategy test",  # description
                str(tmp_path / "strat-app" / "strat-app-specs"),  # primary_repo
                "",  # claude_config_dir (optional; blank = absent)
                "",  # domains
                "5",  # roles → cofounder (5th option in list: CEO CPO CTO BA cofounder)
                "",  # role_cofounder (blank)
                "",  # processes (no outcomes etc.)
                "y",  # strategy_opt_in → Yes (cofounder is present)
                "USD",  # currency_code
                "$",  # currency_symbol
                "2",  # currency_decimals
                # scales not shown (no risks/outcomes)
                # releases not shown (no outcomes)
                "",  # skill_profile
                "",  # git_platform
                "",  # secret_backend
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(responses, ""))

        try:
            args = _args(program="strat-app")
            rc = run_program_init(args)
        finally:
            qmod._Q_AVAILABLE = orig

        assert rc == 0
        platform = tmp_path / "strat-app" / "strat-app-specs" / "platform.yaml"
        assert platform.is_file()
        content = platform.read_text()
        assert "strategy" in content

    def test_strategy_opt_in_true_adds_to_processes_via_builtin_fallback(
        self, tmp_path, monkeypatch
    ):
        """Same scenario as above, but with `_find_questions_yaml` forced to
        return None so `_builtin_questions()` is exercised regardless of
        whether a sibling `otaman-meta` checkout happens to be present.

        Regression: `_builtin_questions()` was missing the `role_cofounder`
        follow-up that the otaman-meta YAML has right after `roles`
        (conditioned on `'cofounder' in roles`). CI never checks out
        otaman-meta, so it always exercises the builtin fallback — while a
        full otaman-dev workspace checkout (as used for local development)
        has the sibling repo and silently uses the real YAML instead,
        masking the drift. The one-question gap shifted every later answer
        by one position, eventually feeding the currency_symbol answer
        ("$") into the currency_decimals (number) question and crashing
        with `ValueError: invalid literal for int() with base 10: '$'`.
        This test pins the builtin path so the two question sets can't
        silently drift apart again undetected.
        """
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path / "state",
        )
        from otaman_cli.onboard.program_init import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_find_questions_yaml", lambda override=None: None)

        import otaman_cli.onboard.program_init.questions as qmod

        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False

        responses = iter(
            [
                "strat-app",  # program_name
                "Strategy test",  # description
                str(tmp_path / "strat-app" / "strat-app-specs"),  # primary_repo
                "",  # claude_config_dir (optional; blank = absent)
                "",  # domains
                "5",  # roles → cofounder (5th option in list: CEO CPO CTO BA cofounder)
                "",  # role_cofounder (blank)
                "",  # processes (no outcomes etc.)
                "y",  # strategy_opt_in → Yes (cofounder is present)
                "USD",  # currency_code
                "$",  # currency_symbol
                "2",  # currency_decimals
                "",  # skill_profile
                "",  # git_platform
                "",  # secret_backend
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(responses, ""))

        try:
            args = _args(program="strat-app")
            rc = run_program_init(args)
        finally:
            qmod._Q_AVAILABLE = orig

        assert rc == 0
        platform = tmp_path / "strat-app" / "strat-app-specs" / "platform.yaml"
        assert platform.is_file()
        content = platform.read_text()
        assert "strategy" in content


class TestCliWiring:
    """Smoke tests for the CLI argparse wiring."""

    def test_program_init_in_help(self, capsys):
        from otaman_cli.onboard.cli import main as onboard_main

        with pytest.raises(SystemExit):
            onboard_main(["--help"])
        out = capsys.readouterr().out
        assert "program-init" in out

    def test_program_init_help(self, capsys):
        from otaman_cli.onboard.cli import main as onboard_main

        with pytest.raises(SystemExit):
            onboard_main(["program-init", "--help"])
        out = capsys.readouterr().out
        assert "--program" in out
        assert "--dry-run" in out
        assert "--mode" in out
