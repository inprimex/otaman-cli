"""Tests for agent-status-presence PR 2 — hooks + check integration + lifecycle.

Tasks 1.6 (ack hook), 1.7 (complete hook), 1.8 (blocked hook),
1.10 (check fleet integration), 1.12 (lifecycle integration).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------- shared helpers
def _project_root(tmp_path: Path, agent: str = "cli-agent") -> Path:
    (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text(agent, encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        f"repos:\n  - {{name: tst, path: ., owner: {agent}}}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_cli(root: Path, *args: str, agent: str = "cli-agent",
             extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": agent,
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )


def _read_status(root: Path, agent: str = "cli-agent") -> dict | None:
    p = root / ".agents" / "status" / f"{agent}.yaml"
    if not p.is_file():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _plant_task_assignment(root: Path, *, name: str, to: str,
                           task: str = "1.3 build it",
                           change: str = "agent-status-presence",
                           frm: str = "plugin-agent") -> Path:
    body = (
        f"---\n"
        f"id: tst-{name}\n"
        f"from: {frm}\n"
        f"to: {to}\n"
        f"priority: high\n"
        f"type: task-assignment\n"
        f"timestamp: 2026-06-09T10:00:00Z\n"
        f"status: pending\n"
        f"---\n\n"
        f"## Subject: Task assignment: {change}\n\n"
        f"**Task:** {task}\n"
        f"**Change:** {change}\n"
        f"\n"
        f"Do the thing.\n"
    )
    p = root / ".agents" / "bus" / "active" / f"20260609T100000-{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------- task 1.6
class TestAckHook:
    def test_ack_task_assignment_writes_working(self, tmp_path: Path):
        root = _project_root(tmp_path)
        msg = _plant_task_assignment(root, name="a1", to="cli-agent",
                                     task="1.3 build", change="agent-status-presence")
        r = _run_cli(root, "ack", msg.stem)
        assert r.returncode == 0, r.stderr
        s = _read_status(root)
        assert s is not None
        assert s["state"] == "working"
        assert s["task"] == "1.3 build"
        assert s["change"] == "agent-status-presence"

    def test_ack_non_task_assignment_skips_hook(self, tmp_path: Path):
        root = _project_root(tmp_path)
        # Plant a regular info message
        body = (
            "---\n"
            "id: tst-info1\n"
            "from: human\n"
            "to: cli-agent\n"
            "priority: normal\n"
            "type: info\n"
            "timestamp: 2026-06-09T10:00:00Z\n"
            "status: pending\n"
            "---\n\n"
            "## Subject: FYI\n\nBody\n"
        )
        msg = root / ".agents" / "bus" / "active" / "20260609T100000-info1.md"
        msg.write_text(body, encoding="utf-8")
        r = _run_cli(root, "ack", msg.stem)
        assert r.returncode == 0
        # No status file created
        assert _read_status(root) is None

    def test_ack_hook_respects_feature_switch(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        msg = _plant_task_assignment(root, name="a1", to="cli-agent")
        r = _run_cli(root, "ack", msg.stem)
        assert r.returncode == 0
        assert _read_status(root) is None


# ---------------------------------------------------------------- task 1.8
class TestBlockedHook:
    def test_blocked_with_slug_writes_blocked_status(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "blocked", "my-task")
        assert r.returncode == 0, r.stderr
        s = _read_status(root)
        assert s is not None
        assert s["state"] == "blocked"
        assert s["blocked_by"] == "human"

    def test_blocked_with_explicit_blocked_by(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "blocked", "my-task", "--blocked-by", "spec-agent")
        assert r.returncode == 0, r.stderr
        s = _read_status(root)
        assert s["blocked_by"] == "spec-agent"

    def test_blocked_writes_blocked_md_entry(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "blocked", "my-task")
        assert r.returncode == 0
        bf = root / ".agents" / "blocked" / "cli-agent.md"
        assert bf.is_file()
        text = bf.read_text(encoding="utf-8")
        assert "## Blocked: my-task" in text

    def test_blocked_idempotent_when_slug_already_present(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "blocked", "my-task")
        r = _run_cli(root, "blocked", "my-task")
        assert r.returncode == 0
        bf = root / ".agents" / "blocked" / "cli-agent.md"
        # Slug appears exactly once
        assert bf.read_text(encoding="utf-8").count("## Blocked: my-task") == 1

    def test_blocked_list_still_works(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "blocked", "my-task")
        r = _run_cli(root, "blocked", "--list")
        assert r.returncode == 0
        assert "my-task" in r.stdout

    def test_blocked_no_args_prints_usage(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "blocked")
        assert r.returncode == 1
        assert "blocked" in r.stdout.lower() or "blocked" in r.stderr.lower()


# ---------------------------------------------------------------- task 1.7
class TestCompleteHook:
    """The complete hook reads tasks.md from the specs repo.

    For unit-style testing we stage a fake `otaman-specs` sibling dir with
    a `tasks.md` and let `_find_tasks_md_for_change` discover it.
    """

    def _stage_specs_sibling(self, tmp_path: Path, change: str,
                             tasks_md_body: str) -> Path:
        # Project root is tmp_path/<project>; specs at tmp_path/otaman-specs
        proj = tmp_path / "project"
        proj.mkdir()
        _project_root(proj)
        specs = tmp_path / "otaman-specs"
        chdir = specs / "openspec" / "changes" / change
        chdir.mkdir(parents=True)
        (chdir / "tasks.md").write_text(tasks_md_body, encoding="utf-8")
        return proj

    def test_finds_tasks_md_via_sibling(self, tmp_path: Path):
        from otaman_cli.commands.complete import _find_tasks_md_for_change
        proj = self._stage_specs_sibling(tmp_path, "ch1", "# tasks\n- [ ] 1.1\n")
        path = _find_tasks_md_for_change(proj, "ch1")
        assert path is not None
        assert path.name == "tasks.md"

    def test_complete_hook_writes_working_when_tasks_remain(self, tmp_path: Path):
        """All-done detection: still some unchecked → working with task=null."""
        body = (
            "# ch1 — tasks\n"
            "## @otaman-cli\n"
            "- [x] 1.1 @otaman-cli already done\n"
            "- [ ] 1.2 @otaman-cli still pending\n"
        )
        proj = self._stage_specs_sibling(tmp_path, "ch1", body)
        from otaman_cli.commands.complete import _status_hook_after_complete
        _status_hook_after_complete(proj, "cli-agent", "ch1")
        s = _read_status(proj)
        assert s is not None
        assert s["state"] == "working"
        assert s["task"] is None
        assert s["change"] == "ch1"

    def test_complete_hook_writes_idle_when_all_done(self, tmp_path: Path):
        body = (
            "# ch2 — tasks\n"
            "## @otaman-cli\n"
            "- [x] 1.1 @otaman-cli done\n"
            "- [x] 1.2 @otaman-cli also done\n"
        )
        proj = self._stage_specs_sibling(tmp_path, "ch2", body)
        from otaman_cli.commands.complete import _status_hook_after_complete
        _status_hook_after_complete(proj, "cli-agent", "ch2")
        s = _read_status(proj)
        assert s is not None
        assert s["state"] == "idle"
        assert s["task"] is None
        assert s["change"] is None

    def test_complete_hook_ignores_other_agents_unchecked(self, tmp_path: Path):
        """An unchecked task assigned to a DIFFERENT agent must not keep me working."""
        body = (
            "# ch3 — tasks\n"
            "## @otaman-cli\n"
            "- [x] 1.1 @otaman-cli done\n"
            "## @otaman-plugin\n"
            "- [ ] 2.1 @otaman-plugin not mine\n"
        )
        proj = self._stage_specs_sibling(tmp_path, "ch3", body)
        from otaman_cli.commands.complete import _status_hook_after_complete
        _status_hook_after_complete(proj, "cli-agent", "ch3")
        s = _read_status(proj)
        assert s is not None
        assert s["state"] == "idle"


# ---------------------------------------------------------------- task 1.10
class TestCheckFleetIntegration:
    def _plant_status(self, root: Path, agent: str, state: str, **fields):
        sdir = root / ".agents" / "status"
        sdir.mkdir(parents=True, exist_ok=True)
        body = {
            "agent": agent, "state": state, "task": None, "change": None,
            "outcome": None, "blocked_by": None,
            "since": "2026-06-09T10:00:00Z", "updated_at": "2026-06-09T10:00:00Z",
            **fields,
        }
        (sdir / f"{agent}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    def test_fleet_omitted_when_all_idle(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant_status(root, "alpha", "idle")
        self._plant_status(root, "beta", "idle")
        r = _run_cli(root, "check")
        assert r.returncode == 0
        assert "Fleet:" not in r.stdout
        assert "Fleet status" not in r.stdout

    def test_fleet_compact_summary_when_non_idle_but_no_blocked(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant_status(root, "alpha", "working", task="1.3")
        self._plant_status(root, "beta", "idle")
        r = _run_cli(root, "check")
        assert r.returncode == 0
        assert "Fleet:" in r.stdout
        assert "alpha" in r.stdout
        assert "working" in r.stdout

    def test_fleet_full_table_when_any_blocked(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant_status(root, "alpha", "blocked", blocked_by="human")
        self._plant_status(root, "beta", "working", task="t")
        r = _run_cli(root, "check")
        assert r.returncode == 0
        # Full table header
        assert "Fleet status" in r.stdout

    def test_fleet_omitted_when_feature_disabled(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        self._plant_status(root, "alpha", "working", task="t")
        r = _run_cli(root, "check")
        assert "Fleet" not in r.stdout


# ---------------------------------------------------------------- task 1.12 lifecycle
class TestLifecycleIntegration:
    """End-to-end: spawn-like idle → ack working → complete idle."""

    def test_full_lifecycle_via_cli(self, tmp_path: Path):
        # Stage project + specs sibling for the complete hook to find
        proj = tmp_path / "project"
        proj.mkdir()
        _project_root(proj)
        specs = tmp_path / "otaman-specs"
        chdir = specs / "openspec" / "changes" / "lifecycle-test"
        chdir.mkdir(parents=True)
        (chdir / "tasks.md").write_text(
            "# lifecycle-test\n## @otaman-cli\n- [ ] 1.1 @otaman-cli build it\n",
            encoding="utf-8",
        )

        # Step 1: agent starts idle (we simulate by calling set-status idle)
        r = _run_cli(proj, "set-status", "idle")
        assert r.returncode == 0
        s = _read_status(proj)
        assert s["state"] == "idle"

        # Step 2: ack task-assignment → working
        msg = _plant_task_assignment(proj, name="lc1", to="cli-agent",
                                     task="1.1 build it", change="lifecycle-test")
        r = _run_cli(proj, "ack", msg.stem)
        assert r.returncode == 0
        s = _read_status(proj)
        assert s["state"] == "working"
        assert s["task"] == "1.1 build it"
        assert s["change"] == "lifecycle-test"

        # Step 3: mark the task done in tasks.md (simulating actualize-tasks)
        (chdir / "tasks.md").write_text(
            "# lifecycle-test\n## @otaman-cli\n- [x] 1.1 @otaman-cli build it\n",
            encoding="utf-8",
        )
        # Trigger the complete hook directly (cmd_complete also calls
        # actualize-tasks.py which we don't want to run in tests)
        from otaman_cli.commands.complete import _status_hook_after_complete
        _status_hook_after_complete(proj, "cli-agent", "lifecycle-test")
        s = _read_status(proj)
        assert s["state"] == "idle"
        assert s["task"] is None

    def test_blocked_lifecycle(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "set-status", "working", "--task", "t1")
        # block
        r = _run_cli(root, "blocked", "spec-clarification-needed",
                     "--blocked-by", "spec-agent")
        assert r.returncode == 0
        s = _read_status(root)
        assert s["state"] == "blocked"
        assert s["blocked_by"] == "spec-agent"
        # Status file ALSO has the task from before (preserved per spec)
        assert s["task"] == "t1"

    def test_json_schema_stable(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "set-status", "working", "--task", "t", "--change", "ch")
        r = _run_cli(root, "status", "--json")
        data = json.loads(r.stdout)
        assert data["enabled"] is True
        assert "generated_at" in data
        assert isinstance(data["agents"], list)
        a = data["agents"][0]
        for key in ("agent", "state", "task", "change", "outcome",
                    "blocked_by", "since", "updated_at"):
            assert key in a, f"missing field: {key}"

    def test_disabled_suppresses_all(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        # set-status no-op
        _run_cli(root, "set-status", "working", "--task", "t")
        assert _read_status(root) is None
        # status command short-circuits
        r = _run_cli(root, "status")
        assert "disabled" in r.stdout.lower()
        # ack hook no-op
        msg = _plant_task_assignment(root, name="x1", to="cli-agent")
        _run_cli(root, "ack", msg.stem)
        assert _read_status(root) is None
        # blocked hook still writes the blocked entry but no status
        _run_cli(root, "blocked", "x")
        assert _read_status(root) is None
