"""Tests for scripts/install_cli.py — PATH / symlink setup."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

# install_cli is now a package module
from otaman_cli import install_cli


# ---------------------------------------------------------------------------
# POSIX install (unix + mac + WSL; skip on Windows because symlinks need
# admin / developer-mode shenanigans)


IS_POSIX = os.name != "nt"
pytestmark_posix = pytest.mark.skipif(not IS_POSIX, reason="POSIX-only path")


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A fake ~/.local/bin we can inspect. Also clear PATH so the hint
    code is deterministic."""
    bin_dir = tmp_path / "bin"
    # PATH without our bin_dir — hint code should print the rc line.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return bin_dir


class TestPosixDryRun:
    def test_prints_what_it_would_do(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        out = io.StringIO()
        rc = install_cli.posix_install(fake_bin, apply=False, out=out)
        assert rc == 0
        text = out.getvalue()
        assert "Would symlink" in text
        assert str(fake_bin / "otaman") in text
        assert "--apply" in text
        # Nothing was actually created.
        assert not fake_bin.exists()


class TestPosixApply:
    def test_creates_symlink(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        out = io.StringIO()
        rc = install_cli.posix_install(fake_bin, apply=True, out=out)
        assert rc == 0
        target = fake_bin / "otaman"
        assert target.is_symlink()
        assert target.resolve() == install_cli.POSIX_LAUNCHER.resolve()

    def test_idempotent(self, fake_bin):
        """Running install twice is a no-op on second run."""
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        out = io.StringIO()
        install_cli.posix_install(fake_bin, apply=True, out=out)
        out2 = io.StringIO()
        rc = install_cli.posix_install(fake_bin, apply=True, out=out2)
        assert rc == 0
        assert "OK:" in out2.getvalue()

    def test_refuses_to_clobber_regular_file(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        fake_bin.mkdir(parents=True)
        regular = fake_bin / "otaman"
        regular.write_text("#!/bin/sh\necho hand-rolled\n", encoding="utf-8")
        out = io.StringIO()
        rc = install_cli.posix_install(fake_bin, apply=True, out=out)
        assert rc == 1
        assert "NOT a symlink" in out.getvalue()
        # Preserved.
        assert regular.read_text(encoding="utf-8").startswith("#!/bin/sh")


class TestPosixUninstall:
    def test_dry_run(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        fake_bin.mkdir(parents=True)
        (fake_bin / "otaman").symlink_to(install_cli.POSIX_LAUNCHER)
        out = io.StringIO()
        rc = install_cli.posix_uninstall(fake_bin, apply=False, out=out)
        assert rc == 0
        assert "Would remove" in out.getvalue()
        assert (fake_bin / "otaman").is_symlink()

    def test_apply_removes_symlink(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        fake_bin.mkdir(parents=True)
        (fake_bin / "otaman").symlink_to(install_cli.POSIX_LAUNCHER)
        out = io.StringIO()
        rc = install_cli.posix_uninstall(fake_bin, apply=True, out=out)
        assert rc == 0
        assert not (fake_bin / "otaman").exists()

    def test_refuses_non_symlink(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        fake_bin.mkdir(parents=True)
        (fake_bin / "otaman").write_text("x", encoding="utf-8")
        out = io.StringIO()
        rc = install_cli.posix_uninstall(fake_bin, apply=True, out=out)
        assert rc == 1
        assert "not a symlink" in out.getvalue()

    def test_nothing_to_do(self, fake_bin):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        out = io.StringIO()
        rc = install_cli.posix_uninstall(fake_bin, apply=True, out=out)
        assert rc == 0
        assert "Nothing to do" in out.getvalue()


class TestPosixPathHint:
    def test_already_on_path_no_hint(self, tmp_path, monkeypatch):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        bin_dir = tmp_path / "bin"
        monkeypatch.setenv("PATH", f"/usr/bin:{bin_dir}:/bin")
        out = io.StringIO()
        install_cli._posix_path_hint(bin_dir, out=out)
        assert "NOT on your PATH" not in out.getvalue()

    def test_missing_prints_rc_line(self, tmp_path, monkeypatch):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        bin_dir = tmp_path / "bin"
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("SHELL", "/bin/zsh")
        out = io.StringIO()
        install_cli._posix_path_hint(bin_dir, out=out)
        text = out.getvalue()
        assert "NOT on your PATH" in text
        assert ".zshrc" in text
        assert str(bin_dir) in text


# ---------------------------------------------------------------------------
# Windows (cross-platform tests — the function just has to print the
# right diagnostic; actual setx is not invoked unless apply=True)


class TestWindowsDryRun:
    def test_prints_what_it_would_do(self, monkeypatch):
        # Force the function to think we're on Windows without relying on
        # os.name: patch the helpers it uses.
        monkeypatch.setattr(install_cli, "windows_current_user_path",
                            lambda: "C:\\Windows\\System32;C:\\Windows")
        out = io.StringIO()
        rc = install_cli.windows_install(apply=False, out=out)
        assert rc == 0
        text = out.getvalue()
        assert "Would prepend" in text
        assert str(install_cli.CLI_DIR) in text
        assert "--apply" in text

    def test_already_present_noop(self, monkeypatch):
        existing = str(install_cli.CLI_DIR)
        monkeypatch.setattr(install_cli, "windows_current_user_path",
                            lambda: f"C:\\Windows;{existing}")
        out = io.StringIO()
        rc = install_cli.windows_install(apply=False, out=out)
        assert rc == 0
        assert "already on the User PATH" in out.getvalue()


class TestWindowsUninstall:
    def test_prints_setx_command(self, monkeypatch):
        existing = str(install_cli.CLI_DIR)
        monkeypatch.setattr(install_cli, "windows_current_user_path",
                            lambda: f"C:\\Windows;{existing};C:\\bin")
        out = io.StringIO()
        rc = install_cli.windows_uninstall(apply=False, out=out)
        assert rc == 0
        text = out.getvalue()
        assert "setx PATH" in text
        assert existing not in text.split("setx PATH", 1)[1]  # filtered out

    def test_nothing_to_do_when_absent(self, monkeypatch):
        monkeypatch.setattr(install_cli, "windows_current_user_path",
                            lambda: "C:\\Windows;C:\\bin")
        out = io.StringIO()
        rc = install_cli.windows_uninstall(apply=False, out=out)
        assert rc == 0
        assert "Nothing to do" in out.getvalue()


# ---------------------------------------------------------------------------
# Orchestration (run)


class TestRun:
    def test_dry_run_default(self, monkeypatch, tmp_path):
        """run() with no --apply must not mutate anything."""
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        bin_dir = tmp_path / "bin"
        monkeypatch.setenv("PATH", "/usr/bin")
        rc = install_cli.run(["--bin-dir", str(bin_dir)])
        assert rc == 0
        assert not bin_dir.exists()

    def test_uninstall_dry_run(self, monkeypatch, tmp_path):
        if not IS_POSIX:
            pytest.skip("POSIX-only")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "otaman").symlink_to(install_cli.POSIX_LAUNCHER)
        rc = install_cli.run([
            "--uninstall", "--bin-dir", str(bin_dir),
        ])
        assert rc == 0
        # Dry-run: symlink still there.
        assert (bin_dir / "otaman").is_symlink()
