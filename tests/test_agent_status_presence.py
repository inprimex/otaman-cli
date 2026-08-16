"""Tests for agent-status-presence tasks 1.1–1.5, 1.9, 1.11.

Hooks (1.6-1.8), check integration (1.10), and integration tests (1.12)
land in a follow-up PR.
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

from otaman_cli.status import (
    AgentStatus,
    FileStatusBackend,
    NatsKvStatusBackend,
    State,
    get_backend,
    is_agent_presence_enabled,
)


# ---------------------------------------------------------------- task 1.1
class TestAgentStatusModel:
    def test_minimum_construct(self):
        s = AgentStatus(agent="alpha", state=State.IDLE)
        assert s.agent == "alpha"
        assert s.state == State.IDLE
        assert s.since
        assert s.updated_at

    def test_to_from_dict_roundtrip(self):
        s = AgentStatus(
            agent="alpha",
            state=State.WORKING,
            task="1.1 build it",
            change="ch-1",
            outcome=None,
            blocked_by=None,
            since="2026-06-09T10:00:00Z",
            updated_at="2026-06-09T10:05:00Z",
        )
        d = s.to_dict()
        assert d["state"] == "working"
        s2 = AgentStatus.from_dict(d)
        assert s2.agent == s.agent
        assert s2.state == s.state
        assert s2.task == s.task
        assert s2.since == s.since
        assert s2.updated_at == s.updated_at

    def test_from_dict_invalid_state_falls_back_to_idle(self):
        s = AgentStatus.from_dict({"agent": "x", "state": "bogus"})
        assert s.state == State.IDLE


# ---------------------------------------------------------------- task 1.2
class TestFileStatusBackend:
    def test_write_creates_file(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        b.write(AgentStatus(agent="alpha", state=State.WORKING, task="t1"))
        path = tmp_path / ".agents" / "status" / "alpha.yaml"
        assert path.is_file()
        data = yaml.safe_load(path.read_text())
        assert data["agent"] == "alpha"
        assert data["state"] == "working"
        assert data["task"] == "t1"

    def test_write_atomic_no_tmpfile_remains(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        b.write(AgentStatus(agent="alpha", state=State.IDLE))
        sdir = tmp_path / ".agents" / "status"
        leftovers = [
            p for p in sdir.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")
        ]
        assert leftovers == []

    def test_read_returns_none_when_absent(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        assert b.read("missing") is None

    def test_read_after_write(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        original = AgentStatus(
            agent="beta",
            state=State.BLOCKED,
            blocked_by="human",
            change="ch",
            task="t",
        )
        b.write(original)
        got = b.read("beta")
        assert got is not None
        assert got.agent == "beta"
        assert got.state == State.BLOCKED
        assert got.blocked_by == "human"

    def test_read_all_globs_directory(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        b.write(AgentStatus(agent="a1", state=State.WORKING))
        b.write(AgentStatus(agent="a2", state=State.IDLE))
        b.write(AgentStatus(agent="a3", state=State.BLOCKED, blocked_by="human"))
        names = sorted(r.agent for r in b.read_all())
        assert names == ["a1", "a2", "a3"]

    def test_read_all_empty_when_no_dir(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        assert b.read_all() == []

    def test_delete_removes_file(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        b.write(AgentStatus(agent="alpha", state=State.IDLE))
        assert (tmp_path / ".agents" / "status" / "alpha.yaml").is_file()
        b.delete("alpha")
        assert not (tmp_path / ".agents" / "status" / "alpha.yaml").exists()

    def test_delete_silent_when_absent(self, tmp_path: Path):
        b = FileStatusBackend(tmp_path)
        b.delete("nope")  # must not raise


# ---------------------------------------------------------------- task 1.3
class TestNatsKvBackend:
    def test_methods_raise_not_implemented(self, tmp_path: Path):
        b = NatsKvStatusBackend(tmp_path)
        with pytest.raises(NotImplementedError):
            b.write(AgentStatus(agent="x", state=State.IDLE))
        with pytest.raises(NotImplementedError):
            b.read("x")
        with pytest.raises(NotImplementedError):
            b.read_all()
        with pytest.raises(NotImplementedError):
            b.delete("x")


# ---------------------------------------------------------------- task 1.4
class TestFeatureSwitch:
    def test_default_true_when_no_platform_yaml(self, tmp_path: Path):
        assert is_agent_presence_enabled(tmp_path) is True

    def test_default_true_when_field_absent(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("project: x\n", encoding="utf-8")
        assert is_agent_presence_enabled(tmp_path) is True

    def test_explicit_true(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("agent_presence: true\n", encoding="utf-8")
        assert is_agent_presence_enabled(tmp_path) is True

    def test_explicit_false(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("agent_presence: false\n", encoding="utf-8")
        assert is_agent_presence_enabled(tmp_path) is False

    def test_nested_platform_form(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "platform:\n  agent_presence: false\n", encoding="utf-8"
        )
        assert is_agent_presence_enabled(tmp_path) is False

    def test_disabled_backend_writes_noop(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("agent_presence: false\n", encoding="utf-8")
        b = get_backend(tmp_path)
        # write must be a no-op
        b.write(AgentStatus(agent="x", state=State.WORKING))
        assert b.read_all() == []
        assert not (tmp_path / ".agents" / "status" / "x.yaml").exists()


# ---------------------------------------------------------------- factory
class TestBackendFactory:
    def test_default_file(self, tmp_path: Path):
        b = get_backend(tmp_path)
        assert isinstance(b, FileStatusBackend)

    def test_explicit_file(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("bus:\n  transport: file\n", encoding="utf-8")
        assert isinstance(get_backend(tmp_path), FileStatusBackend)

    def test_nats_dispatch(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text("bus:\n  transport: nats\n", encoding="utf-8")
        assert isinstance(get_backend(tmp_path), NatsKvStatusBackend)


# ---------------------------------------------------------------- task 1.5 (CLI cmd_set_status)
def _project_root(tmp_path: Path, agent: str = "cli-agent") -> Path:
    """Stage a minimal otaman project root for CLI subprocess tests."""
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "current-agent").write_text(agent, encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        f"repos:\n  - {{name: tst, path: ., owner: {agent}}}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_cli(
    root: Path, *args: str, agent: str = "cli-agent", extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": agent,
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestSetStatusCommand:
    def test_set_working_writes_status_file(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(
            root, "set-status", "working", "--task", "1.5", "--change", "agent-status-presence"
        )
        assert r.returncode == 0, r.stderr
        f = root / ".agents" / "status" / "cli-agent.yaml"
        assert f.is_file()
        data = yaml.safe_load(f.read_text())
        assert data["state"] == "working"
        assert data["task"] == "1.5"
        assert data["change"] == "agent-status-presence"

    def test_set_idle_clears_task_and_change(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "set-status", "working", "--task", "1.5", "--change", "ch")
        r = _run_cli(root, "set-status", "idle")
        assert r.returncode == 0, r.stderr
        data = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        assert data["state"] == "idle"
        assert data["task"] is None
        assert data["change"] is None
        assert data["blocked_by"] is None

    def test_heartbeat_preserves_since(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "set-status", "working", "--task", "t")
        first = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        time.sleep(1.05)  # at least one second so ISO timestamps differ
        _run_cli(root, "set-status", "working", "--task", "t")
        second = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        assert second["since"] == first["since"], "since must be preserved on heartbeat"
        assert second["updated_at"] != first["updated_at"], "updated_at must advance"

    def test_state_change_resets_since(self, tmp_path: Path):
        root = _project_root(tmp_path)
        _run_cli(root, "set-status", "working", "--task", "t")
        first = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        time.sleep(1.05)
        _run_cli(root, "set-status", "waiting")
        second = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        assert second["state"] == "waiting"
        assert second["since"] != first["since"], "state change must reset since"

    def test_blocked_with_default_blocked_by_human(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "set-status", "blocked")
        assert r.returncode == 0, r.stderr
        data = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        assert data["state"] == "blocked"
        assert data["blocked_by"] == "human"

    def test_blocked_with_explicit_blocked_by(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "set-status", "blocked", "--blocked-by", "spec-agent")
        assert r.returncode == 0
        data = yaml.safe_load((root / ".agents" / "status" / "cli-agent.yaml").read_text())
        assert data["blocked_by"] == "spec-agent"

    def test_invalid_state_rejected(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "set-status", "bogus-state")
        assert r.returncode == 2
        assert "Invalid state" in r.stdout or "Invalid state" in r.stderr

    def test_disabled_feature_is_noop(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        r = _run_cli(root, "set-status", "working", "--task", "t")
        assert r.returncode == 0
        # No status file written
        assert not (root / ".agents" / "status" / "cli-agent.yaml").exists()


# ---------------------------------------------------------------- task 1.9 (cmd_fleet_status)
class TestFleetStatusCommand:
    def _plant(self, root: Path, agent: str, state: str, **fields):
        sdir = root / ".agents" / "status"
        sdir.mkdir(parents=True, exist_ok=True)
        body = {
            "agent": agent,
            "state": state,
            "task": None,
            "change": None,
            "outcome": None,
            "blocked_by": None,
            "since": "2026-06-09T10:00:00Z",
            "updated_at": "2026-06-09T10:00:00Z",
            **fields,
        }
        (sdir / f"{agent}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    def test_empty_fleet(self, tmp_path: Path):
        root = _project_root(tmp_path)
        r = _run_cli(root, "status")
        assert r.returncode == 0
        assert "No agents reporting" in r.stdout

    def test_sorts_blocked_first(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant(root, "a-idle", "idle")
        self._plant(root, "z-working", "working", task="t")
        self._plant(root, "m-blocked", "blocked", blocked_by="human")
        r = _run_cli(root, "status")
        assert r.returncode == 0
        out = r.stdout
        idx_blocked = out.find("m-blocked")
        idx_working = out.find("z-working")
        idx_idle = out.find("a-idle")
        assert idx_blocked != -1 and idx_blocked < idx_working < idx_idle

    def test_blocked_filter(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant(root, "alpha", "blocked", blocked_by="human")
        self._plant(root, "beta", "working", task="t")
        r = _run_cli(root, "status", "--blocked")
        assert "alpha" in r.stdout
        assert "beta" not in r.stdout

    def test_agent_filter(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant(root, "alpha", "working", task="ta")
        self._plant(root, "beta", "working", task="tb")
        r = _run_cli(root, "status", "--agent", "alpha")
        assert "alpha" in r.stdout
        assert "beta" not in r.stdout

    def test_json_output(self, tmp_path: Path):
        root = _project_root(tmp_path)
        self._plant(root, "alpha", "working", task="ta", change="ch")
        r = _run_cli(root, "status", "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["enabled"] is True
        assert isinstance(data["agents"], list)
        assert any(a["agent"] == "alpha" for a in data["agents"])

    def test_disabled_feature_message(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        r = _run_cli(root, "status")
        assert r.returncode == 0
        assert "disabled" in r.stdout.lower()

    def test_disabled_feature_json(self, tmp_path: Path):
        root = _project_root(tmp_path)
        (root / "platform.yaml").write_text(
            "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
            "agent_presence: false\n"
            "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
            encoding="utf-8",
        )
        r = _run_cli(root, "status", "--json")
        data = json.loads(r.stdout)
        assert data["enabled"] is False
        assert data["agents"] == []

    def test_repos_flag_falls_through_to_legacy(self, tmp_path: Path):
        """`otaman status --repos` should NOT show fleet view; legacy path runs."""
        root = _project_root(tmp_path)
        self._plant(root, "alpha", "working", task="t")
        r = _run_cli(root, "status", "--repos")
        # Legacy path; may fail or succeed but should NOT contain the fleet header
        assert "Fleet status" not in r.stdout
