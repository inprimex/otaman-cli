"""Tests for `otaman blocked --list` and `otaman blocked --clear` (cli-blocked-task-clear).

Covers design.md D2 (list) and D3 (clear):
a) --list with no file returns "No blocked tasks."
b) --list with 2 entries prints both
c) --clear removes target section and preserves other sections
d) --clear on absent slug is a no-op (idempotent)
e) --clear on file with only that section leaves file with empty/minimal content
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal otaman project with cli-agent identity."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    agents = meta / ".agents"
    agents.mkdir()
    (agents / "current-agent").write_text("cli-agent\n")
    blocked_dir = agents / "blocked"
    blocked_dir.mkdir()
    (meta / "platform.yaml").write_text(
        "project: p\nrepos:\n  - name: svc\n    path: ../svc\n    owner: cli-agent\n",
        encoding="utf-8",
    )
    return meta


def _run(meta: Path, *args: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "blocked", *args],
        capture_output=True, text=True, cwd=str(meta), env=env,
    )


BLOCKED_TWO = """\
## Blocked: change-alpha
- **Proposal**: 20260528T113653-proposal.md
- **Blocked since**: 2026-05-28T11:36:53Z
- **Depends on**: spec-change-approved

## Blocked: change-beta
- **Blocked since**: 2026-05-29T09:00:00Z
- **Depends on**: spec-change-approved
"""


# ---------------------------------------------------------------------------
# (a) --list with no file


def test_list_no_file_prints_clean(project: Path) -> None:
    result = _run(project, "--list")
    assert result.returncode == 0
    assert "No blocked tasks." in result.stdout


def test_list_empty_dir_prints_clean(project: Path) -> None:
    # blocked/ dir exists but no agent file
    result = _run(project, "--list")
    assert result.returncode == 0
    assert "No blocked tasks." in result.stdout


# ---------------------------------------------------------------------------
# (b) --list with 2 entries


def test_list_two_entries_prints_both(project: Path) -> None:
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(BLOCKED_TWO, encoding="utf-8")
    result = _run(project, "--list")
    assert result.returncode == 0
    assert "change-alpha" in result.stdout
    assert "change-beta" in result.stdout


def test_list_shows_blocked_since(project: Path) -> None:
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(BLOCKED_TWO, encoding="utf-8")
    result = _run(project, "--list")
    assert "2026-05-28T11:36:53Z" in result.stdout


# ---------------------------------------------------------------------------
# (c) --clear removes target and preserves others


def test_clear_removes_target_section(project: Path) -> None:
    blocked = project / ".agents" / "blocked" / "cli-agent.md"
    blocked.write_text(BLOCKED_TWO, encoding="utf-8")
    result = _run(project, "--clear", "change-alpha")
    assert result.returncode == 0
    remaining = blocked.read_text(encoding="utf-8")
    assert "change-alpha" not in remaining


def test_clear_preserves_other_sections(project: Path) -> None:
    blocked = project / ".agents" / "blocked" / "cli-agent.md"
    blocked.write_text(BLOCKED_TWO, encoding="utf-8")
    _run(project, "--clear", "change-alpha")
    remaining = blocked.read_text(encoding="utf-8")
    assert "change-beta" in remaining


def test_clear_prints_confirmation(project: Path) -> None:
    blocked = project / ".agents" / "blocked" / "cli-agent.md"
    blocked.write_text(BLOCKED_TWO, encoding="utf-8")
    result = _run(project, "--clear", "change-alpha")
    assert "change-alpha" in result.stdout


# ---------------------------------------------------------------------------
# (d) --clear on absent slug is a no-op


def test_clear_absent_slug_no_error(project: Path) -> None:
    blocked = project / ".agents" / "blocked" / "cli-agent.md"
    blocked.write_text(BLOCKED_TWO, encoding="utf-8")
    original = blocked.read_text(encoding="utf-8")
    result = _run(project, "--clear", "nonexistent-change")
    assert result.returncode == 0
    assert blocked.read_text(encoding="utf-8") == original


def test_clear_absent_file_no_error(project: Path) -> None:
    result = _run(project, "--clear", "change-alpha")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# (e) --clear on file with only that section


def test_clear_only_section_leaves_empty_file(project: Path) -> None:
    blocked = project / ".agents" / "blocked" / "cli-agent.md"
    blocked.write_text(
        "## Blocked: change-alpha\n- **Blocked since**: 2026-05-28T00:00:00Z\n",
        encoding="utf-8",
    )
    result = _run(project, "--clear", "change-alpha")
    assert result.returncode == 0
    # File either absent or has no Blocked: sections left
    if blocked.exists():
        assert "## Blocked:" not in blocked.read_text(encoding="utf-8")
