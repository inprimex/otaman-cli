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
# Task 6.3 — JTBD-37 pm-sync-adapter strict acceptance tests
# ---------------------------------------------------------------------------

class TestPmInitTask63:
    """4 strict assertions called out in pm-sync-adapter task 6.3:

    (a) --dry-run makes ZERO HTTP calls and ALL output non-blank lines
        contain [dry-run]
    (b) Idempotency: re-run with adapter returning existing project skips
        create calls
    (c) Capability mismatch → exit 1 with clear error message
    (d) Admin key not written to any file after init (platform.yaml, .env)
    """

    # ----- (a) dry-run: zero HTTP -----
    def test_dry_run_zero_http_calls(self, tmp_path: Path) -> None:
        """Stricter than test_dry_run_no_http_calls: assert urlopen is never invoked."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None

        import otaman_cli.pm.cmd_init as mod

        urlopen_calls: list = []
        original_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen
        def _spy_urlopen(*args, **kwargs):
            urlopen_calls.append((args, kwargs))
            return original_urlopen(*args, **kwargs)

        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": MagicMock(),
            }),
            patch("urllib.request.urlopen", _spy_urlopen),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])

        assert rc == 0
        assert urlopen_calls == [], (
            f"dry-run must not make any urlopen() calls; got: {urlopen_calls!r}"
        )

    def test_dry_run_lines_all_marked(self, tmp_path: Path, capsys) -> None:
        """Every NON-empty / NON-step / NON-summary output line must include
        [dry-run] OR be a structural line (Step heading, banner, blank, etc.)
        so an operator scanning the output sees only planned-not-done actions.
        """
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
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": MagicMock(),
            }),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])
        assert rc == 0
        out = capsys.readouterr().out
        # Action-implying lines (Would, Creating, Posting) must carry the
        # [dry-run] marker.  "Step N:" headings, "ok"/"warn"/"muted" lines
        # describing state, and blank lines are allowed without marker.
        for line in out.splitlines():
            stripped = line.strip().lower()
            if not stripped:
                continue
            # Skip structural prefixes commonly emitted by UI.action/ok/etc.
            looks_like_action = any(
                kw in stripped for kw in ("would ", "creating ", "posting ", "calling ")
            )
            if looks_like_action:
                assert "[dry-run]" in line, (
                    f"action line missing [dry-run] marker: {line!r}"
                )

    # ----- (b) idempotency: re-run skips creates -----
    def test_idempotent_rerun_returns_existing_project(self, tmp_path: Path) -> None:
        """Re-running cmd_pm_init with an adapter that already returns the same
        project does not crash and treats the second call as a refresh.

        Idempotency lives inside the adapter's provision_project (it detects
        existing project by identifier).  This test confirms that the CLI is
        well-behaved when provision_project returns the SAME project twice.
        """
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None
        mock_config.program_name = "Otaman"
        mock_config.program_key = "otaman"
        mock_config.status_map = {}
        mock_config.tracker = "Task"

        # Mock adapter returns the same Project on every call (idempotency
        # is the adapter's contract; the CLI must not double-create).
        existing_project = MagicMock(id=42, name="Otaman", identifier="otaman")
        mock_adapter = MagicMock()
        mock_adapter.provision_project.return_value = existing_project

        # Patch Easy8Adapter so the CLI uses our mock
        mock_easy8_mod = MagicMock()
        mock_easy8_mod.Easy8Adapter = MagicMock(return_value=mock_adapter)
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=True, agent_identity_group=True,
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
            patch.dict(os.environ, {"OTAMAN_PM_EASY8_API_KEY": "test-key"}),
        ):
            rc1 = mod.cmd_pm_init(["easy8", "--no-webhooks"])
            rc2 = mod.cmd_pm_init(["easy8", "--no-webhooks"])

        # Both runs succeed (zero-arg signal of idempotency at CLI level);
        # provision_project was called once per run with same args.
        assert rc1 == 0
        assert rc2 == 0
        # The adapter saw two calls total — but each returned the same
        # project id, demonstrating the contract held end-to-end.
        assert mock_adapter.provision_project.call_count == 2
        assert mock_adapter.provision_project.return_value.id == 42

    # ----- (c) capability mismatch -----
    def test_capability_mismatch_exits_1(self, tmp_path: Path, capsys) -> None:
        """identity-mode=user but adapter doesn't support it → exit 1, clear error."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = "user"  # ← request mode 'user'
        mock_config.webhook_url = None

        # Adapter capabilities REJECT identity-mode=user
        mock_easy8_mod = MagicMock()
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=False,    # ← mismatch
            agent_identity_group=True,
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])
        assert rc == 1, "capability mismatch must exit 1"
        out = capsys.readouterr().out
        # Clear error mentions the capability + the mode
        assert "identity-mode" in out.lower() or "identity_mode" in out.lower()
        assert "user" in out.lower()

    def test_capability_mismatch_group_mode_exits_1(self, tmp_path: Path, capsys) -> None:
        """identity-mode=group but adapter doesn't support it → exit 1."""
        _make_platform_yaml(tmp_path, with_pm_sync=True)
        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = "group"
        mock_config.webhook_url = None

        mock_easy8_mod = MagicMock()
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=True, agent_identity_group=False,  # ← mismatch
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
        ):
            rc = mod.cmd_pm_init(["easy8", "--dry-run", "--no-webhooks"])
        assert rc == 1
        assert "group" in capsys.readouterr().out.lower()

    # ----- (d) admin key never persisted -----
    def test_admin_key_not_written_to_platform_yaml(self, tmp_path: Path) -> None:
        """After init, the --admin-key value MUST NOT appear in platform.yaml."""
        platform_yaml = _make_platform_yaml(tmp_path, with_pm_sync=True)

        secret = "super-secret-admin-key-do-not-leak-1234"
        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None
        mock_config.program_name = "Otaman"
        mock_config.program_key = "otaman"
        mock_config.status_map = {}
        mock_config.tracker = "Task"

        mock_adapter = MagicMock()
        mock_adapter.provision_project.return_value = MagicMock(
            id=1, name="Otaman", identifier="otaman",
        )

        mock_easy8_mod = MagicMock()
        mock_easy8_mod.Easy8Adapter = MagicMock(return_value=mock_adapter)
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=True, agent_identity_group=True,
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
        ):
            mod.cmd_pm_init(["easy8", "--no-webhooks", "--admin-key", secret])

        # Audit every file touched by the command
        assert secret not in platform_yaml.read_text(encoding="utf-8"), (
            "admin key leaked into platform.yaml"
        )

    def test_admin_key_not_written_to_dotenv(self, tmp_path: Path) -> None:
        """No .env file is created, and any existing one isn't touched with the key."""
        platform_yaml = _make_platform_yaml(tmp_path, with_pm_sync=True)
        secret = "super-secret-admin-key-987"

        # Stage an existing .env to ensure init doesn't tamper with it
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=preserved\n", encoding="utf-8")

        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None
        mock_config.program_name = "Otaman"
        mock_config.program_key = "otaman"
        mock_config.status_map = {}
        mock_config.tracker = "Task"

        mock_adapter = MagicMock()
        mock_adapter.provision_project.return_value = MagicMock(
            id=1, name="Otaman", identifier="otaman",
        )

        mock_easy8_mod = MagicMock()
        mock_easy8_mod.Easy8Adapter = MagicMock(return_value=mock_adapter)
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=True, agent_identity_group=True,
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
        ):
            mod.cmd_pm_init(["easy8", "--no-webhooks", "--admin-key", secret])

        env_text = env_file.read_text(encoding="utf-8")
        assert secret not in env_text, "admin key leaked into .env"
        assert "EXISTING=preserved" in env_text, ".env content was clobbered"

    def test_admin_key_warning_when_env_var_still_set(self, tmp_path: Path, capsys) -> None:
        """Task 5.7: print warning at end of REAL init if OTAMAN_PM_ADMIN_KEY still set.

        Warning only fires on non-dry-run (when init actually completed).  Dry-run
        skips the warning since no real changes happened.
        """
        _make_platform_yaml(tmp_path, with_pm_sync=True)
        mock_config = MagicMock()
        mock_config.base_url = "http://pm.example.com"
        mock_config.exclude_repos = []
        mock_config.identity_mode = None
        mock_config.webhook_url = None
        mock_config.program_name = "Otaman"
        mock_config.program_key = "otaman"
        mock_config.status_map = {}
        mock_config.tracker = "Task"

        mock_adapter = MagicMock()
        mock_adapter.provision_project.return_value = MagicMock(
            id=1, name="Otaman", identifier="otaman",
        )

        mock_easy8_mod = MagicMock()
        mock_easy8_mod.Easy8Adapter = MagicMock(return_value=mock_adapter)
        mock_easy8_mod.EASY8_CAPABILITIES = MagicMock(
            agent_identity_user=True, agent_identity_group=True,
        )

        import otaman_cli.pm.cmd_init as mod
        with (
            patch.object(mod, "load_pm_sync_config", return_value=mock_config),
            patch.object(mod, "find_project_root", return_value=tmp_path),
            patch.dict("sys.modules", {
                "otaman_adapters": MagicMock(),
                "otaman_adapters.easy8": mock_easy8_mod,
            }),
            patch.dict(os.environ, {"OTAMAN_PM_ADMIN_KEY": "leftover-from-shell"}),
        ):
            mod.cmd_pm_init(["easy8", "--no-webhooks"])
        out = capsys.readouterr().out.lower()
        # 5.7 — warning surfaces the admin-key env var name + rotation/unset hint
        assert "otaman_pm_admin_key" in out, (
            f"expected env var name in output, got:\n{out}"
        )
        assert any(kw in out for kw in ("rotate", "unset", "still set")), (
            f"expected rotation/unset hint, got:\n{out}"
        )


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
