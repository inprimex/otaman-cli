"""Tests for `otaman doctor --org <name>` — ce-bootstrap-harness-deps task 3.3.

Covers:
- All harnesses present → exit 0, ✔ lines
- Missing binary → exit 1, ✗ + actionable install hint
- `doctor` without --org → harness check NOT performed (purely additive)
- min_version too old → exit 1
- Org or runner.harnesses missing → exit 1 with clear error
"""
from __future__ import annotations

import getpass
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from otaman_cli.main import (
    _check_org_harnesses,
    _parse_version_tuple,
    _print_org_harness_report,
    cmd_doctor,
)


# --------------------------------------------------------------------- _parse_version_tuple
class TestParseVersion:
    def test_simple_semver(self):
        assert _parse_version_tuple("2.3.1") == (2, 3, 1)

    def test_v_prefix_stripped(self):
        assert _parse_version_tuple("v2.3.1") == (2, 3, 1)

    def test_trailing_text_ignored(self):
        assert _parse_version_tuple("2.3.1 (Anthropic Claude)") == (2, 3, 1)

    def test_prerelease_suffix_stripped(self):
        assert _parse_version_tuple("v2.0.0-beta.3") == (2, 0, 0)

    def test_empty_returns_none(self):
        assert _parse_version_tuple("") is None
        assert _parse_version_tuple("   ") is None

    def test_non_numeric_returns_none(self):
        assert _parse_version_tuple("abc") is None


# --------------------------------------------------------------------- _check_org_harnesses
@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """A tmp dir that looks just enough like a project root: has platform.yaml."""
    return tmp_path


def _write_platform(root: Path, body: str) -> None:
    (root / "platform.yaml").write_text(body, encoding="utf-8")


def _make_executable(path: Path, content: str = "#!/usr/bin/env bash\necho 'v2.3.1'\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestCheckOrgHarnesses:
    """Use the current test user as the org's system_user, with their real
    home dir as ~<org>.  Stage binaries under ~/.local/bin/<test-prefix-...>
    to avoid clobbering anything real, then point min_version at our stub."""

    def test_no_platform_yaml(self, fake_root: Path):
        rc, results = _check_org_harnesses(fake_root, "any")
        assert rc == 1
        assert "platform.yaml not found" in results[0]["error"]

    def test_org_not_declared(self, fake_root: Path):
        _write_platform(fake_root, "project: x\n")
        rc, results = _check_org_harnesses(fake_root, "ghost")
        assert rc == 1
        assert "not declared" in results[0]["error"]

    def test_org_missing_system_user(self, fake_root: Path):
        _write_platform(fake_root, "orgs:\n  myorg: {}\n")
        rc, results = _check_org_harnesses(fake_root, "myorg")
        assert rc == 1
        assert "system_user" in results[0]["error"]

    def test_org_unknown_system_user(self, fake_root: Path):
        _write_platform(
            fake_root,
            "orgs:\n  myorg:\n    system_user: this-user-does-not-exist-zzzz\n",
        )
        rc, results = _check_org_harnesses(fake_root, "myorg")
        assert rc == 1
        assert "does not exist" in results[0]["error"]

    def test_no_runner_harnesses(self, fake_root: Path, tmp_path: Path):
        user = getpass.getuser()
        _write_platform(
            fake_root,
            f"orgs:\n  myorg:\n    system_user: {user}\n",
        )
        rc, results = _check_org_harnesses(fake_root, "myorg")
        assert rc == 1
        assert "runner.harnesses" in results[0]["error"]

    def test_missing_binary_fails_with_install_hint(self, fake_root: Path):
        user = getpass.getuser()
        _write_platform(
            fake_root,
            f"orgs:\n  myorg:\n    system_user: {user}\n"
            "runner:\n  harnesses:\n"
            "    - {id: claude-code, binary: zzzz-nonexistent-harness-binary}\n",
        )
        rc, results = _check_org_harnesses(fake_root, "myorg")
        assert rc == 1
        assert results[0]["status"] == "missing"
        assert results[0]["harness_id"] == "claude-code"

    def test_present_binary_no_version_pin_passes(self, fake_root: Path):
        user = getpass.getuser()
        home = Path.home()
        bin_name = "otaman-test-harness-present"
        bin_path = home / ".local" / "bin" / bin_name
        try:
            _make_executable(bin_path)
            _write_platform(
                fake_root,
                f"orgs:\n  myorg:\n    system_user: {user}\n"
                "runner:\n  harnesses:\n"
                f"    - {{id: test-h, binary: {bin_name}}}\n",
            )
            rc, results = _check_org_harnesses(fake_root, "myorg")
            assert rc == 0
            assert results[0]["status"] == "ok"
        finally:
            if bin_path.exists():
                bin_path.unlink()

    def test_min_version_satisfied(self, fake_root: Path):
        user = getpass.getuser()
        home = Path.home()
        bin_name = "otaman-test-harness-newver"
        bin_path = home / ".local" / "bin" / bin_name
        try:
            _make_executable(bin_path, "#!/usr/bin/env bash\necho 'v2.5.0'\n")
            _write_platform(
                fake_root,
                f"orgs:\n  myorg:\n    system_user: {user}\n"
                "runner:\n  harnesses:\n"
                f"    - {{id: test-h, binary: {bin_name}, min_version: '2.0.0'}}\n",
            )
            rc, results = _check_org_harnesses(fake_root, "myorg")
            assert rc == 0
            assert results[0]["status"] == "ok"
            assert "2.5.0" in results[0]["version"]
        finally:
            if bin_path.exists():
                bin_path.unlink()

    def test_min_version_too_old(self, fake_root: Path):
        user = getpass.getuser()
        home = Path.home()
        bin_name = "otaman-test-harness-oldver"
        bin_path = home / ".local" / "bin" / bin_name
        try:
            _make_executable(bin_path, "#!/usr/bin/env bash\necho 'v1.0.0'\n")
            _write_platform(
                fake_root,
                f"orgs:\n  myorg:\n    system_user: {user}\n"
                "runner:\n  harnesses:\n"
                f"    - {{id: test-h, binary: {bin_name}, min_version: '2.0.0'}}\n",
            )
            rc, results = _check_org_harnesses(fake_root, "myorg")
            assert rc == 1
            assert results[0]["status"] == "too_old"
        finally:
            if bin_path.exists():
                bin_path.unlink()


# --------------------------------------------------------------------- _print_org_harness_report
class TestPrintReport:
    def test_install_hint_appears_for_missing(self, capsys):
        _print_org_harness_report("myorg", [{
            "harness_id": "claude-code",
            "binary": "claude",
            "status": "missing",
            "path": "/home/u/.local/bin/claude",
        }])
        out = capsys.readouterr().out
        assert "claude-code" in out
        assert "NOT FOUND" in out
        assert "sudo bash ce-bootstrap.sh --org=myorg --install-harness=claude-code" in out

    def test_upgrade_hint_for_too_old(self, capsys):
        _print_org_harness_report("myorg", [{
            "harness_id": "claude-code",
            "binary": "claude",
            "status": "too_old",
            "version": "1.0.0",
            "min_version": "2.0.0",
            "path": "/home/u/.local/bin/claude",
        }])
        out = capsys.readouterr().out
        assert "sudo bash ce-bootstrap.sh --org=myorg --upgrade-harness=claude-code" in out

    def test_ok_line_includes_version(self, capsys):
        _print_org_harness_report("myorg", [{
            "harness_id": "claude-code",
            "binary": "claude",
            "status": "ok",
            "version": "v2.3.1",
        }])
        out = capsys.readouterr().out
        assert "claude-code" in out
        assert "v2.3.1" in out


# --------------------------------------------------------------------- cmd_doctor additive behavior (task 3.2)
class TestDoctorOrgAdditive:
    """`doctor` without --org must not run harness checks."""

    def test_cmd_doctor_signature_accepts_org_kwarg(self):
        """Smoke: the function signature stays backward-compatible."""
        import inspect
        sig = inspect.signature(cmd_doctor)
        assert "org" in sig.parameters
        # org defaults to None — calling without it must work
        assert sig.parameters["org"].default is None

    def test_no_org_skips_harness_helper(self, fake_root: Path, monkeypatch):
        """When --org is absent, _check_org_harnesses must not run.

        We monkeypatch it to raise; if it's called the test fails.  We also
        short-circuit cmd_doctor's heavy doctor.py run by injecting a stub
        report via run_script.
        """
        from otaman_cli import main as m

        def _boom(*_a, **_kw):
            raise AssertionError("_check_org_harnesses must not run when --org is absent")

        monkeypatch.setattr(m, "_check_org_harnesses", _boom)

        # Stub the heavy doctor.py runner to short-circuit
        class _StubResult:
            returncode = 0
            stdout = '{"summary": {"passed": 1, "warned": 0, "failed": 0, "total": 1}, "checks": [], "maestro_dir": "x"}'
            stderr = ""

        monkeypatch.setattr(m, "run_script", lambda *a, **kw: _StubResult())
        monkeypatch.setattr(m, "find_project_root", lambda: fake_root)

        rc = cmd_doctor([], org=None)
        assert rc == 0
