"""Regression test: `otaman check` must not display tombstoned blocked entries.

Found while investigating fleet state manually: `.agents/blocked/<agent>.md`
entries cleared via `otaman blocked --clear` / `otaman blocked clear <stem>`
are tombstoned in place — wrapped in an HTML comment
(`<!-- ## Blocked: ... cleared YYYY-MM-DD — manually-cleared -->`) rather
than deleted. `cmd_check`'s blocked-section parser split on the literal
substring `"\n## Blocked: "`, which never matches a tombstoned entry (the
line actually reads `<!-- ## Blocked: ...`), so the entire file — including
already-cleared entries — was treated as one active block and kept nagging
"waiting for human approval" forever.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / ".agents" / "blocked").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent\n")
    (tmp_path / "platform.yaml").write_text(
        "project: p\nrepos: []\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_check(project_root: Path, agent: str = "cli-agent") -> str:
    # Explicit env: the autouse isolate_bus fixture pins OTAMAN_ROOT at a
    # sandbox; an inherited env would redirect the subprocess there instead
    # of this test's own fixture tree. PYTHONPATH propagation also lets the
    # subprocess resolve otaman_cli in sibling-checkout dev setups.
    import os

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    result = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "check", agent],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env=env,
    )
    return result.stdout + result.stderr


TOMBSTONED_ONLY = """\
<!-- ## Blocked: Destructive-command safety framework
- **Proposal**: 20260704T082401-cli-agent-to-human-spec-change-request
- **Blocked since**: 2026-07-04T08:24:01Z
- **Depends on**: spec-change-approved + spec-change notification
- **Task to resume**: Implement feature after spec is committed
cleared 2026-07-04 — manually-cleared -->
<!-- ## Blocked: Git-flow / branch-environment configuration
- **Proposal**: 20260704T082414-cli-agent-to-human-spec-change-request
- **Blocked since**: 2026-07-04T08:24:14Z
- **Depends on**: spec-change-approved + spec-change notification
- **Task to resume**: Implement feature after spec is committed
cleared 2026-07-04 — manually-cleared -->
"""

MIXED = (
    TOMBSTONED_ONLY
    + """\
## Blocked: Still-active-change
- **Proposal**: 20260705T000000-cli-agent-to-human-spec-change-request
- **Blocked since**: 2026-07-05T00:00:00Z
- **Depends on**: spec-change-approved + spec-change notification
"""
)


def test_all_tombstoned_entries_produce_no_blocked_section(project: Path) -> None:
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        TOMBSTONED_ONLY,
        encoding="utf-8",
    )
    out = _run_check(project)
    assert "BLOCKED TASKS" not in out
    assert "Destructive-command safety framework" not in out
    assert "Git-flow / branch-environment configuration" not in out


def test_active_entry_still_shown_alongside_tombstoned(project: Path) -> None:
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        MIXED,
        encoding="utf-8",
    )
    out = _run_check(project)
    assert "BLOCKED TASKS" in out
    assert "Still-active-change" in out
    assert "waiting for human approval" in out
    # Tombstoned entries must not resurface
    assert "Destructive-command safety framework" not in out
    assert "Git-flow / branch-environment configuration" not in out
