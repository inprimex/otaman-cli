"""Tests for `maestro models` CLI subcommands (Phase C).

Covers the writable set-default / set-repo / set-agent / unset-* paths
that mutate platform.yaml's ``models:`` block, plus the ``show``
subcommand that explains the resolution chain.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# models_report is now an importable package module
from otaman_cli import models_report as _models_report_module


def _load_module():
    """Compatibility shim — returns the package module so existing test code works."""
    return _models_report_module


@pytest.fixture
def maestro_folder(tmp_path, monkeypatch):
    """Temporary maestro root that find_project_root() will discover."""
    root = tmp_path / "my-maestro"
    root.mkdir()
    (root / ".agents").mkdir()
    (root / "platform.yaml").write_text(
        "project: test\nversion: '1.0'\nrepos: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return root


def _run_cli(*args: str, cwd: Path):
    """Run models_report as a subprocess; assert rc and return stdout."""
    # Subprocess does not inherit pytest pythonpath config — set explicitly.
    env = os.environ.copy()
    cli_src = str(Path(__file__).resolve().parent.parent / "src")
    core_src = str(Path(__file__).resolve().parent.parent.parent / "otaman-core" / "src")
    env["PYTHONPATH"] = os.pathsep.join([cli_src, core_src, env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.models_report", *args],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
        env=env,
    )


def _read_models(root: Path) -> dict:
    data = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    return data.get("models") or {}


# ---------------------------------------------------------------------------
# Backcompat — legacy modes still work


class TestLegacyBackcompat:
    def test_no_args_prints_shipped(self, maestro_folder):
        result = _run_cli(cwd=maestro_folder)
        assert result.returncode == 0
        assert "Commands (shipped defaults)" in result.stdout
        assert "Agents (shipped defaults)" in result.stdout

    def test_diff_flag(self, maestro_folder):
        result = _run_cli("--diff", cwd=maestro_folder)
        assert result.returncode == 0

    def test_suggest_flag(self, maestro_folder):
        result = _run_cli("--suggest", cwd=maestro_folder)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# set-default / unset-default


class TestSetDefault:
    def test_writes_default_model_and_effort(self, maestro_folder):
        result = _run_cli(
            "set-default",
            "--model",
            "sonnet",
            "--effort",
            "medium",
            cwd=maestro_folder,
        )
        assert result.returncode == 0
        models = _read_models(maestro_folder)
        assert models["default"] == "sonnet"
        assert models["default_effort"] == "medium"

    def test_model_only(self, maestro_folder):
        _run_cli("set-default", "--model", "haiku", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert models["default"] == "haiku"
        assert "default_effort" not in models

    def test_rejects_invalid_model(self, maestro_folder):
        result = _run_cli(
            "set-default",
            "--model",
            "octopus",
            cwd=maestro_folder,
        )
        assert result.returncode == 2
        assert "invalid model" in result.stderr

    def test_rejects_invalid_effort(self, maestro_folder):
        result = _run_cli(
            "set-default",
            "--effort",
            "maximal",
            cwd=maestro_folder,
        )
        assert result.returncode == 2
        assert "invalid effort" in result.stderr

    def test_case_normalized(self, maestro_folder):
        _run_cli("set-default", "--model", "SONNET", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert models["default"] == "sonnet"

    def test_unset_default_clears(self, maestro_folder):
        _run_cli("set-default", "--model", "sonnet", "--effort", "low", cwd=maestro_folder)
        _run_cli("unset-default", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert "default" not in models
        assert "default_effort" not in models


# ---------------------------------------------------------------------------
# set-repo / unset-repo


class TestSetRepo:
    def test_writes_per_repo_entry(self, maestro_folder):
        _run_cli(
            "set-repo",
            "train",
            "--model",
            "opus",
            "--effort",
            "high",
            cwd=maestro_folder,
        )
        models = _read_models(maestro_folder)
        assert models["by_repo"]["train"] == {"model": "opus", "effort": "high"}

    def test_multiple_repos(self, maestro_folder):
        _run_cli("set-repo", "train", "--model", "opus", cwd=maestro_folder)
        _run_cli("set-repo", "edge", "--model", "haiku", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert models["by_repo"]["train"]["model"] == "opus"
        assert models["by_repo"]["edge"]["model"] == "haiku"

    def test_update_existing(self, maestro_folder):
        _run_cli("set-repo", "train", "--model", "opus", cwd=maestro_folder)
        _run_cli("set-repo", "train", "--model", "haiku", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        # Second set overwrites (stored as model key)
        assert models["by_repo"]["train"]["model"] == "haiku"

    def test_unset_removes_entry(self, maestro_folder):
        _run_cli("set-repo", "train", "--model", "opus", cwd=maestro_folder)
        _run_cli("set-repo", "edge", "--model", "haiku", cwd=maestro_folder)
        _run_cli("unset-repo", "train", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert "train" not in models.get("by_repo", {})
        assert models["by_repo"]["edge"]["model"] == "haiku"

    def test_unset_last_entry_removes_by_repo_key(self, maestro_folder):
        _run_cli("set-repo", "train", "--model", "opus", cwd=maestro_folder)
        _run_cli("unset-repo", "train", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        # Empty by_repo dict is pruned to keep the file clean
        assert "by_repo" not in models


# ---------------------------------------------------------------------------
# set-agent / unset-agent


class TestSetAgent:
    def test_writes_per_agent_entry(self, maestro_folder):
        _run_cli(
            "set-agent",
            "train-agent",
            "--model",
            "opus",
            cwd=maestro_folder,
        )
        models = _read_models(maestro_folder)
        assert models["by_agent"]["train-agent"]["model"] == "opus"

    def test_unset_removes_entry(self, maestro_folder):
        _run_cli("set-agent", "train-agent", "--model", "opus", cwd=maestro_folder)
        _run_cli("unset-agent", "train-agent", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert "by_agent" not in models  # pruned


# ---------------------------------------------------------------------------
# Preserves surrounding platform.yaml content


class TestPreservesSurroundingContent:
    def test_keeps_project_and_repos(self, maestro_folder):
        # Seed platform.yaml with meaningful content + a comment
        (maestro_folder / "platform.yaml").write_text(
            "# Watchtower project\n"
            "project: watchtower\n"
            "version: '1.0'\n"
            "repos:\n"
            "  - name: train\n"
            "    path: ../train\n"
            "    owner: train-agent\n",
            encoding="utf-8",
        )
        _run_cli("set-default", "--model", "sonnet", cwd=maestro_folder)
        content = (maestro_folder / "platform.yaml").read_text(encoding="utf-8")
        assert "# Watchtower project" in content  # top comment preserved
        assert "project: watchtower" in content
        assert "- name: train" in content
        assert "owner: train-agent" in content
        assert "models:" in content

    def test_replaces_existing_models_block(self, maestro_folder):
        (maestro_folder / "platform.yaml").write_text(
            "project: test\n"
            "version: '1.0'\n"
            "repos: []\n"
            "models:\n"
            "  default: opus\n"
            "  default_effort: max\n",
            encoding="utf-8",
        )
        _run_cli("set-default", "--model", "haiku", "--effort", "low", cwd=maestro_folder)
        models = _read_models(maestro_folder)
        assert models == {"default": "haiku", "default_effort": "low"}
        # The text should no longer contain the old opus default
        content = (maestro_folder / "platform.yaml").read_text(encoding="utf-8")
        assert "default: opus" not in content
        assert "default: haiku" in content


# ---------------------------------------------------------------------------
# show subcommand


class TestShow:
    def test_shows_default_chain(self, maestro_folder):
        _run_cli("set-default", "--model", "sonnet", "--effort", "medium", cwd=maestro_folder)
        result = _run_cli("show", cwd=maestro_folder)
        assert result.returncode == 0
        assert "project default" in result.stdout
        assert "sonnet" in result.stdout
        assert "Effective:" in result.stdout

    def test_shows_per_repo_resolution(self, maestro_folder):
        _run_cli("set-default", "--model", "sonnet", cwd=maestro_folder)
        _run_cli("set-repo", "train", "--model", "opus", "--effort", "high", cwd=maestro_folder)
        result = _run_cli("show", "--repo", "train", cwd=maestro_folder)
        assert result.returncode == 0
        # Marker "->" points at the rule that fired
        assert "->" in result.stdout
        assert "by_repo[train]" in result.stdout
        assert "opus" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Error paths


class TestTargetFlag:
    """--launch-settings / --platform / auto target selection."""

    def test_launch_settings_flag_writes_there(self, maestro_folder):
        (maestro_folder / "launch-settings.yaml").write_text(
            "accounts: {}\n",
            encoding="utf-8",
        )
        result = _run_cli(
            "set-default",
            "--model",
            "sonnet",
            "--launch-settings",
            cwd=maestro_folder,
        )
        assert result.returncode == 0
        # Written to launch-settings.yaml
        ls = (
            yaml.safe_load((maestro_folder / "launch-settings.yaml").read_text(encoding="utf-8"))
            or {}
        )
        assert ls.get("models", {}).get("default") == "sonnet"
        # platform.yaml stays untouched (no models: block)
        platform = (
            yaml.safe_load((maestro_folder / "platform.yaml").read_text(encoding="utf-8")) or {}
        )
        assert "models" not in platform

    def test_platform_flag_forces_platform_yaml(self, maestro_folder):
        """Even if launch-settings.yaml exists and looks launcher-y,
        --platform forces platform.yaml."""
        (maestro_folder / "launch-settings.yaml").write_text(
            "accounts:\n  personal:\n    config_dir: ~/.claude-personal\n",
            encoding="utf-8",
        )
        result = _run_cli(
            "set-default",
            "--model",
            "sonnet",
            "--platform",
            cwd=maestro_folder,
        )
        assert result.returncode == 0
        platform = (
            yaml.safe_load((maestro_folder / "platform.yaml").read_text(encoding="utf-8")) or {}
        )
        assert platform["models"]["default"] == "sonnet"

    def test_auto_prefers_launch_settings_when_launcher_like(self, maestro_folder):
        """launch-settings.yaml exists with connections/accounts → auto
        picks it over platform.yaml."""
        (maestro_folder / "launch-settings.yaml").write_text(
            "accounts:\n  personal:\n    config_dir: ~/.claude-personal\n"
            "active_connection: lan\n"
            "connections:\n  lan:\n    type: ssh\n",
            encoding="utf-8",
        )
        _run_cli(
            "set-repo",
            "train",
            "--model",
            "opus",
            cwd=maestro_folder,
        )
        ls = (
            yaml.safe_load((maestro_folder / "launch-settings.yaml").read_text(encoding="utf-8"))
            or {}
        )
        assert ls["models"]["by_repo"]["train"]["model"] == "opus"
        # platform.yaml untouched
        platform = (
            yaml.safe_load((maestro_folder / "platform.yaml").read_text(encoding="utf-8")) or {}
        )
        assert "models" not in platform

    def test_auto_falls_to_platform_without_launcher_signals(self, maestro_folder):
        """launch-settings.yaml without connections:/accounts: → auto
        uses platform.yaml (legacy behavior)."""
        (maestro_folder / "launch-settings.yaml").write_text(
            "# empty/non-launcher file\n",
            encoding="utf-8",
        )
        _run_cli("set-default", "--model", "sonnet", cwd=maestro_folder)
        platform = (
            yaml.safe_load((maestro_folder / "platform.yaml").read_text(encoding="utf-8")) or {}
        )
        assert platform["models"]["default"] == "sonnet"

    def test_launch_settings_read_by_resolver(self, maestro_folder):
        """End-to-end: write via --launch-settings, read via the resolver."""
        (maestro_folder / "launch-settings.yaml").write_text(
            "accounts:\n  personal:\n    config_dir: x\n",
            encoding="utf-8",
        )
        _run_cli(
            "set-default",
            "--model",
            "sonnet",
            "--launch-settings",
            cwd=maestro_folder,
        )
        _run_cli(
            "set-repo",
            "train",
            "--model",
            "opus",
            "--launch-settings",
            cwd=maestro_folder,
        )
        # maestro models show reads from both sources via resolver
        result = _run_cli("show", "--repo", "train", cwd=maestro_folder)
        assert result.returncode == 0
        assert "opus" in result.stdout.lower()


class TestErrors:
    def test_missing_platform_yaml_errors(self, tmp_path):
        # Run from a directory without platform.yaml anywhere up the chain
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        result = _run_cli(
            "set-default",
            "--model",
            "sonnet",
            cwd=orphan,
        )
        assert result.returncode == 2
        assert "platform.yaml" in result.stderr

    def test_set_repo_without_repo_arg(self, maestro_folder):
        result = _run_cli(
            "set-repo",
            "--model",
            "sonnet",
            cwd=maestro_folder,
        )
        # argparse catches missing positional
        assert result.returncode != 0
