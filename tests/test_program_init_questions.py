"""Tests for questions.py — YAML loader + condition evaluation (tasks.md 2.1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from otaman_cli.onboard.program_init.questions import (
    _eval_condition,
    _is_edition_gated,
    _is_mode_gated,
    ask_question,
    load_questions,
    run_questions,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _make_yaml(questions: list[dict]) -> str:
    return yaml.safe_dump({"questions": questions})


# ── load_questions ─────────────────────────────────────────────────────────

class TestLoadQuestions:
    def test_loads_list(self, tmp_path):
        f = tmp_path / "q.yaml"
        f.write_text(_make_yaml([{"id": "foo", "type": "text", "label": "Foo"}]))
        qs = load_questions(f)
        assert len(qs) == 1
        assert qs[0]["id"] == "foo"

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "q.yaml"
        f.write_text("questions: []\n")
        assert load_questions(f) == []

    def test_bad_type_raises(self, tmp_path):
        f = tmp_path / "q.yaml"
        f.write_text("questions: not-a-list\n")
        with pytest.raises(ValueError, match="must be a list"):
            load_questions(f)


# ── _eval_condition ────────────────────────────────────────────────────────

class TestEvalCondition:
    def test_none_returns_true(self):
        assert _eval_condition(None, {}) is True

    def test_empty_returns_true(self):
        assert _eval_condition("", {}) is True

    def test_true_expr(self):
        assert _eval_condition("True", {}) is True

    def test_false_expr(self):
        assert _eval_condition("False", {}) is False

    def test_answers_context(self):
        ctx = {"answers": {"processes": ["strategy"]}, "edition": "ce", "mode": 1}
        assert _eval_condition("'strategy' in answers.get('processes', [])", ctx) is True

    def test_edition_context(self):
        ctx = {"answers": {}, "edition": "ee", "mode": 1}
        assert _eval_condition("edition == 'ee'", ctx) is True

    def test_bad_expr_returns_true(self):
        # Unknown expressions default to include (fail-open)
        assert _eval_condition("undefined_var", {"answers": {}, "edition": "ce", "mode": 1}) is True

    def test_subclass_exploit_blocked(self):
        # Ensure the AST sandbox blocks class-hierarchy RCE attempts.
        # This expression reconstructs __import__ without __builtins__ in raw eval().
        exploit = "().__class__.__base__.__subclasses__()"
        # Should not raise; should return True (fail-open) rather than execute
        ctx = {"answers": {}, "edition": "ce", "mode": 1}
        result = _eval_condition(exploit, ctx)
        assert result is True  # fail-open, not executed

    def test_set_intersection_condition(self):
        # set() intersection pattern used in questions.yaml scales conditions
        ctx = {"answers": {"processes": ["outcomes", "risks"]}, "edition": "ce", "mode": 1}
        assert _eval_condition(
            "bool(set(answers.get('processes',[])) & {'risks','outcomes'})", ctx
        ) is True

    def test_set_intersection_false(self):
        ctx = {"answers": {"processes": ["vocabulary"]}, "edition": "ce", "mode": 1}
        assert _eval_condition(
            "bool(set(answers.get('processes',[])) & {'risks','outcomes'})", ctx
        ) is False


# ── edition / mode gates ───────────────────────────────────────────────────

class TestGates:
    def test_ce_question_not_gated_for_ce(self):
        q = {"edition_min": "ce"}
        assert not _is_edition_gated(q, "ce")

    def test_ee_question_gated_for_ce(self):
        q = {"edition_min": "ee"}
        assert _is_edition_gated(q, "ce")

    def test_ee_question_not_gated_for_ee(self):
        q = {"edition_min": "ee"}
        assert not _is_edition_gated(q, "ee")

    def test_mode_1_question_not_gated_for_mode_1(self):
        q = {"mode_min": 1}
        assert not _is_mode_gated(q, 1)

    def test_mode_2_question_gated_for_mode_1(self):
        q = {"mode_min": 2}
        assert _is_mode_gated(q, 1)

    def test_mode_2_question_not_gated_for_mode_2(self):
        q = {"mode_min": 2}
        assert not _is_mode_gated(q, 2)


# ── ask_question (with mocked input) ──────────────────────────────────────

class TestAskQuestion:
    def _text_q(self, **kw) -> dict:
        return {"id": "name", "type": "text", "label": "Name", **kw}

    def test_skips_when_condition_false(self):
        q = self._text_q(condition="False")
        result = ask_question(q, {})
        assert result is None

    def test_skips_ee_question_on_ce(self):
        q = self._text_q(edition_min="ee")
        result = ask_question(q, {}, edition="ce")
        assert result is None

    def test_skips_mode2_on_mode1(self):
        q = self._text_q(mode_min=2)
        result = ask_question(q, {}, mode=1)
        assert result is None

    def test_text_question_with_input(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "my-program")
        # Patch questionary unavailable so we use the fallback
        import otaman_cli.onboard.program_init.questions as qmod
        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False
        try:
            q = {"id": "name", "type": "text", "label": "Name", "default": ""}
            result = ask_question(q, {})
            assert result == "my-program"
        finally:
            qmod._Q_AVAILABLE = orig

    def test_confirm_question(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        import otaman_cli.onboard.program_init.questions as qmod
        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False
        try:
            q = {"id": "ok", "type": "confirm", "label": "OK?", "default": False}
            assert ask_question(q, {}) is True
        finally:
            qmod._Q_AVAILABLE = orig

    def test_checkbox_question(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "1,2")
        import otaman_cli.onboard.program_init.questions as qmod
        orig = qmod._Q_AVAILABLE
        qmod._Q_AVAILABLE = False
        try:
            q = {
                "id": "procs", "type": "checkbox",
                "label": "Pick", "options": ["outcomes", "risks", "strategy"],
                "default": [],
            }
            result = ask_question(q, {})
            assert "outcomes" in result
            assert "risks" in result
        finally:
            qmod._Q_AVAILABLE = orig


# ── run_questions (full flow) ──────────────────────────────────────────────

class TestRunQuestions:
    def _input_seq(self, monkeypatch, responses: list[str]) -> None:
        """Patch input() to return items from *responses* in sequence."""
        import otaman_cli.onboard.program_init.questions as qmod
        qmod._Q_AVAILABLE = False
        it = iter(responses)
        monkeypatch.setattr("builtins.input", lambda _: next(it, ""))

    def test_prefill_skips_answered(self, monkeypatch):
        import otaman_cli.onboard.program_init.questions as qmod
        qmod._Q_AVAILABLE = False
        questions = [
            {"id": "a", "step": "s1", "type": "text", "label": "A"},
            {"id": "b", "step": "s1", "type": "text", "label": "B"},
        ]
        monkeypatch.setattr("builtins.input", lambda _: "live-answer")
        answers = run_questions(questions, prefill={"a": "cached"})
        assert answers["a"] == "cached"
        assert answers["b"] == "live-answer"

    def test_skip_completed_step(self, monkeypatch):
        import otaman_cli.onboard.program_init.questions as qmod
        qmod._Q_AVAILABLE = False
        questions = [
            {"id": "a", "step": "s1", "type": "text", "label": "A"},
            {"id": "b", "step": "s2", "type": "text", "label": "B"},
        ]
        # s1 is already done → should not call input for 'a'
        call_count = {"n": 0}
        def _input(prompt):
            call_count["n"] += 1
            return "answer-b"
        monkeypatch.setattr("builtins.input", _input)
        answers = run_questions(
            questions,
            prefill={"a": "old-a"},
            skip_steps=["s1"],
        )
        assert answers.get("a") == "old-a"  # from prefill
        assert answers["b"] == "answer-b"
        assert call_count["n"] == 1  # only b was asked

    def test_on_step_complete_called(self, monkeypatch):
        import otaman_cli.onboard.program_init.questions as qmod
        qmod._Q_AVAILABLE = False
        questions = [
            {"id": "x", "step": "step_x", "type": "text", "label": "X"},
        ]
        monkeypatch.setattr("builtins.input", lambda _: "val")
        fired = {}
        def cb(step_id, step_answers):
            fired[step_id] = step_answers
        run_questions(questions, on_step_complete=cb)
        assert "step_x" in fired
        assert fired["step_x"]["x"] == "val"

    def test_ee_questions_skipped_in_ce(self, monkeypatch):
        import otaman_cli.onboard.program_init.questions as qmod
        qmod._Q_AVAILABLE = False
        questions = [
            {"id": "a", "step": "s1", "type": "text", "label": "A"},
            {"id": "ee_only", "step": "s1", "type": "text", "label": "EE", "edition_min": "ee"},
        ]
        monkeypatch.setattr("builtins.input", lambda _: "ans")
        answers = run_questions(questions, edition="ce")
        assert "ee_only" not in answers
        assert answers["a"] == "ans"
