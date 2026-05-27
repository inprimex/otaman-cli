"""Tests for scaffold.py — companion-repos logic (tasks.md 3.1)."""
from __future__ import annotations

import pytest

from otaman_cli.onboard.program_init.scaffold import (
    ScaffoldResult,
    compute_companion_repos,
    scaffold_companion_repos,
)


class TestComputeCompanionRepos:
    def test_outcomes_triggers_business(self):
        assert "business" in compute_companion_repos(["outcomes"])

    def test_risks_triggers_business(self):
        assert "business" in compute_companion_repos(["risks"])

    def test_strategy_triggers_strategy(self):
        assert "strategy" in compute_companion_repos(["strategy"])

    def test_strategy_alone_no_business(self):
        repos = compute_companion_repos(["strategy"])
        assert "business" not in repos
        assert "strategy" in repos

    def test_both_processes(self):
        repos = compute_companion_repos(["outcomes", "strategy"])
        assert "business" in repos
        assert "strategy" in repos

    def test_empty_processes(self):
        assert compute_companion_repos([]) == []

    def test_unknown_process_ignored(self):
        repos = compute_companion_repos(["unknown-process"])
        assert repos == []


class TestScaffoldCompanionRepos:
    def test_empty_repos_returns_ok(self):
        result = scaffold_companion_repos("myproj", [], {})
        assert result.ok
        assert result.scaffolded == []

    def test_bridge_missing_returns_error_guidance(self):
        """When bridge is not installed, errors should contain manual command guidance."""
        result = scaffold_companion_repos("myproj", ["business"], {})
        # Bridge is not installed in test env → should get error with guidance
        assert len(result.errors) > 0
        assert "myproj-business" in result.errors[0]
        assert "companion-repos" in result.errors[0] or "bridge" in result.errors[0]

    def test_bridge_called_when_available(self, monkeypatch):
        """If bridge is importable, its function is called."""
        from types import SimpleNamespace
        import sys

        fake_bridge = SimpleNamespace(
            scaffold=SimpleNamespace(
                scaffold_companion_repos=lambda slug, repos, answers, dry_run=False: ScaffoldResult(
                    scaffolded=[f"{slug}-{r}" for r in repos]
                )
            )
        )
        monkeypatch.setitem(sys.modules, "otaman_bridge", fake_bridge)
        monkeypatch.setitem(sys.modules, "otaman_bridge.scaffold", fake_bridge.scaffold)

        result = scaffold_companion_repos("acme", ["business"], {})
        assert result.ok
        assert "acme-business" in result.scaffolded
