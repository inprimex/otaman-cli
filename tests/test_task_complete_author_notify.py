"""Tests for task-complete-author-notify spec (tasks 1.1, 1.2, 2.1, 2.2, 2.3).

Covers:
- _write_spec_owner writes/updates spec_owner in .openspec.yaml
- _write_spec_owner is idempotent
- _write_spec_owner is a no-op when specs path is missing
- _read_spec_owner returns the value or None when absent
- cmd_complete sends two bus messages when spec_owner differs from recipient
- cmd_complete sends one message when spec_owner is absent
- cmd_complete sends one message when spec_owner == recipient (no duplicate)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.main import _read_spec_owner, _write_spec_owner


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal otaman project: meta + one repo + openspec change."""
    parent = tmp_path / "platform"
    parent.mkdir()

    meta = parent / "platform-meta"
    meta.mkdir()
    (meta / ".agents").mkdir()
    (meta / ".agents" / "bus").mkdir()
    (meta / ".agents" / "bus" / "active").mkdir()
    (meta / ".agents" / "bus" / "active" / "acks").mkdir()
    (meta / ".agents" / "current-agent").write_text("cli-agent\n")

    specs = parent / "platform-specs"
    specs.mkdir()
    changes = specs / "openspec" / "changes"
    changes.mkdir(parents=True)
    change_dir = changes / "my-feature"
    change_dir.mkdir()
    (change_dir / ".openspec.yaml").write_text("schema: spec-driven\ncreated: 2026-05-30\n")
    (change_dir / "tasks.md").write_text("# my-feature\n- [x] 1.1 @otaman-cli do thing\n")

    (meta / "platform.yaml").write_text(
        "project: platform\n"
        "specs:\n"
        "  path: ../platform-specs\n"
        "  format: openspec\n"
        "repos:\n"
        "  - name: svc\n    path: ../svc\n    owner: cli-agent\n",
        encoding="utf-8",
    )
    return meta


# ---------------------------------------------------------------------------
# Task 1.2 — _write_spec_owner


def test_write_spec_owner_creates_field(project: Path) -> None:
    openspec_yaml = project.parent / "platform-specs" / "openspec" / "changes" / "my-feature" / ".openspec.yaml"
    _write_spec_owner(project, "my-feature", "spec-agent")
    text = openspec_yaml.read_text(encoding="utf-8")
    assert "spec_owner: spec-agent" in text


def test_write_spec_owner_preserves_existing_fields(project: Path) -> None:
    openspec_yaml = project.parent / "platform-specs" / "openspec" / "changes" / "my-feature" / ".openspec.yaml"
    _write_spec_owner(project, "my-feature", "spec-agent")
    text = openspec_yaml.read_text(encoding="utf-8")
    assert "schema: spec-driven" in text
    assert "created: 2026-05-30" in text


def test_write_spec_owner_idempotent(project: Path) -> None:
    openspec_yaml = project.parent / "platform-specs" / "openspec" / "changes" / "my-feature" / ".openspec.yaml"
    _write_spec_owner(project, "my-feature", "spec-agent")
    _write_spec_owner(project, "my-feature", "spec-agent")
    text = openspec_yaml.read_text(encoding="utf-8")
    assert text.count("spec_owner:") == 1


def test_write_spec_owner_overwrites_on_reassign(project: Path) -> None:
    openspec_yaml = project.parent / "platform-specs" / "openspec" / "changes" / "my-feature" / ".openspec.yaml"
    _write_spec_owner(project, "my-feature", "spec-agent")
    _write_spec_owner(project, "my-feature", "new-spec-agent")
    text = openspec_yaml.read_text(encoding="utf-8")
    assert "spec_owner: new-spec-agent" in text
    assert "spec-agent" not in text or "new-spec-agent" in text


def test_write_spec_owner_missing_specs_path_no_error(tmp_path: Path) -> None:
    """No specs.path in platform.yaml → silent no-op, no exception."""
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text("project: x\nrepos: []\n", encoding="utf-8")
    _write_spec_owner(meta, "my-feature", "spec-agent")  # must not raise


def test_write_spec_owner_missing_change_dir_no_error(project: Path) -> None:
    """Change dir doesn't exist → silent no-op."""
    _write_spec_owner(project, "nonexistent-change", "spec-agent")  # must not raise


# ---------------------------------------------------------------------------
# Task 2.1 — _read_spec_owner


def test_read_spec_owner_returns_value(project: Path) -> None:
    _write_spec_owner(project, "my-feature", "spec-agent")
    assert _read_spec_owner(project, "my-feature") == "spec-agent"


def test_read_spec_owner_returns_none_when_field_absent(project: Path) -> None:
    assert _read_spec_owner(project, "my-feature") is None


def test_read_spec_owner_returns_none_when_file_missing(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text("project: x\nspecs:\n  path: ../specs\nrepos: []\n", encoding="utf-8")
    assert _read_spec_owner(meta, "nonexistent") is None


def test_read_spec_owner_returns_none_no_specs_path(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text("project: x\nrepos: []\n", encoding="utf-8")
    assert _read_spec_owner(meta, "my-feature") is None


# ---------------------------------------------------------------------------
# Task 2.3 — cmd_complete fanout via subprocess


def _run_complete(meta: Path, change: str, tasks: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "complete", change, "--tasks", tasks],
        capture_output=True, text=True, cwd=str(meta), env=env,
    )


def _active_bus_messages(meta: Path, change: str) -> list[Path]:
    """Return task-complete messages for *change* in the active bus dir."""
    active = meta / ".agents" / "bus" / "active"
    result = []
    for f in active.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"change: {change}" in text and "type: task-complete" in text:
            result.append(f)
    return result


def _make_task_assignment(meta: Path, change: str, sender: str) -> None:
    """Plant a task-assignment bus message so cmd_complete can find the recipient."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    active = meta / ".agents" / "bus" / "active"
    (active / f"{ts}-{sender}-to-cli-agent-task-assignment-{change}.md").write_text(
        f"---\nid: test-{ts}\nfrom: {sender}\nto: cli-agent\npriority: normal\n"
        f"type: task-assignment\nchange: {change}\ntimestamp: 2026-05-30T00:00:00Z\nstatus: pending\n---\n\n"
        f"## Subject: Tasks assigned from \"{change}\"\n",
        encoding="utf-8",
    )


def test_complete_with_spec_owner_sends_two_messages(project: Path) -> None:
    """spec_owner set and != recipient → two task-complete messages.

    Post fix-otaman-complete-task-drift: non-spec-agent runs force
    `to: spec-agent` as the primary recipient (per design.md
    `_send_task_complete_bus_message contract`).  When spec_owner is
    a different value (e.g. "otaman"), the Step 2b fanout still fires
    so the original assigner's role is preserved as a separate copy.
    """
    _write_spec_owner(project, "my-feature", "otaman")  # spec_owner = original assigner
    _make_task_assignment(project, "my-feature", "otaman")

    result = _run_complete(project, "my-feature", "1.1")
    assert result.returncode == 0, result.stderr

    msgs = _active_bus_messages(project, "my-feature")
    recipients = set()
    for m in msgs:
        text = m.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("to:"):
                recipients.add(line.split(":", 1)[1].strip())
    # Primary always spec-agent (cli-agent caller); spec_owner fanout reaches otaman
    assert "spec-agent" in recipients, f"primary recipient missing; messages: {[m.name for m in msgs]}"
    assert "otaman" in recipients, f"spec_owner fanout missing; messages: {[m.name for m in msgs]}"


def test_complete_without_spec_owner_sends_one_message(project: Path) -> None:
    """No spec_owner → only one task-complete message."""
    _make_task_assignment(project, "my-feature", "otaman")

    result = _run_complete(project, "my-feature", "1.1")
    assert result.returncode == 0, result.stderr

    msgs = _active_bus_messages(project, "my-feature")
    assert len(msgs) == 1, f"Expected 1 message, got {len(msgs)}: {[m.name for m in msgs]}"


def test_complete_spec_owner_equals_recipient_sends_one_message(project: Path) -> None:
    """spec_owner == recipient → no duplicate message.

    Post fix-otaman-complete-task-drift: the recipient for non-spec-agent
    runs is hardcoded to "spec-agent".  This test verifies the
    no-duplicate semantics by setting spec_owner == "spec-agent" too.
    """
    _write_spec_owner(project, "my-feature", "spec-agent")
    _make_task_assignment(project, "my-feature", "otaman")

    result = _run_complete(project, "my-feature", "1.1")
    assert result.returncode == 0, result.stderr

    msgs = _active_bus_messages(project, "my-feature")
    assert len(msgs) == 1, f"Expected 1 message, got {len(msgs)}: {[m.name for m in msgs]}"
