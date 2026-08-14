"""Tests for guidance.py — post-init next-step generation (tasks.md 6.1)."""

from __future__ import annotations

from otaman_cli.onboard.program_init.guidance import generate_guidance, print_guidance


class TestGenerateGuidance:
    def _answers(self, **kw):
        return {"processes": [], "active_edition": "ce", **kw}

    def test_otaman_check_always_present(self):
        entries = generate_guidance(self._answers())
        commands = [cmd for cmd, _ in entries]
        assert "otaman check" in commands

    def test_outcome_add_when_outcomes_enabled(self):
        answers = self._answers(processes=["outcomes"])
        entries = generate_guidance(answers)
        commands = [cmd for cmd, _ in entries]
        assert "otaman outcome add" in commands

    def test_outcome_add_absent_when_not_enabled(self):
        answers = self._answers(processes=["vocabulary"])
        entries = generate_guidance(answers)
        commands = [cmd for cmd, _ in entries]
        assert "otaman outcome add" not in commands

    def test_pitch_add_when_strategy_enabled(self):
        answers = self._answers(processes=["strategy"])
        entries = generate_guidance(answers)
        commands = [cmd for cmd, _ in entries]
        assert "otaman pitch add" in commands

    def test_doctor_edition_for_ee(self):
        answers = self._answers(active_edition="ee")
        entries = generate_guidance(answers)
        commands = [cmd for cmd, _ in entries]
        assert "otaman doctor --show-edition" in commands

    def test_doctor_edition_absent_for_ce(self):
        answers = self._answers(active_edition="ce")
        entries = generate_guidance(answers)
        commands = [cmd for cmd, _ in entries]
        assert "otaman doctor --show-edition" not in commands

    def test_all_processes_gives_many_entries(self):
        answers = self._answers(
            processes=["outcomes", "solutions", "vocabulary", "risks", "strategy"]
        )
        entries = generate_guidance(answers)
        assert len(entries) >= 4


class TestPrintGuidance:
    def test_prints_program_name(self, capsys):
        answers = {"processes": ["outcomes"], "active_edition": "ce"}
        print_guidance(answers, "my-prog")
        out = capsys.readouterr().out
        assert "my-prog" in out

    def test_prints_commands(self, capsys):
        answers = {"processes": ["outcomes"], "active_edition": "ce"}
        print_guidance(answers, "my-prog")
        out = capsys.readouterr().out
        assert "otaman check" in out
        assert "otaman outcome add" in out
