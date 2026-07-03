"""Tests for scripts/_launchers_registry.py and the `maestro launcher` /
`maestro upgrade` CLI subcommands.

The registry is the per-user, per-host inventory of known launcher folders
(``~/.maestro/launchers.yaml``). ``maestro upgrade`` walks it to refresh
each launcher (``git pull`` plug-in checkout + ``maestro init`` on the
maestro folder).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# _launchers_registry is now an importable package module


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path: Path) -> Path:
    """Point the registry at a temp file so tests don't touch ``~/.maestro``."""
    registry = tmp_path / "launchers.yaml"
    monkeypatch.setenv("MAESTRO_LAUNCHERS_REGISTRY", str(registry))
    # Force re-import so module-level cache (none today, defensive) is fresh.
    if "otaman_cli._launchers_registry" in sys.modules:
        del sys.modules["otaman_cli._launchers_registry"]
    return registry


@pytest.fixture
def reg(isolated_registry):
    from otaman_cli import _launchers_registry as r
    return r


def _make_fake_launcher(tmp_path: Path, name: str, conn_type: str = "local") -> Path:
    """Create a launcher folder with a minimal launch-settings.yaml."""
    launcher = tmp_path / name
    launcher.mkdir()
    if conn_type == "local":
        body = (
            "active_connection: c\n"
            "connections:\n"
            "  c:\n"
            "    type: local\n"
            f"    local_root: {tmp_path}\n"
        )
    else:
        body = (
            "active_connection: m\n"
            "connections:\n"
            "  m:\n"
            "    type: ssh\n"
            "    ssh_default_host: user@host\n"
            "    ssh_remote_root: /home/user/proj-maestro\n"
            "    ssh_plugin_path: /home/user/maestro-plugin\n"
        )
    (launcher / "launch-settings.yaml").write_text(body, encoding="utf-8")
    return launcher


# ---------------------------------------------------------------------------
# Registry helpers


class TestRegistryHelpers:

    def test_load_returns_empty_when_missing(self, reg) -> None:
        assert reg.load() == []

    def test_register_adds_new_entry(self, reg, tmp_path: Path) -> None:
        target = tmp_path / "p1"
        target.mkdir()
        was_new, entry = reg.register(target)
        assert was_new is True
        assert entry["path"] == str(target.resolve())
        assert entry["added"]
        assert entry["last_used"]

    def test_register_twice_returns_existing(self, reg, tmp_path: Path) -> None:
        target = tmp_path / "p1"; target.mkdir()
        reg.register(target)
        was_new, _ = reg.register(target)
        assert was_new is False

    def test_register_updates_last_used(self, reg, tmp_path: Path) -> None:
        target = tmp_path / "p1"; target.mkdir()
        _, e1 = reg.register(target)
        first_last = e1["last_used"]
        # Re-register; last_used should be >= first
        _, e2 = reg.register(target)
        assert e2["last_used"] >= first_last
        assert e2["added"] == e1["added"]  # added only set once

    def test_unregister_removes(self, reg, tmp_path: Path) -> None:
        target = tmp_path / "p1"; target.mkdir()
        reg.register(target)
        assert reg.unregister(target) is True
        assert reg.list_entries() == []

    def test_unregister_returns_false_if_absent(self, reg, tmp_path: Path) -> None:
        target = tmp_path / "p1"; target.mkdir()
        assert reg.unregister(target) is False

    def test_list_sorted_by_last_used_descending(
        self, reg, isolated_registry: Path, tmp_path: Path
    ) -> None:
        # Timestamps in register() are per-second, so a tight register/register
        # loop produces ties. Construct the registry directly with explicit
        # different timestamps so the sort assertion is deterministic.
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        import yaml
        isolated_registry.parent.mkdir(exist_ok=True)
        isolated_registry.write_text(
            yaml.safe_dump({
                "launchers": [
                    {"path": str(a.resolve()), "added": "2026-01-01T00:00:00+00:00",
                     "last_used": "2026-01-01T00:00:00+00:00"},
                    {"path": str(b.resolve()), "added": "2026-01-02T00:00:00+00:00",
                     "last_used": "2026-01-02T00:00:00+00:00"},
                ],
            }),
            encoding="utf-8",
        )
        entries = reg.list_entries()
        assert entries[0]["path"].endswith("b")
        assert entries[1]["path"].endswith("a")

    def test_normalisation_dedupes_relative_and_absolute(
        self, reg, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p1"
        target.mkdir()
        monkeypatch.chdir(tmp_path)
        reg.register("p1")
        was_new, _ = reg.register(target)
        assert was_new is False
        assert len(reg.list_entries()) == 1

    def test_malformed_registry_returns_empty(self, reg, isolated_registry) -> None:
        isolated_registry.parent.mkdir(exist_ok=True)
        isolated_registry.write_text("not: valid: yaml: ::: [", encoding="utf-8")
        assert reg.load() == []


# ---------------------------------------------------------------------------
# CLI subcommands


class TestLauncherCli:
    """Run the actual CLI as a subprocess so the dispatch path is exercised."""

    @staticmethod
    def _run(args: list[str], registry: Path, **kwargs):
        cli_invoke = [sys.executable, "-m", "otaman_cli.main"]
        env = os.environ.copy()
        env["MAESTRO_LAUNCHERS_REGISTRY"] = str(registry)
        repo_root = Path(__file__).resolve().parent.parent
        bridge_src = str(repo_root / "src")
        core_src = str(repo_root.parent / "otaman-core" / "src")
        env["PYTHONPATH"] = os.pathsep.join([bridge_src, core_src, env.get("PYTHONPATH", "")])
        return subprocess.run(
            cli_invoke + list(args),
            capture_output=True,
            text=True,
            env=env,
            **kwargs,
        )

    def test_list_empty(self, isolated_registry: Path) -> None:
        r = self._run(["launcher", "list"], isolated_registry)
        assert r.returncode == 0
        assert "No launchers registered" in r.stdout

    def test_add_then_list(self, isolated_registry: Path, tmp_path: Path) -> None:
        launcher = _make_fake_launcher(tmp_path, "myproj")
        r = self._run(["launcher", "add", str(launcher)], isolated_registry)
        assert r.returncode == 0
        assert "Registered" in r.stdout

        r = self._run(["launcher", "list"], isolated_registry)
        assert r.returncode == 0
        assert "Registered Launchers" in r.stdout
        assert str(launcher) in r.stdout

    def test_add_rejects_nonexistent_path(self, isolated_registry: Path) -> None:
        r = self._run(["launcher", "add", "/no/such/path/anywhere"], isolated_registry)
        assert r.returncode != 0
        assert "Not a directory" in (r.stdout + r.stderr)

    def test_register_silent_mode(self, isolated_registry: Path, tmp_path: Path) -> None:
        launcher = _make_fake_launcher(tmp_path, "silent")
        r = self._run(["launcher", "register", str(launcher)], isolated_registry)
        assert r.returncode == 0
        # Silent: no output on success
        assert r.stdout.strip() == ""

    def test_remove_unregisters(self, isolated_registry: Path, tmp_path: Path) -> None:
        launcher = _make_fake_launcher(tmp_path, "tobegone")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["launcher", "remove", str(launcher)], isolated_registry)
        assert r.returncode == 0
        assert "Unregistered" in r.stdout

    def test_remove_unknown_returns_nonzero(self, isolated_registry: Path) -> None:
        r = self._run(["launcher", "remove", "/never/registered"], isolated_registry)
        assert r.returncode != 0
        assert "Not in registry" in (r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# maestro upgrade


class TestMaestroUpgrade:

    @staticmethod
    def _run(args: list[str], registry: Path, **kwargs):
        cli_invoke = [sys.executable, "-m", "otaman_cli.main"]
        env = os.environ.copy()
        env["MAESTRO_LAUNCHERS_REGISTRY"] = str(registry)
        repo_root = Path(__file__).resolve().parent.parent
        bridge_src = str(repo_root / "src")
        core_src = str(repo_root.parent / "otaman-core" / "src")
        env["PYTHONPATH"] = os.pathsep.join([bridge_src, core_src, env.get("PYTHONPATH", "")])
        return subprocess.run(
            cli_invoke + list(args),
            capture_output=True,
            text=True,
            env=env,
            **kwargs,
        )

    def test_upgrade_empty_registry_warns(self, isolated_registry: Path) -> None:
        r = self._run(["upgrade"], isolated_registry)
        assert r.returncode == 0
        assert "No launchers registered" in (r.stdout + r.stderr)

    def test_dry_run_local_launcher(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        launcher = _make_fake_launcher(tmp_path, "local1", conn_type="local")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode == 0
        assert "DRY RUN" in r.stdout
        assert "git" in r.stdout and "pull" in r.stdout
        assert "init" in r.stdout
        assert "1 succeeded" in r.stdout

    def test_dry_run_ssh_launcher(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        launcher = _make_fake_launcher(tmp_path, "ssh1", conn_type="ssh")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode == 0
        # Should emit ssh commands. "--" before the host guards against
        # flag-injection via a hostile ssh_default_host value (F031).
        assert "ssh -- user@host" in r.stdout
        # Both pull and init paths should appear
        assert "git pull" in r.stdout
        assert "bash -l -c 'otaman init'" in r.stdout

    def test_hostile_host_rejected(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        """F031 regression: a launch-settings.yaml ssh_default_host starting
        with '-' must never reach ssh's argv as an unguarded flag (e.g.
        -oProxyCommand=... would execute a local command)."""
        launcher = tmp_path / "hostile_host"
        launcher.mkdir()
        (launcher / "launch-settings.yaml").write_text(
            "active_connection: m\n"
            "connections:\n"
            "  m:\n"
            "    type: ssh\n"
            "    ssh_default_host: -oProxyCommand=touch /tmp/pwned\n"
            "    ssh_remote_root: /home/user/proj-maestro\n"
            "    ssh_plugin_path: /home/user/maestro-plugin\n",
            encoding="utf-8",
        )
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode != 0
        assert "unsafe ssh_default_host" in (r.stdout + r.stderr)
        assert "ProxyCommand" not in (r.stdout + r.stderr) or "Refusing" in (r.stdout + r.stderr)

    def test_hostile_ssh_key_rejected(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        launcher = tmp_path / "hostile_key"
        launcher.mkdir()
        (launcher / "launch-settings.yaml").write_text(
            "active_connection: m\n"
            "connections:\n"
            "  m:\n"
            "    type: ssh\n"
            "    ssh_default_host: user@host\n"
            "    ssh_key: -oProxyCommand=touch /tmp/pwned\n"
            "    ssh_remote_root: /home/user/proj-maestro\n"
            "    ssh_plugin_path: /home/user/maestro-plugin\n",
            encoding="utf-8",
        )
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode != 0
        assert "unsafe ssh_key" in (r.stdout + r.stderr)

    def test_shell_metacharacters_in_remote_paths_are_quoted(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        """F031 regression: plugin_path/maestro_root are interpolated into a
        string the *remote* shell parses (ssh's implicit `sh -c`), so a
        value containing shell metacharacters must come out shell-quoted."""
        import shlex as _shlex

        hostile_plugin_path = "/home/user/plugin; touch /tmp/pwned"
        launcher = tmp_path / "hostile_paths"
        launcher.mkdir()
        (launcher / "launch-settings.yaml").write_text(
            "active_connection: m\n"
            "connections:\n"
            "  m:\n"
            "    type: ssh\n"
            "    ssh_default_host: user@host\n"
            f"    ssh_remote_root: /home/user/proj-maestro\n"
            f"    ssh_plugin_path: '{hostile_plugin_path}'\n",
            encoding="utf-8",
        )
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode == 0
        assert f"cd {_shlex.quote(hostile_plugin_path)}" in r.stdout
        # The raw, unquoted injection payload must never appear bare.
        assert "; touch /tmp/pwned && git pull" not in r.stdout

    def test_dry_run_skip_pull(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        launcher = _make_fake_launcher(tmp_path, "skipp", conn_type="ssh")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run", "--skip-pull"], isolated_registry)
        assert r.returncode == 0
        assert "git pull" not in r.stdout
        assert "bash -l -c 'otaman init'" in r.stdout

    def test_dry_run_skip_init(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        launcher = _make_fake_launcher(tmp_path, "skipi", conn_type="ssh")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run", "--skip-init"], isolated_registry)
        assert r.returncode == 0
        assert "git pull" in r.stdout
        assert "bash -l -c 'otaman init'" not in r.stdout

    def test_missing_launcher_folder_reports_failure(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        # Register then delete the folder
        launcher = _make_fake_launcher(tmp_path, "ghost")
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        import shutil
        shutil.rmtree(launcher)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode != 0
        assert "no longer exists" in (r.stdout + r.stderr)

    def test_unknown_flag_rejected(self, isolated_registry: Path) -> None:
        r = self._run(["upgrade", "--bogus"], isolated_registry)
        assert r.returncode != 0

    def test_extends_chain_resolves(
        self, isolated_registry: Path, tmp_path: Path
    ) -> None:
        """The mesh connections in greenbin/watchtower extend lan; upgrade must
        walk the chain to find ssh_remote_root + ssh_plugin_path that live in
        the parent. Without this, real-world configs report "missing
        ssh_remote_root" and fail.
        """
        launcher = tmp_path / "extendsproj"
        launcher.mkdir()
        (launcher / "launch-settings.yaml").write_text(
            "active_connection: mesh\n"
            "connections:\n"
            "  lan:\n"
            "    type: ssh\n"
            "    ssh_default_host: u@lan-host\n"
            "    ssh_remote_root: /home/u/proj-maestro\n"
            "    ssh_plugin_path: /home/u/maestro-plugin\n"
            "  mesh:\n"
            "    type: ssh\n"
            "    extends: lan\n"
            "    ssh_default_host: u@100.64.0.1\n",  # overrides parent
            encoding="utf-8",
        )
        self._run(["launcher", "add", str(launcher)], isolated_registry)
        r = self._run(["upgrade", "--dry-run"], isolated_registry)
        assert r.returncode == 0, r.stdout + r.stderr
        # Should use the OVERRIDDEN host but inherit ssh_remote_root + plugin
        assert "u@100.64.0.1" in r.stdout
        assert "/home/u/proj-maestro" in r.stdout
        assert "/home/u/maestro-plugin" in r.stdout
        # And NOT the parent's host
        assert "u@lan-host" not in r.stdout
