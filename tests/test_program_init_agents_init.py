"""Tests for agents_init.py — .agents/ structure initialization."""

from __future__ import annotations

from pathlib import Path

from otaman_cli.onboard.program_init.agents_init import init_agents_structure


class TestInitAgentsStructure:
    def test_creates_expected_subdirs(self, tmp_path):
        created = init_agents_structure(tmp_path, "my-app")
        paths = [tmp_path / p for p in created]
        assert any("queue" in str(p) for p in paths)
        assert any("blocked" in str(p) for p in paths)
        assert any("reviews" in str(p) and "pending" in str(p) for p in paths)
        assert any("reviews" in str(p) and "done" in str(p) for p in paths)
        assert any("knowledge" in str(p) for p in paths)

    def test_creates_ownership_json(self, tmp_path):
        init_agents_structure(tmp_path, "my-app")
        ownership = tmp_path / ".agents" / "ownership.json"
        assert ownership.is_file()
        import json

        data = json.loads(ownership.read_text())
        assert data["program"] == "my-app"
        assert data["version"] == 1

    def test_creates_agents_yaml(self, tmp_path):
        init_agents_structure(tmp_path, "my-app")
        agents_yaml = tmp_path / ".agents" / "agents.yaml"
        assert agents_yaml.is_file()
        content = agents_yaml.read_text()
        assert "agents:" in content
        assert "my-app" in content

    def test_idempotent(self, tmp_path):
        """Second call creates nothing new."""
        init_agents_structure(tmp_path, "my-app")
        second = init_agents_structure(tmp_path, "my-app")
        assert len(second) == 0  # nothing new created

    def test_returns_relative_paths(self, tmp_path):
        created = init_agents_structure(tmp_path, "my-app")
        for p in created:
            assert not Path(p).is_absolute(), f"Expected relative path: {p}"
