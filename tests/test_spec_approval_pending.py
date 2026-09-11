"""spec-gate-hardening 1.4 — awaiting-approval visibility.

`otaman propose` enqueues a `spec-approval-pending` item for the human; `otaman
check` surfaces the current agent's own such items in a dedicated section.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: t\nversion: '1.0'\nrepos:\n  - {name: t, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(root: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": "cli-agent",
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "NO_COLOR": "1",
    }
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *argv],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_propose_enqueues_spec_approval_pending(tmp_path):
    root = _root(tmp_path)
    r = _run(root, "propose", "add pagination")
    assert r.returncode == 0, r.stderr + r.stdout
    active = root / ".agents" / "bus" / "active"
    sap = list(active.glob("*spec-approval-pending*.md"))
    assert len(sap) == 1
    text = sap[0].read_text(encoding="utf-8")
    assert "to: human" in text
    assert "type: spec-approval-pending" in text
    assert "from: cli-agent" in text
    assert "add pagination" in text
    # the enqueued item passes the validator (core added the type)
    from otaman_core.validate_message import validate_message

    assert validate_message(sap[0])[0] == []


def test_check_surfaces_awaiting_approval(tmp_path):
    root = _root(tmp_path)
    _run(root, "propose", "add pagination")
    r = _run(root, "check", "cli-agent")
    out = r.stdout + r.stderr
    assert "awaiting approval" in out.lower()
    assert "add pagination" in out


def test_check_awaiting_only_for_own_items(tmp_path):
    # a spec-approval-pending from ANOTHER agent must not appear in cli-agent's
    # awaiting section.
    root = _root(tmp_path)
    other = (
        "---\nid: 20260911T130000-spec-agent-to-human-spec-approval-pending\n"
        "from: spec-agent\nto: human\npriority: normal\ntype: spec-approval-pending\n"
        "timestamp: 2026-09-11T13:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Approval pending: someone elses thing\n\nbody\n"
    )
    (root / ".agents" / "bus" / "active"
     / "20260911T130000-spec-agent-to-human-spec-approval-pending.md").write_text(
        other, encoding="utf-8"
    )  # fmt: skip
    r = _run(root, "check", "cli-agent")
    out = r.stdout + r.stderr
    assert "someone elses thing" not in out


def test_check_surfaces_authored_awaiting_ratification(tmp_path):
    # F3: an AUTHORED change this agent owns (spec_owner) awaiting ratification —
    # no spec-approval-pending item exists for it, but it must still appear.
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    specs = tmp_path / "prog-specs"
    chg = specs / "openspec" / "changes" / "my-change"
    chg.mkdir(parents=True)
    (chg / ".openspec.yaml").write_text(
        "stage: authored\nspec_owner: cli-agent\n", encoding="utf-8"
    )
    (root / "platform.yaml").write_text(
        "project: t\nversion: '1.0'\nspecs:\n  path: ../prog-specs\n"
        "repos:\n  - {name: t, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    out = _run(root, "check", "cli-agent").stdout
    assert "awaiting ratification" in out and "my-change" in out


def test_check_does_not_surface_others_authored(tmp_path):
    # a change owned by ANOTHER agent must not appear in cli-agent's section
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    specs = tmp_path / "prog-specs"
    chg = specs / "openspec" / "changes" / "theirs"
    chg.mkdir(parents=True)
    (chg / ".openspec.yaml").write_text(
        "stage: authored\nspec_owner: core-agent\n", encoding="utf-8"
    )
    (root / "platform.yaml").write_text(
        "project: t\nversion: '1.0'\nspecs:\n  path: ../prog-specs\n"
        "repos:\n  - {name: t, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    out = _run(root, "check", "cli-agent").stdout
    assert "theirs" not in out
