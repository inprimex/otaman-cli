"""Tests for HITL message readers + CLI dispatch (tasks 3.2-3.4).

Covers:
- list_pending: collects request-human-review, sorts by priority + deadline + timestamp
- find_by_stem: locates a request by stem or by frontmatter id
- HumanDecisionPayload renders required fields (in-reply-to, session-id, decision, decided-by)
- emit_human_decision writes a file with proper frontmatter
- write_resolved_ack creates the ack sentinel under acks/
- `otaman hitl take <id>` end-to-end (subprocess + mocked input via stdin pipe)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.hitl.messages import (
    PRIORITY_RANK,
    HumanDecisionPayload,
    emit_human_decision,
    find_by_stem,
    list_pending,
    write_resolved_ack,
)


def _write_request(
    bus_active_dir: Path,
    *,
    stem: str,
    from_agent: str = "bridge-agent",
    to: str = "human",
    priority: str = "normal",
    decision_type: str = "approve-reject",
    session_id: str = "sess-abc",
    deadline: str | None = None,
    subject: str = "Test request",
    body_lines: list[str] | None = None,
) -> Path:
    """Write a synthetic request-human-review message file."""
    ts = "2026-06-03T10:00:00Z"
    fm = [
        "---",
        f"id: {stem}",
        f"from: {from_agent}",
        f"to: {to}",
        f"priority: {priority}",
        "type: request-human-review",
        f"timestamp: {ts}",
        "status: pending",
        f"session-id: {session_id}",
        f"decision-type: {decision_type}",
    ]
    if deadline:
        fm.append(f"deadline: {deadline}")
    fm.append("---")
    body = body_lines or [
        f"## Subject: {subject}",
        "",
        "### Context",
        "Test context.",
        "",
        "### Question",
        "Approve or reject?",
    ]
    bus_active_dir.mkdir(parents=True, exist_ok=True)
    path = bus_active_dir / f"{stem}.md"
    path.write_text("\n".join(fm + [""] + body) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def bus(tmp_path: Path) -> Path:
    active = tmp_path / "bus" / "active"
    active.mkdir(parents=True)
    (active / "acks").mkdir()
    return active


# ---------------------------------------------------------------------------
# list_pending


def test_list_pending_empty(bus: Path):
    assert list_pending(bus) == []


def test_list_pending_returns_one(bus: Path):
    _write_request(bus, stem="20260603T100000-bridge-to-human-request-human-review-x")
    results = list_pending(bus)
    assert len(results) == 1
    assert results[0].from_agent == "bridge-agent"
    assert results[0].decision_type == "approve-reject"


def test_list_pending_excludes_acked(bus: Path):
    stem = "20260603T100000-bridge-to-human-request-human-review-x"
    _write_request(bus, stem=stem)
    (bus / "acks" / f"{stem}.human.ack").write_text("resolved\n")
    assert list_pending(bus) == []


def test_list_pending_excludes_other_types(bus: Path):
    """Only request-human-review messages count."""
    (bus / "info.md").write_text(
        "---\nid: x\nfrom: y\nto: human\ntype: info\n---\n\n## Subject: not HITL\n",
        encoding="utf-8",
    )
    assert list_pending(bus) == []


def test_list_pending_filters_by_human_id(bus: Path):
    _write_request(bus, stem="a", to="human")
    _write_request(bus, stem="b", to="alice")
    # `human_id=alice` accepts alice + generic 'human'
    results = list_pending(bus, human_id="alice")
    assert sorted(r.msg_stem for r in results) == ["a", "b"]
    # `human_id=bob` accepts bob + generic 'human' (not alice)
    results = list_pending(bus, human_id="bob")
    assert [r.msg_stem for r in results] == ["a"]


def test_list_pending_sorts_by_priority(bus: Path):
    _write_request(bus, stem="m-normal", priority="normal")
    _write_request(bus, stem="m-urgent", priority="urgent")
    _write_request(bus, stem="m-low", priority="low")
    _write_request(bus, stem="m-high", priority="high")
    stems = [r.msg_stem for r in list_pending(bus)]
    assert stems == ["m-urgent", "m-high", "m-normal", "m-low"]


def test_list_pending_sorts_by_deadline_within_priority(bus: Path):
    _write_request(bus, stem="later", priority="normal", deadline="2026-06-10T00:00:00Z")
    _write_request(bus, stem="sooner", priority="normal", deadline="2026-06-04T00:00:00Z")
    stems = [r.msg_stem for r in list_pending(bus)]
    assert stems == ["sooner", "later"]


def test_priority_rank_constant_complete():
    """All four canonical priorities present in PRIORITY_RANK."""
    assert set(PRIORITY_RANK) == {"low", "normal", "high", "urgent"}


# ---------------------------------------------------------------------------
# find_by_stem


def test_find_by_stem_exact(bus: Path):
    _write_request(bus, stem="abc-123")
    found = find_by_stem(bus, "abc-123")
    assert found is not None and found.msg_stem == "abc-123"


def test_find_by_stem_prefix(bus: Path):
    _write_request(bus, stem="20260603T100000-bridge-to-human-rhr-abc")
    found = find_by_stem(bus, "20260603T100000")
    assert found is not None


def test_find_by_stem_missing(bus: Path):
    assert find_by_stem(bus, "no-such-stem") is None


# ---------------------------------------------------------------------------
# HumanDecisionPayload + emit_human_decision


def test_human_decision_payload_render_includes_required_fields():
    p = HumanDecisionPayload(
        in_reply_to="req-1",
        session_id="sess-abc",
        to_agent="bridge-agent",
        decision="approve",
        decided_by="roman",
        rationale="Looks fine.",
    )
    body = p.render()
    assert "type: human-decision" in body
    assert "in-reply-to: req-1" in body
    assert "session-id: sess-abc" in body
    assert "decision: approve" in body
    assert "decided-by: roman" in body
    assert "Looks fine." in body


def test_emit_human_decision_writes_file(bus: Path):
    p = HumanDecisionPayload(
        in_reply_to="req-1",
        session_id="sess-abc",
        to_agent="bridge-agent",
        decision="approve",
        decided_by="roman",
    )
    out = emit_human_decision(p, bus)
    assert out.is_file()
    assert "human-decision-req-1" in out.name
    text = out.read_text(encoding="utf-8")
    assert "from: human" in text
    assert "to: bridge-agent" in text


def test_write_resolved_ack(bus: Path):
    ack = write_resolved_ack(bus, "some-stem", by="human")
    assert ack.read_text(encoding="utf-8") == "resolved\n"
    assert ack.name == "some-stem.human.ack"


# ---------------------------------------------------------------------------
# `otaman hitl take <id>` — end-to-end via subprocess


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal otaman project layout — meta dir w/ platform.yaml + bus."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    (meta / ".agents" / "bus" / "active").mkdir(parents=True)
    (meta / ".agents" / "bus" / "active" / "acks").mkdir()
    (meta / "platform.yaml").write_text(
        "project: testprog\nrepos: []\n",
        encoding="utf-8",
    )
    return meta


def test_cli_hitl_take_non_tty_refuses_and_writes_nothing(project: Path):
    """`take` produces a PRIVILEGED `human-decision` message -- a piped,
    non-interactive stdin (exactly what a subprocess `input=` pipe gives
    you) must be refused outright, not fed through the decision prompts.
    Same forgery class as F012's pre-fix `otaman approve`; regression guard
    for the 2026-07-09 TTY-gate fix."""
    active = project / ".agents" / "bus" / "active"
    stem = "20260603T100000-bridge-to-human-request-human-review-approve-it"
    _write_request(
        active,
        stem=stem,
        decision_type="approve-reject",
        session_id="sess-xyz",
        from_agent="bridge-agent",
    )

    env = {**os.environ, "OTAMAN_AGENT": "human"}
    stdin_input = "approve\n\n\n\n"

    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "hitl", "take", stem],
        capture_output=True,
        text=True,
        cwd=str(project),
        env=env,
        input=stdin_input,
    )
    assert rc.returncode != 0
    assert "interactive terminal" in (rc.stdout + rc.stderr)

    assert [f for f in active.glob("*human-decision*") if f.is_file()] == []
    ack = active / "acks" / f"{stem}.human.ack"
    assert not ack.exists()


def test_cli_hitl_list_shows_pending(project: Path):
    active = project / ".agents" / "bus" / "active"
    _write_request(active, stem="req-1", priority="high", subject="High prio item")
    _write_request(active, stem="req-2", priority="low", subject="Low prio item")

    env = {**os.environ, "OTAMAN_AGENT": "human"}
    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "hitl", "list"],
        capture_output=True,
        text=True,
        cwd=str(project),
        env=env,
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout
    assert "req-1" in rc.stdout
    assert "req-2" in rc.stdout
    # req-1 (high) should appear before req-2 (low)
    assert rc.stdout.index("req-1") < rc.stdout.index("req-2")


def test_cli_hitl_take_missing_id_errors(project: Path):
    env = {**os.environ, "OTAMAN_AGENT": "human"}
    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "hitl", "take", "no-such-stem"],
        capture_output=True,
        text=True,
        cwd=str(project),
        env=env,
        input="\n\n\n\n",
    )
    assert rc.returncode != 0
    assert "No pending request" in (rc.stdout + rc.stderr)
