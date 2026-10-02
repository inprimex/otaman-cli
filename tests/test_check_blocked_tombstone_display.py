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
        "project: p\nrepos: []\nspecs:\n  path: specs\n",
        encoding="utf-8",
    )
    # A dependency wait's stable ref is its CHANGE, and 1.4 calls an entry stale when
    # nothing can account for its ref — so a change directory has to exist for such an
    # entry to be LIVE at all. The specs path is declared here rather than in the two
    # tests that need it, so the fixture matches a real program.
    for change in ("registry-access-contract", "some-change"):
        (tmp_path / "specs" / "openspec" / "changes" / change).mkdir(parents=True)
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


def _line_with(out: str, needle: str) -> str:
    """The first output line containing *needle*, or "" .

    Used instead of asserting on a whole rendered line: the separator between a
    title and its state is an EM DASH, and the Windows runner decodes the
    subprocess output with the locale codec (cp1252), so a literal
    `"title \u2014 state"` comparison fails there on the character rather than on
    the behaviour. Finding the line and asserting the state within it keeps
    "these two facts are on the SAME row" without pinning a byte sequence that
    three platforms disagree about.
    """
    for line in out.splitlines():
        if needle in line:
            return line
    return ""


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
    """The invariant this test exists for: a tombstoned entry must not hide a
    live one, and must not resurface itself.

    The live entry's RENDERING changed with blocked-entry-lifecycle 1.4 — this
    fixture's bus holds no message for its `**Proposal**:` stem, so the ref
    resolves to nothing and it now renders `[stale]` with the reason instead of
    "waiting for human approval". Still shown, still by title; the entry is
    reported, never auto-removed. The companion test below pins the
    approval-wording path for a ref that DOES resolve, so neither rendering is
    lost.
    """
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        MIXED,
        encoding="utf-8",
    )
    out = _run_check(project)
    assert "BLOCKED TASKS" in out
    assert "Still-active-change" in out
    assert "[stale]" in out
    # Tombstoned entries must not resurface
    assert "Destructive-command safety framework" not in out
    assert "Git-flow / branch-environment configuration" not in out


def test_resolvable_entry_renders_the_approval_wording(project: Path) -> None:
    """The other half of 1.4: when the proposal IS on the bus, the entry is live
    and keeps its "waiting for human approval" rendering."""
    stem = "20260705T000000-cli-agent-to-human-spec-change-request"
    (project / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: cli-agent\nto: human\ntype: spec-change-request\n"
        "---\n\n## Subject: a proposal\n",
        encoding="utf-8",
    )
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(MIXED, encoding="utf-8")
    out = _run_check(project)
    assert "Still-active-change" in out
    assert "waiting for human approval" in out
    assert "[stale]" not in out
    assert "1 live" in out


# ---------------------------------------------------------------------------
# The OTHER kind: an agent waiting on an agent, not on a human.


DEPENDENCY = """\
## Blocked: registry-access-contract-1-2-and-2-1
- **Change**: registry-access-contract
- **Kind**: awaiting-dependency
- **Blocked since**: 2026-10-02T13:21:22Z
- **Blocked by**: core-agent
"""

DEPENDENCY_NO_BLOCKER = """\
## Blocked: something-parked
- **Change**: some-change
- **Kind**: awaiting-dependency
- **Blocked since**: 2026-10-02T13:21:22Z
"""

LEGACY_DEPENDENCY = """\
## Blocked: pre-kind-field-entry
- **Change**: some-change
- **Blocked since**: 2026-09-16T21:01:06Z
- **Blocked by**: plugin-agent
"""


def test_a_dependency_wait_names_the_agent_not_a_human_approval(project: Path) -> None:
    """The defect: every blocked entry rendered as "waiting for human approval".

    An `awaiting-dependency` entry is an agent waiting on another AGENT's code. The
    old wording told the human they owed an approval — the opposite of true — and hid
    WHO the task is waiting on. Measured on this fleet's live file with three such
    entries (core-agent twice, bridge-agent), all three mislabelled.
    """
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(DEPENDENCY, encoding="utf-8")
    out = _run_check(project)
    assert "1 live" in out
    assert "waiting on core-agent" in _line_with(out, "registry-access-contract-1-2-and-2-1")
    assert "waiting for human approval" not in out
    # The change is the stable ref for a dependency wait; the proposal line is not
    # printed, because there is no proposal.
    assert "Change: registry-access-contract" in out
    assert "Proposal:" not in out


def test_a_dependency_wait_with_no_blocker_says_so_rather_than_inventing_one(
    project: Path,
) -> None:
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        DEPENDENCY_NO_BLOCKER, encoding="utf-8"
    )
    out = _run_check(project)
    assert "waiting on a dependency" in out
    assert "no `Blocked by` recorded" in out
    assert "waiting for human approval" not in out


def test_a_legacy_entry_with_no_kind_field_is_still_read_as_a_dependency(
    project: Path,
) -> None:
    """core's `kind` infers it: no proposal ref means a dependency wait. Entries
    written before the field exists are read, not mislabelled."""
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        LEGACY_DEPENDENCY, encoding="utf-8"
    )
    out = _run_check(project)
    assert "waiting on plugin-agent" in out
    assert "waiting for human approval" not in out


def test_both_kinds_in_one_file_each_get_their_own_wording(project: Path) -> None:
    """The regression that matters: fixing one kind must not silence the other."""
    stem = "20260705T000000-cli-agent-to-human-spec-change-request"
    (project / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: cli-agent\nto: human\ntype: spec-change-request\n"
        "---\n\n## Subject: a proposal\n",
        encoding="utf-8",
    )
    (project / ".agents" / "blocked" / "cli-agent.md").write_text(
        MIXED + DEPENDENCY, encoding="utf-8"
    )
    out = _run_check(project)
    assert "2 live" in out
    assert "waiting for human approval" in _line_with(out, "Still-active-change")
    assert f"Proposal: {stem}" in out, "the approval wait keeps its proposal ref"
    assert "waiting on core-agent" in _line_with(out, "registry-access-contract-1-2-and-2-1")
    assert "Change: registry-access-contract" in out
