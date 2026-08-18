"""GATE BLOCKER 2/2 (spec-agent 20260818T195901) — `otaman init --update`
must work headless from a repo dir AND produce CLAUDE.local.md.

The migration gate's step 2 tells every repo owner to run `otaman init
--update` from their repo in a headless session. Two properties verified
end-to-end here:

1. Root resolves via the repo's `.otaman` marker (no platform.yaml in
   cwd, no TTY) — the non-update path's interactive preflight is never
   hit.
2. The generator runs as part of --update, writing each repo's
   gitignored CLAUDE.local.md orchestration rules (plugin af48483
   mechanism) — previously --update skipped the generator entirely, so
   the gate's own command couldn't deliver the migration.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """meta dir + one owned repo with a .otaman marker; returns (meta, repo)."""
    meta = tmp_path / "proj-otaman"
    (meta / ".agents").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: proj\nversion: '1.0'\nrepos:\n"
        "  - name: svc-api\n    path: ../svc-api\n    owner: backend-agent\n",
        encoding="utf-8",
    )
    repo = tmp_path / "svc-api"
    repo.mkdir()
    (repo / ".otaman").write_text(
        "# Path to otaman folder\n../proj-otaman\nagent: backend-agent\n", encoding="utf-8"
    )
    return meta, repo


def _run_update(cwd: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "NO_COLOR": "1",
        # the marker security guard rejects targets outside $HOME; scope
        # HOME to the fixture tree so the tmp-path marker is in-bounds
        "HOME": str(cwd.parent),
    }
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT", "OTAMAN_AGENT"):
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "init", "--update"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,  # headless: no TTY, closed stdin
        timeout=120,
    )


def test_update_headless_from_repo_dir_writes_claude_local_md(tmp_path: Path):
    meta, repo = _workspace(tmp_path)
    r = _run_update(cwd=repo)  # repo dir, NOT the meta dir
    assert r.returncode == 0, r.stdout + r.stderr
    # never hits the interactive preflight
    assert "Interactive setup unavailable" not in r.stdout + r.stderr
    # the generator ran and wrote the repo's orchestration rules
    local = repo / "CLAUDE.local.md"
    assert local.is_file(), r.stdout
    assert "backend-agent" in local.read_text(encoding="utf-8")


def test_update_headless_preserves_marker_agent(tmp_path: Path):
    meta, repo = _workspace(tmp_path)
    r = _run_update(cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    marker = (repo / ".otaman").read_text(encoding="utf-8")
    assert "agent: backend-agent" in marker


def test_update_dry_run_writes_nothing(tmp_path: Path):
    meta, repo = _workspace(tmp_path)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "NO_COLOR": "1",
        "HOME": str(tmp_path),
    }
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT", "OTAMAN_AGENT"):
        env.pop(var, None)
    r = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "init", "--update", "--dry-run"],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (repo / "CLAUDE.local.md").exists()


def test_update_survives_generator_failure_on_file_shape_meta(tmp_path: Path):
    """A meta with a FILE-shape .otaman marker makes the plugin generator's
    create_directories collide (mkdir over a file — plugin-agent's to fix).
    --update must warn and still complete its own patches, not crash."""
    meta, repo = _workspace(tmp_path)
    (meta / ".otaman").write_text("agent: human\n", encoding="utf-8")  # file-shape
    r = _run_update(cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "did not complete" in r.stdout or "crashed" in r.stdout
    assert "Traceback" not in r.stderr
