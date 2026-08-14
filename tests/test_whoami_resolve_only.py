"""Tests for `otaman whoami --resolve-only` (F013 security fix).

Lightweight non-interactive wrapper around
`otaman_core.identity.resolve_enforcement_identity()`, for non-Python
callers (the Bash PreToolUse hook) to shell out to instead of
reimplementing the enforcement-identity priority chain. Prints ONLY the
resolved agent name on success; exits 1 with no output when unresolved.

Deliberately narrower than the general `otaman whoami` display chain:
`OTAMAN_AGENT` env var and `.agents/current-agent` are NOT trusted here
(both self-asserted signals any agent's own tool calls can set) — only
the per-repo `.otaman` `agent:` marker is.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_cli(
    root: Path, *args: str, env_overrides: dict | None = None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    env.pop("OTAMAN_AGENT", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _stage_project(tmp_path: Path, *, otaman_marker_agent: str | None = None) -> Path:
    (tmp_path / ".agents").mkdir()
    (tmp_path / "platform.yaml").write_text("project: tst\nrepos: []\n", encoding="utf-8")
    if otaman_marker_agent is not None:
        (tmp_path / ".otaman").write_text(
            f"otaman_root: .\nagent: {otaman_marker_agent}\n",
            encoding="utf-8",
        )
    return tmp_path


class TestResolveOnly:
    def test_otaman_marker_resolves_and_prints_only_the_agent_name(self, tmp_path: Path):
        _stage_project(tmp_path, otaman_marker_agent="cli-agent")
        r = _run_cli(tmp_path, "whoami", "--resolve-only")
        assert r.returncode == 0
        assert r.stdout.strip() == "cli-agent"

    def test_otaman_agent_env_alone_does_not_resolve(self, tmp_path: Path):
        """The whole point of --resolve-only: OTAMAN_AGENT is not trusted."""
        _stage_project(tmp_path, otaman_marker_agent=None)
        r = _run_cli(
            tmp_path, "whoami", "--resolve-only", env_overrides={"OTAMAN_AGENT": "cli-agent"}
        )
        assert r.returncode == 1
        assert r.stdout.strip() == ""

    def test_current_agent_file_alone_does_not_resolve(self, tmp_path: Path):
        _stage_project(tmp_path, otaman_marker_agent=None)
        (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
        r = _run_cli(tmp_path, "whoami", "--resolve-only")
        assert r.returncode == 1
        assert r.stdout.strip() == ""

    def test_no_marker_no_env_exits_1_with_no_output(self, tmp_path: Path):
        _stage_project(tmp_path, otaman_marker_agent=None)
        r = _run_cli(tmp_path, "whoami", "--resolve-only")
        assert r.returncode == 1
        assert r.stdout.strip() == ""

    def test_takes_priority_over_general_whoami_display_logic(self, tmp_path: Path):
        """--resolve-only must short-circuit before the routing/tmux/bus-state
        lookups the general `otaman whoami` performs -- output should be
        exactly the agent name, nothing else."""
        _stage_project(tmp_path, otaman_marker_agent="spec-agent")
        r = _run_cli(tmp_path, "whoami", "--resolve-only")
        assert r.returncode == 0
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert lines == ["spec-agent"]
