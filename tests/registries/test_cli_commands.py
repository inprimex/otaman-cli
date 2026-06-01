"""Integration tests for `otaman outcome|solution|persona` CLI commands.

End-to-end through subprocess: setup tmp project layout, run the CLI, assert
on-disk state + emitted bus messages.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.registries.loader import yaml_load


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up: parent/meta + parent/biz; meta has .agents/bus/active + platform.yaml."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    (meta / ".agents" / "bus" / "active").mkdir(parents=True)
    (meta / ".agents" / "bus" / "active" / "acks").mkdir()
    (meta / ".agents" / "current-agent").write_text("cli-agent\n")
    (meta / "platform.yaml").write_text(
        "project: testprog\n"
        "repos:\n"
        "  - name: biz\n    path: ../biz\n    owner: cpo-agent\n"
        "role-assignments:\n"
        "  cpo: human\n  ceo: human\n  cto: human\n",
        encoding="utf-8",
    )
    biz = parent / "biz"
    biz.mkdir()
    return meta


def _run(meta: Path, *cli_args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OTAMAN_AGENT": "human"}
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *cli_args],
        capture_output=True, text=True, cwd=str(meta), env=env,
    )


def _outcomes_path(meta: Path) -> Path:
    return meta.parent / "biz" / "outcomes.yaml"


def _solutions_path(meta: Path) -> Path:
    return meta.parent / "biz" / "solutions.yaml"


def _personas_path(meta: Path) -> Path:
    return meta.parent / "biz" / "personas.yaml"


def _active_bus_messages(meta: Path, msg_type: str | None = None) -> list[Path]:
    active = meta / ".agents" / "bus" / "active"
    out = []
    for f in active.glob("*.md"):
        if f.is_dir():
            continue
        if msg_type is None:
            out.append(f)
        elif msg_type in f.name:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# outcome add


def test_outcome_add_writes_yaml(project: Path) -> None:
    rc = _run(
        project, "outcome", "add", "JTBD-1-create-account",
        "--as-a", "new user",
        "--i-want-to", "create an account",
        "--incremental-outcome", "have verified account",
        "--so-i-can", "use TaskFlow",
        "--priority", "P0",
        "--impact", "L",
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout

    data = yaml_load(_outcomes_path(project))
    assert len(data["outcomes"]) == 1
    o = data["outcomes"][0]
    assert o["id"] == "JTBD-1-create-account"
    assert o["status"] == "Drafting"
    assert o["priority"] == "P0"
    assert o["impact"] == "L"
    assert o["statement"]["as-a"] == "new user"
    assert len(o["transitions"]) == 1
    assert o["transitions"][0]["action"] == "create"


def test_outcome_add_missing_required_field_errors(project: Path) -> None:
    rc = _run(project, "outcome", "add", "JTBD-1-x", "--as-a", "u")
    assert rc.returncode != 0
    assert "Missing required" in (rc.stdout + rc.stderr)


def test_outcome_add_invalid_id_format_fails_validation(project: Path) -> None:
    rc = _run(
        project, "outcome", "add", "not-a-jtbd-id",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    assert rc.returncode == 2, rc.stdout + rc.stderr
    # Pydantic message includes the regex pattern
    assert "JTBD-" in (rc.stdout + rc.stderr) or "outcome id" in (rc.stdout + rc.stderr)


# ---------------------------------------------------------------------------
# outcome list / show / history


def test_outcome_list_and_show(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "list")
    assert rc.returncode == 0
    assert "JTBD-1-x" in rc.stdout

    rc = _run(project, "outcome", "show", "JTBD-1-x")
    assert rc.returncode == 0
    assert "JTBD-1-x" in rc.stdout
    assert "Drafting" in rc.stdout


def test_outcome_history(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "history", "JTBD-1-x")
    assert rc.returncode == 0
    assert "create" in rc.stdout


# ---------------------------------------------------------------------------
# outcome promote / demote — also emits outcome-status-changed bus msg


def test_outcome_promote_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "promote", "JTBD-1-x")
    assert rc.returncode == 0, rc.stderr or rc.stdout

    data = yaml_load(_outcomes_path(project))
    assert data["outcomes"][0]["status"] == "Backlog"
    msgs = _active_bus_messages(project, "outcome-status-changed")
    assert len(msgs) == 1
    txt = msgs[0].read_text(encoding="utf-8")
    assert "Drafting" in txt and "Backlog" in txt


def test_outcome_cannot_promote_terminal_state(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    # Drafting → Backlog → Approved → In-Progress → Done
    for _ in range(4):
        _run(project, "outcome", "promote", "JTBD-1-x")
    # Now at Done; promote should fail
    rc = _run(project, "outcome", "promote", "JTBD-1-x")
    assert rc.returncode != 0
    assert "terminal" in (rc.stdout + rc.stderr).lower()


# ---------------------------------------------------------------------------
# outcome request-estimate — emits outcome-estimate-requested


def test_outcome_request_estimate_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "request-estimate", "JTBD-1-x")
    assert rc.returncode == 0
    data = yaml_load(_outcomes_path(project))
    assert data["outcomes"][0]["estimate-requested"] is True
    msgs = _active_bus_messages(project, "outcome-estimate-requested")
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# solution add


def test_solution_add_writes_yaml(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(
        project, "solution", "add", "SOL-1-foo",
        "--outcome", "JTBD-1-x",
        "--description", "Solve it with X",
        "--t-shirt", "Small",
        "--pro", "Fast",
        "--con", "Limited",
        "--depends-on", "external:Email provider",
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout

    data = yaml_load(_solutions_path(project))
    assert len(data["solutions"]) == 1
    s = data["solutions"][0]
    assert s["id"] == "SOL-1-foo"
    assert s["outcome-id"] == "JTBD-1-x"
    assert s["t-shirt"] == "Small"
    assert s["effort-days"] == 3  # Small from default scale
    assert s["pros"] == ["Fast"]
    assert s["cons"] == ["Limited"]
    assert s["dependencies"][0]["kind"] == "external"
    assert s["dependencies"][0]["name"] == "Email provider"


def test_solution_add_rejects_self_reference(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(
        project, "solution", "add", "SOL-1-foo",
        "--outcome", "JTBD-1-x",
        "--description", "x",
        "--depends-on", "solution:SOL-1-foo",
    )
    assert rc.returncode == 2
    assert "self-reference" in (rc.stdout + rc.stderr)


# ---------------------------------------------------------------------------
# solution propose — emits outcome-estimates-ready


def test_solution_propose_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    _run(
        project, "solution", "add", "SOL-1-foo",
        "--outcome", "JTBD-1-x",
        "--description", "x",
        "--t-shirt", "Small",
    )
    rc = _run(project, "solution", "propose", "SOL-1-foo")
    assert rc.returncode == 0, rc.stderr or rc.stdout
    msgs = _active_bus_messages(project, "outcome-estimates-ready")
    assert len(msgs) == 1
    txt = msgs[0].read_text(encoding="utf-8")
    assert "recommended" in txt and "SOL-1-foo" in txt


# ---------------------------------------------------------------------------
# Smoke test: full happy path — add outcome → add solution → accept-cost


def test_full_acceptcost_flow_emits_3_bus_messages(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
        "--priority", "P0", "--impact", "L",
    )
    _run(project, "outcome", "promote", "JTBD-1-x")  # → Backlog
    _run(project, "outcome", "request-estimate", "JTBD-1-x")
    _run(
        project, "solution", "add", "SOL-1-foo",
        "--outcome", "JTBD-1-x",
        "--description", "x",
        "--t-shirt", "Small",
    )
    rc = _run(
        project, "outcome", "accept-cost", "JTBD-1-x",
        "--solution", "SOL-1-foo",
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout

    # Outcome should now be Approved + chosen-solution set
    data = yaml_load(_outcomes_path(project))
    o = data["outcomes"][0]
    assert o["status"] == "Approved"
    assert o["chosen-solution"] == "SOL-1-foo"
    assert o["cost-accepted"] is True

    # 3 bus messages from the lifecycle:
    # - outcome-status-changed (promote)
    # - outcome-estimate-requested
    # - outcome-cost-accepted
    types = [m.name for m in _active_bus_messages(project)]
    assert any("outcome-status-changed" in t for t in types)
    assert any("outcome-estimate-requested" in t for t in types)
    assert any("outcome-cost-accepted" in t for t in types)


# ---------------------------------------------------------------------------
# outcome reject-cost


def test_outcome_reject_cost_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "reject-cost", "JTBD-1-x", "--reason", "too expensive")
    assert rc.returncode == 0
    data = yaml_load(_outcomes_path(project))
    assert data["outcomes"][0]["cost-accepted"] is False
    msgs = _active_bus_messages(project, "outcome-cost-rejected")
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# outcome retire


def test_outcome_retire_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    rc = _run(project, "outcome", "retire", "JTBD-1-x", "--reason", "deprioritised")
    assert rc.returncode == 0
    data = yaml_load(_outcomes_path(project))
    assert data["outcomes"][0]["status"] == "Retired"
    msgs = _active_bus_messages(project, "outcome-status-changed")
    assert len(msgs) >= 1


# ---------------------------------------------------------------------------
# solution discard


def test_solution_discard_emits_bus_message(project: Path) -> None:
    _run(
        project, "outcome", "add", "JTBD-1-x",
        "--as-a", "u", "--i-want-to", "t",
        "--incremental-outcome", "x", "--so-i-can", "y",
    )
    _run(
        project, "solution", "add", "SOL-1-foo",
        "--outcome", "JTBD-1-x", "--description", "x",
    )
    rc = _run(project, "solution", "discard", "SOL-1-foo", "--reason", "rejected approach")
    assert rc.returncode == 0
    data = yaml_load(_solutions_path(project))
    assert data["solutions"][0]["status"] == "Discarded"
    msgs = _active_bus_messages(project, "solution-status-changed")
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# persona add / list / show / retire


def test_persona_add_and_show(project: Path) -> None:
    rc = _run(
        project, "persona", "add", "persona-end-user",
        "--name", "End user",
        "--description", "A user using TaskFlow",
        "--kind", "end-user",
    )
    assert rc.returncode == 0
    data = yaml_load(_personas_path(project))
    assert data["personas"][0]["id"] == "persona-end-user"
    assert data["personas"][0]["kind"] == "end-user"

    rc = _run(project, "persona", "show", "persona-end-user")
    assert rc.returncode == 0
    assert "End user" in rc.stdout


def test_persona_retire_sets_status(project: Path) -> None:
    _run(
        project, "persona", "add", "persona-end-user",
        "--name", "X", "--description", "Y", "--kind", "end-user",
    )
    rc = _run(project, "persona", "retire", "persona-end-user", "--reason", "consolidated")
    assert rc.returncode == 0
    data = yaml_load(_personas_path(project))
    assert data["personas"][0]["status"] == "retired"


def test_persona_invalid_kind_rejected(project: Path) -> None:
    rc = _run(
        project, "persona", "add", "persona-robot",
        "--name", "Bot", "--description", "X", "--kind", "robot",
    )
    assert rc.returncode != 0
    assert "Invalid kind" in (rc.stdout + rc.stderr)


# ---------------------------------------------------------------------------
# Role advisory warning (Mode 1: proceeds with warning)


def test_unauthorized_actor_emits_warning_but_proceeds(project: Path, monkeypatch) -> None:
    # role-assignments has cpo:human, but actor is "stranger" (no cpo role)
    env = {**os.environ, "OTAMAN_AGENT": "stranger"}
    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main",
         "outcome", "add", "JTBD-1-x",
         "--as-a", "u", "--i-want-to", "t",
         "--incremental-outcome", "x", "--so-i-can", "y"],
        capture_output=True, text=True, cwd=str(project), env=env,
    )
    # v1 advisory-only: command succeeds despite role mismatch
    assert rc.returncode == 0
    # but emits a WARN to stderr
    assert "WARN" in rc.stderr
    assert "outcome.add" in rc.stderr
    # and the file was still written
    assert _outcomes_path(project).is_file()
