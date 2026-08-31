"""conformance-2026-09 D5 — `otaman send` broadcast-whitelist warning.

shared-contracts: `task-complete` is a point-to-point signal to the assigner,
not a fleet announcement. Broadcasting it to `all` must WARN (non-blocking) and
point at targeted routing; other types broadcast without warning; a targeted
task-complete never warns.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _project_root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    return tmp_path


def _send(root: Path, to: str, *extra: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": "cli-agent",
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "send",
            to,
            "--subject",
            "s",
            "--body",
            "b",
            *extra,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_task_complete_broadcast_warns_but_sends(tmp_path: Path):
    root = _project_root(tmp_path)
    r = _send(root, "all", "--type", "task-complete")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out  # warn-and-allow, never blocks
    assert "should not broadcast" in out


def test_targeted_task_complete_does_not_warn(tmp_path: Path):
    root = _project_root(tmp_path)
    r = _send(root, "plugin-agent", "--type", "task-complete")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "should not broadcast" not in out


def test_info_broadcast_does_not_warn(tmp_path: Path):
    # the whitelist warning is task-complete-specific; a plain info broadcast is fine
    root = _project_root(tmp_path)
    r = _send(root, "all", "--type", "info")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "should not broadcast" not in out
