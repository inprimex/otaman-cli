"""Tests for checkpoint.py — failure recovery (tasks.md 2.3)."""

from __future__ import annotations

import yaml

from otaman_cli.onboard.program_init.checkpoint import Checkpoint


class TestCheckpointSaveLoad:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        ckpt = Checkpoint.new("my-program")
        ckpt.mark_step("identity", {"program_name": "my-program"})
        state_file = tmp_path / "my-program" / ".init-state.yaml"
        assert state_file.is_file()
        data = yaml.safe_load(state_file.read_text())
        assert data["program"] == "my-program"
        assert "identity" in data["completed_steps"]
        assert data["answers"]["program_name"] == "my-program"

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        assert Checkpoint.load("nonexistent") is None

    def test_load_restores_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        ckpt = Checkpoint.new("demo-app")
        ckpt.mark_step("identity", {"program_name": "demo-app", "description": "Demo"})
        ckpt.mark_step("roles", {"roles": ["CTO"]})

        loaded = Checkpoint.load("demo-app")
        assert loaded is not None
        assert loaded.program == "demo-app"
        assert "identity" in loaded.completed_steps
        assert "roles" in loaded.completed_steps
        assert loaded.answers["description"] == "Demo"

    def test_clear_removes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        ckpt = Checkpoint.new("to-clear")
        ckpt.mark_step("identity", {"program_name": "to-clear"})
        state_file = tmp_path / "to-clear" / ".init-state.yaml"
        assert state_file.is_file()
        ckpt.clear()
        assert not state_file.exists()

    def test_load_returns_none_on_corrupt_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        state_file = tmp_path / "corrupt" / ".init-state.yaml"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{broken yaml: [unclosed")
        assert Checkpoint.load("corrupt") is None

    def test_mark_step_accumulates_answers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "otaman_cli.onboard.program_init.checkpoint._STATE_DIR_BASE",
            tmp_path,
        )
        ckpt = Checkpoint.new("acc")
        ckpt.mark_step("identity", {"a": 1})
        ckpt.mark_step("roles", {"b": 2})
        assert ckpt.answers == {"a": 1, "b": 2}
        assert ckpt.completed_steps == ["identity", "roles"]
