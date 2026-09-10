"""The bus-writer self-validation invariant (approved 20260910T210846):

    THE CLI SHALL NEVER EMIT A MESSAGE THAT FAILS ITS OWN VALIDATOR.

Every message-writing path is driven into a temp bus, then every emitted file is
run through otaman-core's `validate_message`. Zero errors is the contract. This
one assertion guards the whole class — it catches B1 (unparseable frontmatter),
B2 (id/route shape), B4 (broadcast-type), and any future regression.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from otaman_core.validate_message import validate_message


def _stage(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "myorg"
    specs = tmp_path / "myorg-specs"
    project.mkdir()
    (specs / "openspec" / "changes").mkdir(parents=True)
    (project / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (project / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (project / "platform.yaml").write_text(
        textwrap.dedent("""
            project: myorg
            version: '1.0'
            specs:
              path: ../myorg-specs
            repos:
              - {name: otaman-cli, path: ../otaman-cli, owner: cli-agent}
              - {name: otaman-core, path: ../otaman-core, owner: core-agent}
        """).lstrip(),
        encoding="utf-8",
    )
    return project, specs


def _run(project: Path, *argv: str) -> subprocess.CompletedProcess:
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
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _all_bus_errors(project: Path) -> dict[str, list[str]]:
    """Validate every file in the active bus; return {filename: errors} for any
    file with errors."""
    bus = project / ".agents" / "bus" / "active"
    bad: dict[str, list[str]] = {}
    for f in bus.glob("*.md"):
        errors, _warnings = validate_message(f)
        if errors:
            bad[f.name] = errors
    return bad


_SEQ_BODY = (
    "## Context\nParent: rollout.\n\n"
    "## Sequence\n1. cli-agent — author (DONE) · 2. spec-agent — YOU ARE HERE\n\n"
    "## Your step\nDo it. STOP-AT: PR is open: do NOT merge.\n\n"
    "## Handoff\nUnblocks step 3.\n\n"
    "## Artifacts\nnone\n"
)


def test_plain_send_emits_valid_message(tmp_path: Path):
    project, _ = _stage(tmp_path)
    r = _run(project, "send", "spec-agent", "--subject", "hello: world", "--body", "b")
    assert r.returncode == 0, r.stderr
    assert _all_bus_errors(project) == {}


def test_sequenced_send_with_hostile_stop_at_is_valid(tmp_path: Path):
    # B1: a stop-at containing ': ' must still produce a validator-clean message.
    project, _ = _stage(tmp_path)
    r = _run(
        project,
        "send",
        "spec-agent",
        "--type",
        "task-assignment",
        "--subject",
        "step 2 work",
        "--body",
        _SEQ_BODY,
        "--sequence-id",
        "rollout-1",
        "--step",
        "2/3",
        "--depends-on",
        "step 1 (DONE)",
        "--stop-at",
        "PR is open: do NOT merge",
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert _all_bus_errors(project) == {}


def test_send_with_cc_fanout_all_copies_valid(tmp_path: Path):
    project, _ = _stage(tmp_path)
    r = _run(project, "send", "spec-agent", "--subject", "s", "--body", "b", "--cc", "core-agent")
    assert r.returncode == 0, r.stderr
    assert _all_bus_errors(project) == {}


def test_notify_change_fanout_all_copies_valid(tmp_path: Path):
    project, specs = _stage(tmp_path)
    change = specs / "openspec" / "changes" / "ch1"
    change.mkdir(parents=True)
    (change / "tasks.md").write_text("- [ ] 1.1 @otaman-cli\n- [ ] 1.2 @otaman-core\n", "utf-8")
    r = _run(project, "notify-change", "ch1")
    assert r.returncode == 0, r.stderr
    assert _all_bus_errors(project) == {}


@pytest.mark.parametrize("subject", ["ok", "colon: here", 'quotes " and [brackets]', "# hash"])
def test_send_hostile_subjects_stay_valid(tmp_path: Path, subject: str):
    project, _ = _stage(tmp_path)
    r = _run(project, "send", "spec-agent", "--subject", subject, "--body", "b")
    assert r.returncode == 0, r.stderr
    assert _all_bus_errors(project) == {}, subject
