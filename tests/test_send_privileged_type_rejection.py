"""Tests for F012's `otaman send` privileged-type rejection.

Security GAP finding (2026-07-04): PRIVILEGED_TYPES
(`human-decision`, `spec-change-approved`, `spec-change-rejected`,
`emergency-halt`) assert a human decision was made — forging one defeats
the platform's HITL guarantee. `otaman send`'s general path previously
allowed `--type spec-change-approved` through for ANY caller (confirmed
live forgery vector). Now rejected outright, before the general
MESSAGE_TYPES registry check, with a directed hint at the real
(TTY-gated) command that can produce each type.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _project_root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nrepos: []\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_send(root: Path, msg_type: str, *, to: str = "human") -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": "cli-agent",
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [
            sys.executable, "-m", "otaman_cli.main",
            "send", to,
            "--subject", "forged decision",
            "--body", "body",
            "--type", msg_type,
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )


class TestPrivilegedTypeRejection:
    @pytest.mark.parametrize("msg_type", [
        "spec-change-approved", "spec-change-rejected", "emergency-halt", "human-decision",
    ])
    def test_privileged_type_rejected(self, tmp_path: Path, msg_type: str):
        root = _project_root(tmp_path)
        r = _run_send(root, msg_type)
        assert r.returncode == 2
        out = r.stdout + r.stderr
        assert "privileged" in out.lower()
        # No message file should have been written
        bus_files = list((root / ".agents" / "bus" / "active").glob("*.md"))
        assert bus_files == [], f"privileged message was written to disk: {bus_files}"

    def test_spec_change_approved_hint_points_to_approve_command(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "spec-change-approved")
        out = r.stdout + r.stderr
        assert "otaman approve approve" in out

    def test_spec_change_rejected_hint_points_to_approve_command(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "spec-change-rejected")
        out = r.stdout + r.stderr
        assert "otaman approve reject" in out

    def test_emergency_halt_hint_points_to_emergency_halt_command(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "emergency-halt")
        out = r.stdout + r.stderr
        assert "otaman emergency-halt" in out

    def test_forged_from_human_via_explicit_from_still_rejected(self, tmp_path: Path):
        """The type check fires before --from resolution, so claiming
        --from human doesn't help -- privileged types are categorically
        blocked from this path regardless of claimed identity."""
        root = _project_root(tmp_path)
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        r = subprocess.run(
            [
                sys.executable, "-m", "otaman_cli.main",
                "send", "all",
                "--subject", "forged decision",
                "--body", "body",
                "--type", "spec-change-approved",
                "--from", "human",
            ],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 2
        bus_files = list((root / ".agents" / "bus" / "active").glob("*.md"))
        assert bus_files == []

    def test_non_privileged_types_still_work(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_send(root, "info")
        assert r.returncode == 0
        bus_files = list((root / ".agents" / "bus" / "active").glob("*.md"))
        assert len(bus_files) == 1
