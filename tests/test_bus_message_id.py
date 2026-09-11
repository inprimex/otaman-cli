"""B2 — bus message id uniqueness (tenant defect report 20260910T210601).

The old `id: {ts}-{agent[:8]}` was second-resolution AND truncated the sender to
8 chars, so distinct messages collided (agent-pmeets-{infra,worker,…} all →
'agent-pm') and `otaman ack` — which keys on the id — matched the wrong file.
The id must equal the unique, route-carrying filename stem.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def _setup_root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(root: Path, agent: str, *argv: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": agent,
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


def _send(root: Path, sender: str, to: str, subject: str) -> subprocess.CompletedProcess:
    return _run(root, sender, "send", to, "--subject", subject, "--body", "b")


def _id_of(path: Path) -> str:
    m = re.search(r"^id:\s*(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def test_id_equals_filename_stem(tmp_path: Path):
    root = _setup_root(tmp_path)
    r = _send(root, "cli-agent", "plugin-agent", "hello world")
    assert r.returncode == 0, r.stderr
    (msg,) = list((root / ".agents" / "bus" / "active").glob("*.md"))
    assert _id_of(msg) == msg.stem  # id IS the unique stem


def test_id_is_route_carrying_not_truncated(tmp_path: Path):
    # the id must name the route (sender-to-recipient-subject), not the old
    # truncated `{ts}-{sender[:8]}` form.
    root = _setup_root(tmp_path)
    _send(root, "cli-agent", "plugin-agent", "deploy the thing")
    (msg,) = list((root / ".agents" / "bus" / "active").glob("*.md"))
    mid = _id_of(msg)
    assert "-to-plugin-agent-" in mid  # recipient present
    assert "deploy-the-thing" in mid  # subject slug present
    assert not re.fullmatch(r"\d{8}T\d{6}-[a-z-]{1,8}", mid)  # not the truncated form


def test_distinct_recipients_get_distinct_ids(tmp_path: Path):
    # the collision class: same second, same truncated sender → same id.
    root = _setup_root(tmp_path)
    _send(root, "agent-pmeets-infra", "cli-agent", "s")
    _send(root, "agent-pmeets-infra", "spec-agent", "s")
    ids = {_id_of(m) for m in (root / ".agents" / "bus" / "active").glob("*.md")}
    assert len(ids) == 2  # no collision


def test_ack_matches_by_displayed_id(tmp_path: Path):
    # the displayed id must resolve exactly one file (collision-proof ack).
    root = _setup_root(tmp_path)
    _send(root, "spec-agent", "cli-agent", "please do X")
    (msg,) = list((root / ".agents" / "bus" / "active").glob("*.md"))
    mid = _id_of(msg)
    r = _run(root, "cli-agent", "ack", mid)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ambiguous" not in (r.stdout + r.stderr).lower()


def test_cc_copy_id_equals_its_own_stem(tmp_path: Path):
    # F1: under CC fan-out, EVERY file's id equals its OWN stem (the CC copy no
    # longer carries the primary's route stem).
    root = _setup_root(tmp_path)
    r = _run(root, "cli-agent", "send", "spec-agent",
             "--subject", "hi", "--body", "b", "--cc", "core-agent")  # fmt: skip
    assert r.returncode == 0, r.stderr + r.stdout
    msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
    assert len(msgs) == 2  # primary + one CC copy
    for m in msgs:
        assert _id_of(m) == m.stem, f"{m.name}: id {_id_of(m)!r} != stem"
    assert len({_id_of(m) for m in msgs}) == 2  # distinct ids
    # the CC copy names the CC recipient in its own id
    cc = next(m for m in msgs if "x-cc: true" in m.read_text(encoding="utf-8"))
    assert "core-agent" in _id_of(cc)
