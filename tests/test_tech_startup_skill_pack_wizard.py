"""Tests for tech-startup-skill-pack-implementation tasks 4.1-4.4.

  4.1 — When domain=tech-startup, the generated platform.yaml has
        skills.profile = tech-startup-cofounder
  4.2 — Confirmation screen appears before write
  4.3 — Confirmation screen includes cofounder identity note
  4.4 — Tests covering (a-d):
        (a) tech-startup domain sets profile
        (b) other domains do not set the profile
        (c) confirmation screen is shown
        (d) user can override profile before confirming
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml
from otaman_cli.onboard.program_init.questions import _recommend_skill_profile
from otaman_cli.onboard.program_init.runner import _confirm_tech_startup_prefill


# ---------------------------------------------------------------- task 4.1 (a)
class TestPrefill:
    """tech-startup domain → skills.profile == tech-startup-cofounder."""

    def test_recommend_returns_tech_startup_cofounder_for_tech_startup_domain(self):
        assert _recommend_skill_profile({"domains": ["tech-startup"]}) == "tech-startup-cofounder"

    def test_tech_startup_domain_sets_profile_in_output_yaml(self, tmp_path: Path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "myprog",
            "primary_repo": ".",
            "domains": ["tech-startup"],
            # The wizard sets `skill_profile` based on the recommendation;
            # mimic that here for an end-to-end check.
            "skill_profile": _recommend_skill_profile({"domains": ["tech-startup"]}),
            "mode": 1,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        assert doc["skills"]["profile"] == "tech-startup-cofounder"

    # 4.4 (b)
    def test_other_domains_do_not_set_tech_startup_profile(self, tmp_path: Path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "fin-app",
            "primary_repo": ".",
            "domains": ["fintech"],
            "skill_profile": _recommend_skill_profile({"domains": ["fintech"]}),
            "mode": 1,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        assert doc["skills"]["profile"] != "tech-startup-cofounder"
        assert doc["skills"]["profile"] == "fintech-default"

    def test_healthcare_domain_does_not_get_tech_startup_profile(self):
        assert _recommend_skill_profile({"domains": ["healthcare"]}) == "healthcare-default"

    def test_no_domain_defaults_to_software_development(self):
        assert _recommend_skill_profile({}) == "software-development-default"


# ---------------------------------------------------------------- task 4.2 + 4.3
class TestConfirmationScreen:
    """The screen + identity note appear when domain=tech-startup."""

    def test_confirmation_screen_shown_for_tech_startup(self, capsys):
        answers = {"domains": ["tech-startup"], "skill_profile": "tech-startup-cofounder"}
        _confirm_tech_startup_prefill(answers, dry_run=True)
        out = capsys.readouterr().out
        # Header
        assert "Tech-Startup Pack Prefill" in out
        # Exact design.md Q4 copy markers
        assert "10 skills for cofounder strategy work" in out
        assert "investor-targeting-strategist" in out
        assert "financial-modeling-analyst" in out
        assert "require cofounder identity" in out
        # Current prefill value
        assert "tech-startup-cofounder" in out

    # 4.4 (c)
    def test_confirmation_screen_NOT_shown_for_other_domains(self, capsys):
        answers = {"domains": ["fintech"], "skill_profile": "fintech-default"}
        _confirm_tech_startup_prefill(answers, dry_run=True)
        out = capsys.readouterr().out
        assert out == "", "confirmation must not fire for non-tech-startup domains"

    def test_confirmation_screen_NOT_shown_when_domains_empty(self, capsys):
        _confirm_tech_startup_prefill({"domains": []}, dry_run=True)
        assert capsys.readouterr().out == ""

    # 4.3 — identity note
    def test_identity_note_appears_in_confirmation(self, capsys):
        answers = {"domains": ["tech-startup"]}
        _confirm_tech_startup_prefill(answers, dry_run=True)
        out = capsys.readouterr().out
        assert "identity:" in out
        assert "roles:" in out
        assert "cofounder: <username>" in out
        assert "platform.yaml after init" in out


# ---------------------------------------------------------------- task 4.4 (d)
class TestProfileOverride:
    """User can override the prefilled profile from the confirmation prompt."""

    def test_user_override_changes_skill_profile(self, capsys, monkeypatch):
        answers = {"domains": ["tech-startup"], "skill_profile": "tech-startup-cofounder"}
        monkeypatch.setattr("builtins.input", lambda *_: "fintech-default")
        _confirm_tech_startup_prefill(answers, dry_run=False)
        assert answers["skill_profile"] == "fintech-default"
        out = capsys.readouterr().out
        assert "overridden" in out.lower()

    def test_blank_input_keeps_prefilled_profile(self, capsys, monkeypatch):
        answers = {"domains": ["tech-startup"], "skill_profile": "tech-startup-cofounder"}
        monkeypatch.setattr("builtins.input", lambda *_: "")
        _confirm_tech_startup_prefill(answers, dry_run=False)
        assert answers["skill_profile"] == "tech-startup-cofounder"
        out = capsys.readouterr().out
        assert "Confirmed" in out

    def test_eof_falls_through_to_accept_as_is(self, monkeypatch):
        """Non-interactive shell (no stdin) → don't crash, accept the prefill."""
        answers = {"domains": ["tech-startup"], "skill_profile": "tech-startup-cofounder"}
        def _raise_eof(*_a, **_kw):
            raise EOFError
        monkeypatch.setattr("builtins.input", _raise_eof)
        _confirm_tech_startup_prefill(answers, dry_run=False)
        assert answers["skill_profile"] == "tech-startup-cofounder"

    def test_keyboard_interrupt_falls_through_to_accept(self, monkeypatch):
        answers = {"domains": ["tech-startup"], "skill_profile": "tech-startup-cofounder"}
        def _raise_kbd(*_a, **_kw):
            raise KeyboardInterrupt
        monkeypatch.setattr("builtins.input", _raise_kbd)
        _confirm_tech_startup_prefill(answers, dry_run=False)
        # Profile unchanged, no exception bubbled
        assert answers["skill_profile"] == "tech-startup-cofounder"

    def test_dry_run_does_not_call_input(self, monkeypatch):
        """Dry-run skips the input() call entirely."""
        answers = {"domains": ["tech-startup"]}
        def _boom(*_a, **_kw):
            raise AssertionError("input() must NOT be called in dry-run")
        monkeypatch.setattr("builtins.input", _boom)
        _confirm_tech_startup_prefill(answers, dry_run=True)
        # No error → input wasn't called


# ---------------------------------------------------------------- integration
class TestEndToEndPrefill:
    """End-to-end via write_platform_yaml — confirms the recommendation flow."""

    def test_wizard_path_tech_startup_yields_correct_profile(self, tmp_path: Path):
        """Simulate the wizard's resolution: domain → recommendation → answers → yaml."""
        domains = ["tech-startup"]
        # As the wizard would, compute the default via _recommend_skill_profile
        profile = _recommend_skill_profile({"domains": domains})

        out = tmp_path / "platform.yaml"
        write_platform_yaml({
            "program_name": "ts-app",
            "primary_repo": ".",
            "domains": domains,
            "skill_profile": profile,
            "mode": 1,
            "active_edition": "ce",
        }, out)
        doc = yaml.safe_load(out.read_text())
        assert doc["skills"]["profile"] == "tech-startup-cofounder"
        # domain preserved in output
        assert doc.get("domains") == ["tech-startup"]
