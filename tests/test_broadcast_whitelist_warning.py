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


def test_task_complete_broadcast_is_refused(tmp_path: Path):
    # bwsv ruling: warn+allow (D5) is retired — a non-broadcast type sent `to: all`
    # is hard-refused at write time, nothing written.
    root = _project_root(tmp_path)
    r = _send(root, "all", "--type", "task-complete")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "failed self-validation" in out or "must not use 'to: all'" in out
    assert list((root / ".agents" / "bus" / "active").glob("*.md")) == []


def test_targeted_task_complete_still_sends(tmp_path: Path):
    root = _project_root(tmp_path)
    r = _send(root, "plugin-agent", "--type", "task-complete")
    assert r.returncode == 0, r.stdout + r.stderr


def test_info_broadcast_is_refused(tmp_path: Path):
    # `info` is not a broadcast type — `to: all` is refused (use `announce`).
    root = _project_root(tmp_path)
    r = _send(root, "all", "--type", "info")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert list((root / ".agents" / "bus" / "active").glob("*.md")) == []


def test_announce_broadcast_is_allowed(tmp_path: Path):
    # `announce` IS the non-privileged fleet-broadcast type — `to: all` is fine.
    root = _project_root(tmp_path)
    r = _send(root, "all", "--type", "announce")
    assert r.returncode == 0, r.stdout + r.stderr
    msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
    assert len(msgs) == 1 and "type: announce" in msgs[0].read_text(encoding="utf-8")
