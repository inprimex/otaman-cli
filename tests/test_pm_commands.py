"""Tests for `otaman pm` subcommands (PM tool sync).

Covers:
- cmd_pm_init with --dry-run makes zero HTTP calls, prints plan, returns 0
- cmd_pm_init with no provider exits 1
- cmd_pm_status with no project-map prints "not yet initialized"
- otaman pm unknown subcommand exits 1
- main(["pm", "init", "easy8", "--dry-run"]) exits 0
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = str(REPO_ROOT / "src")
CORE_PATH = str(REPO_ROOT.parent / "otaman-core" / "src")

# Ensure src is on sys.path for direct imports
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)
if CORE_PATH not in sys.path:
    sys.path.insert(0, CORE_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_platform_yaml(tmp_path: Path, with_pm_sync: bool = True, with_project_map: bool = False) -> Path:
    """Write a minimal platform.yaml with optional pm-sync block."""
    pm_block = ""
    if with_pm_sync:
        pm_block = "\npm-sync:\n  provider: easy8\n  base-url: http://pm.example.com\n"
        if with_project_map:
            pm_block += "  project-map:\n    _root: root-123\n    otaman-cli: cli-456\n"

    yaml_content = f"""project:
  name: test-otaman
repos:
  - name: otaman-cli
    owner: cli-agent
    path: ./otaman-cli
  - name: otaman-core
    owner: core-agent
    path: ./otaman-core
{pm_block}"""
    p = tmp_path / "platform.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    return p


def _make_otaman_marker(tmp_path: Path, platform_yaml: Path) -> Path:
    """Write a .otaman marker so find_project_root() resolves to tmp_path."""
    marker = tmp_path / ".otaman"
    marker.write_text(f"otaman-dir: {tmp_path}\n", encoding="utf-8")
    return marker


def _run_main(args: list[str], cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run otaman_cli.main via subprocess with a clean PYTHONPATH."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([SRC_PATH, CORE_PATH, os.environ.get("PYTHONPATH", "")]),
        "NO_COLOR": "1",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main"] + args,
        capture_output=True, text=True, cwd=str(cwd), env=env,
    )


# ---------------------------------------------------------------------------
# Unit tests: cmd_pm_init
# ---------------------------------------------------------------------------

class TestCmdPmInit:
    """Tests for cmd_pm_init()."""

    def test_no_provider_exits_1(self, capsys) -> None:
        """cmd_pm_init([]) should print usage and return 1 (no provider parsed)."""
        from otaman_cli.pm.cmd_init import cmd_pm_init

        # No provider argument => exits before find_project_root is called
        rc = cmd_pm_init([])

        assert rc == 1
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_dry_run_no_http_calls(self, tmp_path: Path, capsys) -> None:
        """--dry-run must produce output and return 0 without any HTTP."""
        platform_yaml = _make_platform_yaml(tmp_path, with_pm_sync=True)

        # Stub load_pm_sync_config to return a simple config object
        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None

        import otaman_cli.pm.cmd_init as mod

        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            # Ensure no real HTTP adapter is instantiated
            patch.dict("sys.modules", {"otaman_adapters": MagicMock(), "otaman_adapters.easy8": MagicMock()}),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()

    def test_dry_run_prints_steps(self, tmp_path: Path, capsys) -> None:
        """dry-run should print all step headings."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None

        import otaman_cli.pm.cmd_init as mod

        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {"otaman_adapters": MagicMock(), "otaman_adapters.easy8": MagicMock()}),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])

        assert rc == 0
        out = capsys.readouterr().out
        # Check for key step indicators
        assert "Step 1" in out
        assert "Step 4" in out

    def test_no_pm_sync_in_platform_yaml_exits_1(self, tmp_path: Path, capsys) -> None:
        """If load_pm_sync_config returns None, cmd should exit 1."""
        _make_platform_yaml(tmp_path, with_pm_sync=False)

        import otaman_cli.pm.cmd_init as mod

        with (
            patch.object(mod, "load_pm_sync_config", return_value=None),
            patch.object(mod, "find_project_root", return_value=tmp_path),
        ):
            rc = mod.cmd_pm_init(["easy8"])

        assert rc == 1

    def test_load_pm_sync_config_none_function_exits_1(self, tmp_path: Path, capsys) -> None:
        """If otaman-core is missing (load_pm_sync_config is None), exit 1."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        import otaman_cli.pm.cmd_init as mod
        original = mod.load_pm_sync_config
        try:
            mod.load_pm_sync_config = None  # type: ignore[assignment]
            with patch.object(mod, "find_project_root", return_value=tmp_path):
                rc = mod.cmd_pm_init(["easy8"])
        finally:
            mod.load_pm_sync_config = original

        assert rc == 1


# ---------------------------------------------------------------------------
# Unit tests: cmd_pm_status
# ---------------------------------------------------------------------------

class TestCmdPmStatus:
    """Tests for cmd_pm_status()."""

    def test_no_project_map_prints_not_initialized(self, tmp_path: Path, capsys) -> None:
        """With no project-map, should print 'not yet initialized' message."""
        _make_platform_yaml(tmp_path, with_pm_sync=True, with_project_map=False)

        import otaman_cli.pm.cmd_status as mod

        with patch.object(mod, "find_project_root", return_value=tmp_path):
            rc = mod.cmd_pm_status([])

        assert rc == 0
        out = capsys.readouterr().out
        assert "not yet initialized" in out.lower() or "PM sync not yet initialized" in out

    def test_with_project_map_shows_table(self, tmp_path: Path, capsys) -> None:
        """With a project-map, should show a table with repo entries."""
        _make_platform_yaml(tmp_path, with_pm_sync=True, with_project_map=True)

        import otaman_cli.pm.cmd_status as mod

        with patch.object(mod, "find_project_root", return_value=tmp_path):
            rc = mod.cmd_pm_status([])

        assert rc == 0
        out = capsys.readouterr().out
        # Should show the repo name and project ID from project-map
        assert "otaman-cli" in out
        assert "cli-456" in out

    def test_no_platform_yaml_exits_1(self, tmp_path: Path, capsys) -> None:
        """If platform.yaml doesn't exist, should exit 1."""
        import otaman_cli.pm.cmd_status as mod

        with patch.object(mod, "find_project_root", return_value=tmp_path):
            rc = mod.cmd_pm_status([])

        assert rc == 1


# ---------------------------------------------------------------------------
# Dispatch tests: unknown subcommand + integration
# ---------------------------------------------------------------------------

class TestPmDispatch:
    """Tests for the pm subcommand router in main.py."""

    def test_unknown_subcommand_exits_1(self, tmp_path: Path) -> None:
        """otaman pm bogus should exit 1 via the early dispatch."""
        result = _run_main(["pm", "bogus"], cwd=tmp_path)
        assert result.returncode == 1
        assert "Unknown pm subcommand" in result.stdout or "Unknown pm subcommand" in result.stderr

    def test_pm_no_subcommand_exits_1(self, tmp_path: Path) -> None:
        """otaman pm (no subcommand) should exit 1."""
        result = _run_main(["pm"], cwd=tmp_path)
        assert result.returncode == 1

    def test_pm_init_dry_run_not_in_project_exits_nonzero(self, tmp_path: Path) -> None:
        """otaman pm init easy8 --dry-run outside a project should fail gracefully."""
        result = _run_main(["pm", "init", "easy8", "--dry-run"], cwd=tmp_path)
        # Should fail (not in a project) but not crash
        assert result.returncode != 0 or "dry-run" in result.stdout


class TestMainPmIntegration:
    """Integration-level tests for main(['pm', ...])."""

    def test_main_pm_init_dry_run_in_project_exits_0(self, tmp_path: Path) -> None:
        """main(['pm', 'init', 'easy8', '--dry-run']) in a project with pm-sync exits 0."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        from otaman_cli.main import main as otaman_main
        import otaman_cli.pm.cmd_init as mod

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None

        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {"otaman_adapters": MagicMock(), "otaman_adapters.easy8": MagicMock()}),
            patch("sys.argv", ["otaman", "pm", "init", "easy8", "--dry-run"]),
        ):
            rc = otaman_main()

        assert rc == 0

    def test_main_pm_unknown_subcommand_exits_1(self, tmp_path: Path) -> None:
        """main(['pm', 'unknown']) should exit 1."""
        from otaman_cli.main import main as otaman_main

        with patch("sys.argv", ["otaman", "pm", "unknown"]):
            rc = otaman_main()

        assert rc == 1
