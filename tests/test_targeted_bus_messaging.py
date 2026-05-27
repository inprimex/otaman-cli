"""Tests for targeted-bus-messaging changes (tasks 2.1-2.4).

Covers:
- _find_task_assignment_sender: routes task-complete to assigner
- cmd_check broadcast labelling (to: all gets '(broadcast)' label)
- --hide-broadcast-older-than filter
"""

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from otaman_cli.main import _find_task_assignment_sender


# ---------------------------------------------------------------------------
# Helpers


def _write_msg(bus_dir: Path, stem: str, frontmatter: dict, body: str = "") -> Path:
    """Write a bus message file."""
    fm_lines = "---\n"
    for k, v in frontmatter.items():
        fm_lines += f"{k}: {v}\n"
    fm_lines += "---\n"
    f = bus_dir / f"{stem}.md"
    f.write_text(fm_lines + body, encoding="utf-8")
    return f


def _make_bus(tmp_path: Path) -> Path:
    bus = tmp_path / ".agents" / "bus" / "active"
    bus.mkdir(parents=True)
    (bus / "acks").mkdir()
    return bus


# ---------------------------------------------------------------------------
# _find_task_assignment_sender


def test_finds_sender_by_change_field(tmp_path: Path) -> None:
    bus = _make_bus(tmp_path)
    _write_msg(bus, "20260101T120000-maestro-to-cli-agent-task-assignment", {
        "type": "task-assignment",
        "from": "maestro",
        "to": "cli-agent",
        "change": "my-feature",
    }, "## Subject: Tasks assigned from my-feature\n")
    result = _find_task_assignment_sender(bus, "my-feature", tmp_path)
    assert result == "maestro"


def test_finds_reply_to_over_from(tmp_path: Path) -> None:
    bus = _make_bus(tmp_path)
    _write_msg(bus, "20260101T120000-maestro-ta", {
        "type": "task-assignment",
        "from": "maestro",
        "reply-to": "spec-agent",
        "change": "my-feature",
    })
    result = _find_task_assignment_sender(bus, "my-feature", tmp_path)
    assert result == "spec-agent"


def test_falls_back_to_human_when_no_task_assignment(tmp_path: Path) -> None:
    bus = _make_bus(tmp_path)
    # Only a spec-change message, no task-assignment
    _write_msg(bus, "20260101T120000-specs-spec-change", {
        "type": "spec-change",
        "from": "otaman-specs",
        "to": "all",
    })
    result = _find_task_assignment_sender(bus, "nonexistent-feature", tmp_path)
    assert result == "human"


def test_does_not_match_wrong_change(tmp_path: Path) -> None:
    bus = _make_bus(tmp_path)
    _write_msg(bus, "20260101T120000-maestro-ta", {
        "type": "task-assignment",
        "from": "maestro",
        "change": "other-feature",
    })
    result = _find_task_assignment_sender(bus, "my-feature", tmp_path)
    assert result == "human"


def test_falls_back_to_human_on_empty_bus(tmp_path: Path) -> None:
    bus = _make_bus(tmp_path)
    result = _find_task_assignment_sender(bus, "any-feature", tmp_path)
    assert result == "human"


# ---------------------------------------------------------------------------
# cmd_check broadcast label and filter
# These tests run the CLI via subprocess to keep test isolation clean.


def _run_check(project_root: Path, agent: str, extra_args: list[str] | None = None) -> str:
    """Run `otaman check <agent>` and return stdout."""
    import subprocess, sys
    cmd = [sys.executable, "-m", "otaman_cli.main", "check", agent] + (extra_args or [])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_root))
    return result.stdout + result.stderr


def _setup_project(tmp_path: Path, agent: str) -> Path:
    """Create a minimal otaman project with a bus directory."""
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text(f"project: test\nrepos: []\n")
    (meta / ".otaman").write_text(f"agent: {agent}\n")
    # Marker in parent dir so find_project_root works from tmp_path
    return meta


def test_broadcast_label_shown_for_to_all(tmp_path: Path) -> None:
    meta = _setup_project(tmp_path, "cli-agent")
    bus = meta / ".agents" / "bus" / "active"
    ts = "20260101T120000"
    _write_msg(bus, f"{ts}-all-broadcast", {
        "id": f"{ts}-all-broadcast",
        "type": "contract-change",
        "from": "core-agent",
        "to": "all",
        "priority": "normal",
        "timestamp": "2026-01-01T12:00:00Z",
        "status": "pending",
    }, "## Subject: contract change\n")
    output = _run_check(meta, "cli-agent")
    assert "(broadcast)" in output


def test_targeted_message_has_no_broadcast_label(tmp_path: Path) -> None:
    meta = _setup_project(tmp_path, "cli-agent")
    bus = meta / ".agents" / "bus" / "active"
    ts = "20260101T120000"
    _write_msg(bus, f"{ts}-targeted", {
        "id": f"{ts}-targeted",
        "type": "task-assignment",
        "from": "maestro",
        "to": "cli-agent",
        "priority": "normal",
        "timestamp": "2026-01-01T12:00:00Z",
        "status": "pending",
    }, "## Subject: task assignment\n")
    output = _run_check(meta, "cli-agent")
    assert "(broadcast)" not in output


def test_hide_broadcast_older_than_hides_old_broadcast(tmp_path: Path) -> None:
    meta = _setup_project(tmp_path, "cli-agent")
    bus = meta / ".agents" / "bus" / "active"
    # Old broadcast (48h ago)
    old_ts_iso = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y%m%dT%H%M%S")
    _write_msg(bus, f"{old_ts}-old-broadcast", {
        "id": f"{old_ts}-old-broadcast",
        "type": "contract-change",
        "from": "core-agent",
        "to": "all",
        "priority": "normal",
        "timestamp": old_ts_iso,
        "status": "pending",
    }, "## Subject: old broadcast\n")
    output = _run_check(meta, "cli-agent", ["--hide-broadcast-older-than", "24"])
    assert "old broadcast" not in output


def test_hide_broadcast_older_than_keeps_recent_broadcast(tmp_path: Path) -> None:
    meta = _setup_project(tmp_path, "cli-agent")
    bus = meta / ".agents" / "bus" / "active"
    # Recent broadcast (1h ago)
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
    _write_msg(bus, f"{recent_ts}-recent-broadcast", {
        "id": f"{recent_ts}-recent-broadcast",
        "type": "contract-change",
        "from": "core-agent",
        "to": "all",
        "priority": "normal",
        "timestamp": recent_iso,
        "status": "pending",
    }, "## Subject: recent broadcast\n")
    output = _run_check(meta, "cli-agent", ["--hide-broadcast-older-than", "24"])
    assert "recent broadcast" in output
