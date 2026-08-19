"""task-sequencing-contract 1.1/1.2 (JTBD-67/D6, jtbd-67-rollout step 4).

1.1: `otaman send` accepts + validates sequence-id/step/depends-on/stop-at
frontmatter; malformed fields refused at send; sections and frontmatter
refused apart (they travel together, per Roman's review).
1.2: `otaman check` shows advisory "waiting on step <n> (<owner>)" for
unsatisfied depends-on; `otaman read` shows stop-at prominently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.sequencing import (
    parse_step,
    validate_sequencing,
    waiting_annotation,
)

_SEQ_BODY = (
    "## Context\nParent: jtbd-67 rollout.\n\n"
    "## Sequence\n1. spec-agent — author (DONE) · 2. cli-agent — YOU ARE HERE\n\n"
    "## Your step\nImplement the thing. STOP-AT: PR open, do not merge.\n\n"
    "## Handoff\nUnblocks step 3.\n\n"
    "## Artifacts\nnone\n"
)


# ---------------------------------------------------------------------------
# validate_sequencing (unit)


def test_unsequenced_message_passes():
    assert validate_sequencing({}, "just a normal body") == []


def test_wellformed_sequenced_message_passes():
    fields = {
        "sequence-id": "jtbd-67-rollout",
        "step": "2/3",
        "depends-on": ["step 1 (DONE)"],
        "stop-at": "PR open, do not merge",
    }
    assert validate_sequencing(fields, _SEQ_BODY) == []


@pytest.mark.parametrize("bad_step", ["2", "0/3", "4/3", "a/b", "2/0"])
def test_malformed_step_refused(bad_step):
    fields = {"sequence-id": "x-1", "step": bad_step, "stop-at": "s"}
    errors = validate_sequencing(fields, _SEQ_BODY)
    assert any("step" in e and "malformed" in e for e in errors)


def test_depends_on_unknown_step_refused():
    fields = {
        "sequence-id": "x-1",
        "step": "2/3",
        "depends-on": ["step 7"],
        "stop-at": "s",
    }
    errors = validate_sequencing(fields, _SEQ_BODY)
    assert any("unknown step 7" in e for e in errors)


def test_depends_on_own_step_refused():
    fields = {"sequence-id": "x-1", "step": "2/3", "depends-on": ["step 2"], "stop-at": "s"}
    errors = validate_sequencing(fields, _SEQ_BODY)
    assert any("own step" in e for e in errors)


def test_sections_without_frontmatter_refused():
    errors = validate_sequencing({}, _SEQ_BODY)
    assert len(errors) == 1 and "travel together" in errors[0]


def test_frontmatter_without_sections_refused():
    fields = {"sequence-id": "x-1", "step": "1/2", "stop-at": "s"}
    errors = validate_sequencing(fields, "plain body, no coordination sections")
    assert any("lacks coordination section" in e for e in errors)


def test_missing_stop_at_refused():
    fields = {"sequence-id": "x-1", "step": "1/2"}
    errors = validate_sequencing(fields, _SEQ_BODY)
    assert any("stop-at is required" in e for e in errors)


def test_step_parse():
    assert parse_step("4/5") == (4, 5)
    assert parse_step(" 1/1 ") == (1, 1)
    assert parse_step("5/4") is None


# ---------------------------------------------------------------------------
# waiting_annotation (unit)


def test_waiting_when_dep_not_done():
    fm = {"depends-on": ["step 3"], "type": "task-assignment"}
    body = "## Sequence\n1. a — x · 2. b — y · 3. core-agent — pilot\n"
    assert waiting_annotation(fm, body) == "waiting on step 3 (core-agent)"


def test_not_waiting_when_dep_done():
    fm = {"depends-on": ["step 3 (DONE — Roman approved)"]}
    assert waiting_annotation(fm, "") is None


def test_owner_unknown_when_unparseable():
    fm = {"depends-on": ["step 9"]}
    assert waiting_annotation(fm, "no sequence section") == "waiting on step 9 (?)"


# ---------------------------------------------------------------------------
# End-to-end via the CLI


@pytest.fixture
def project(tmp_path: Path) -> Path:
    meta = tmp_path / "meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: t\nrepos: []\n", encoding="utf-8")
    return meta


def _run(meta: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": "spec-agent",
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "NO_COLOR": "1",
    }
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *argv],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )


def _send_sequenced(meta: Path, *extra: str, body: str = _SEQ_BODY):
    return _run(
        meta,
        "send",
        "cli-agent",
        "--type",
        "task-assignment",
        "--subject",
        "step 2 work",
        "--body",
        body,
        *extra,
    )


_SEQ_FLAGS = (
    "--sequence-id", "jtbd-67-rollout",
    "--step", "2/3",
    "--depends-on", "step 1 (DONE)",
    "--stop-at", "PR open, do not merge",
)  # fmt: skip


def test_send_emits_sequencing_frontmatter(project: Path):
    r = _send_sequenced(project, *_SEQ_FLAGS)
    assert r.returncode == 0, r.stdout + r.stderr
    msg = next((project / ".agents" / "bus" / "active").glob("*step-2-work*.md"))
    content = msg.read_text(encoding="utf-8")
    assert "sequence-id: jtbd-67-rollout" in content
    assert "step: 2/3" in content
    assert "depends-on: [step 1 (DONE)]" in content
    assert "stop-at: PR open, do not merge" in content


def test_send_refuses_malformed_step(project: Path):
    r = _send_sequenced(
        project,
        "--sequence-id", "jtbd-67-rollout",
        "--step", "9",
        "--stop-at", "s",
    )  # fmt: skip
    assert r.returncode == 2
    assert "malformed step" in r.stdout + r.stderr
    assert list((project / ".agents" / "bus" / "active").glob("*.md")) == []


def test_send_refuses_sections_without_frontmatter(project: Path):
    r = _send_sequenced(project)  # body has sections, no flags
    assert r.returncode == 2
    assert "travel together" in r.stdout + r.stderr


def test_send_refuses_frontmatter_without_sections(project: Path):
    r = _send_sequenced(project, *_SEQ_FLAGS, body="plain body")
    assert r.returncode == 2
    assert "travel together" in r.stdout + r.stderr


def test_send_refuses_sequencing_on_non_task_assignment(project: Path):
    r = _run(
        project,
        "send",
        "cli-agent",
        "--type",
        "info",
        "--subject",
        "s",
        "--body",
        "b",
        "--sequence-id",
        "x-1",
        "--step",
        "1/1",
        "--stop-at",
        "s",
    )
    assert r.returncode == 2
    assert "task-assignment contract" in r.stdout + r.stderr


def test_check_shows_waiting_annotation(project: Path):
    stem = "20260819T120000-spec-agent-to-cli-agent-step-2-work"
    (project / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\n"
        f"id: {stem}\nfrom: spec-agent\nto: cli-agent\n"
        "sequence-id: jtbd-67-rollout\nstep: 2/3\n"
        "depends-on: [step 1]\nstop-at: PR open only\n"
        "priority: normal\ntype: task-assignment\n"
        "timestamp: 2026-08-19T12:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: step 2 work\n\n" + _SEQ_BODY.replace("1. spec-agent", "1. core-agent"),
        encoding="utf-8",
    )
    out = _run(project, "check", "cli-agent").stdout
    assert "waiting on step 1 (core-agent)" in out


def test_read_shows_stop_at_banner(project: Path):
    stem = "20260819T120000-spec-agent-to-cli-agent-step-2-work"
    (project / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\n"
        f"id: {stem}\nfrom: spec-agent\nto: cli-agent\n"
        "sequence-id: jtbd-67-rollout\nstep: 2/3\n"
        "depends-on: [step 1 (DONE)]\nstop-at: PR open, do NOT merge\n"
        "priority: normal\ntype: task-assignment\n"
        "timestamp: 2026-08-19T12:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: step 2 work\n\n" + _SEQ_BODY,
        encoding="utf-8",
    )
    out = _run(project, "read", stem).stdout
    assert "STOP-AT" in out
    assert "PR open, do NOT merge" in out
    # banner appears before the raw content dump
    assert out.index("STOP-AT") < out.index("## Subject: step 2 work")
