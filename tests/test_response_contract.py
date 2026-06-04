"""Tests for inter-agent-request-response-contract (tasks 2.1-2.4).

Covers:
- Sort tiebreaker within priority band (expects-response, effort, timestamp)
- Type-default response-effort table (Q4)
- Sender override of response-effort
- response-deadline imminence within 2h (incl. past deadlines)
- otaman ack --resolved advisory when expects-response + no reply
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from otaman_cli.response_contract import (
    DEADLINE_WINDOW,
    EFFORT_ORDER,
    PRIORITY_ORDER,
    TYPE_DEFAULT_EFFORT,
    deadline_is_imminent,
    has_outbound_reply,
    make_sort_key,
    resolve_response_effort,
)


# ---------------------------------------------------------------------------
# resolve_response_effort (Q4 type-defaults)


def test_question_defaults_to_S():
    assert resolve_response_effort("question", None) == "S"


def test_task_assignment_defaults_to_M():
    assert resolve_response_effort("task-assignment", None) == "M"


def test_spec_change_request_defaults_to_L():
    assert resolve_response_effort("spec-change-request", None) == "L"


def test_info_defaults_to_XS():
    assert resolve_response_effort("info", None) == "XS"


def test_fyi_defaults_to_XS():
    assert resolve_response_effort("fyi", None) == "XS"


def test_sender_override_wins():
    assert resolve_response_effort("question", "XL") == "XL"


def test_unknown_type_falls_back_to_M():
    assert resolve_response_effort("totally-novel-type", None) == "M"


def test_explicit_lowercased_normalised_to_upper():
    assert resolve_response_effort("question", "xl") == "XL"


def test_default_table_matches_design_md_Q4():
    """The table must match design.md §Q4 verbatim — guard against drift."""
    expected = {
        "question": "S",
        "task-assignment": "M",
        "spec-change-request": "L",
        "contract-change": "M",
        "info": "XS",
        "fyi": "XS",
        "review-request": "M",
        "spec-change-approved": "XS",
        "task-complete": "XS",
        "proposal": "L",
    }
    assert TYPE_DEFAULT_EFFORT == expected


# ---------------------------------------------------------------------------
# make_sort_key — tiebreaker order


def test_priority_dominates():
    """Urgent always before high regardless of other fields."""
    urgent = {"priority": "urgent", "type": "info", "timestamp": "9"}
    high = {"priority": "high", "type": "task-assignment",
            "expects_response": True, "timestamp": "1"}
    assert make_sort_key(urgent) < make_sort_key(high)


def test_expects_response_true_before_false_same_priority():
    """Within priority band: expects-response: True first."""
    expects = {"priority": "normal", "type": "info", "expects_response": True}
    no_expect = {"priority": "normal", "type": "info", "expects_response": False}
    assert make_sort_key(expects) < make_sort_key(no_expect)


def test_effort_ascending_within_band():
    """Same priority + expects-response: XS sorts before XL."""
    xs = {"priority": "normal", "type": "info", "expects_response": False}        # XS
    l_ = {"priority": "normal", "type": "spec-change-request",
          "expects_response": False}  # L
    assert make_sort_key(xs) < make_sort_key(l_)


def test_timestamp_ascending_as_final_tiebreaker():
    """Identical priority + expects + effort → older timestamp wins."""
    earlier = {"priority": "normal", "type": "question",
               "expects_response": True, "timestamp": "2026-06-01T00:00:00Z"}
    later = {"priority": "normal", "type": "question",
             "expects_response": True, "timestamp": "2026-06-04T00:00:00Z"}
    assert make_sort_key(earlier) < make_sort_key(later)


def test_full_sort_mixed_inbox():
    """Realistic mixed inbox sorts correctly end-to-end."""
    msgs = [
        # priority, type, expects_response, response_effort, timestamp
        {"id": "low-info",       "priority": "low",    "type": "info",                  "expects_response": False, "timestamp": "t1"},
        {"id": "high-question",  "priority": "high",   "type": "question",              "expects_response": True,  "timestamp": "t2"},
        {"id": "normal-task",    "priority": "normal", "type": "task-assignment",       "expects_response": True,  "timestamp": "t3"},
        {"id": "urgent-info",    "priority": "urgent", "type": "info",                  "expects_response": False, "timestamp": "t4"},
        {"id": "normal-info",    "priority": "normal", "type": "info",                  "expects_response": False, "timestamp": "t5"},
        {"id": "normal-q-late",  "priority": "normal", "type": "question",              "expects_response": True,  "timestamp": "t6"},
        {"id": "normal-q-early", "priority": "normal", "type": "question",              "expects_response": True,  "timestamp": "t0"},
    ]
    msgs.sort(key=make_sort_key)
    ids = [m["id"] for m in msgs]
    # Expected order:
    # 1. urgent (urgent-info)
    # 2. high (high-question)
    # 3. normal — expects-response=True: q-early before q-late (same effort S),
    #    then task-assignment (M)
    # 4. normal — expects-response=False: info (XS)
    # 5. low (low-info)
    assert ids == [
        "urgent-info",
        "high-question",
        "normal-q-early",
        "normal-q-late",
        "normal-task",
        "normal-info",
        "low-info",
    ]


# ---------------------------------------------------------------------------
# deadline_is_imminent


def test_deadline_within_2h_returns_true():
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    deadline = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert deadline_is_imminent(deadline, now=now) is True


def test_deadline_3h_away_returns_false():
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    deadline = (now + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    assert deadline_is_imminent(deadline, now=now) is False


def test_past_deadline_returns_true():
    """Past deadlines are even MORE imminent — must alert."""
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    deadline = (now - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    assert deadline_is_imminent(deadline, now=now) is True


def test_no_deadline_returns_false():
    assert deadline_is_imminent(None) is False
    assert deadline_is_imminent("") is False


def test_malformed_deadline_returns_false():
    assert deadline_is_imminent("not-a-date") is False
    assert deadline_is_imminent("2026-13-99T99:99:99Z") is False


def test_naive_datetime_is_assumed_utc():
    """RFC3339 without timezone — treat as UTC (consistent with bus convention)."""
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    deadline_no_tz = "2026-06-04T13:00:00"   # 1h ahead, but no Z
    assert deadline_is_imminent(deadline_no_tz, now=now) is True


# ---------------------------------------------------------------------------
# has_outbound_reply


def _write_msg(active: Path, stem: str, fm: dict[str, str]) -> Path:
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path = active / f"{stem}.md"
    path.write_text(f"---\n{fm_lines}\n---\n\n## Subject: test\n", encoding="utf-8")
    return path


def test_has_outbound_reply_finds_match(tmp_path: Path):
    active = tmp_path / "active"
    active.mkdir()
    _write_msg(active, "reply", {
        "id": "reply-1", "from": "cli-agent", "to": "spec-agent",
        "type": "info", "reply-to": "request-1",
    })
    assert has_outbound_reply(active, in_reply_to_id="request-1", from_agent="cli-agent") is True


def test_has_outbound_reply_wrong_from_returns_false(tmp_path: Path):
    active = tmp_path / "active"
    active.mkdir()
    _write_msg(active, "reply", {
        "id": "r", "from": "spec-agent", "to": "cli-agent",
        "type": "info", "reply-to": "request-1",
    })
    assert has_outbound_reply(active, in_reply_to_id="request-1", from_agent="cli-agent") is False


def test_has_outbound_reply_wrong_reply_to_returns_false(tmp_path: Path):
    active = tmp_path / "active"
    active.mkdir()
    _write_msg(active, "reply", {
        "id": "r", "from": "cli-agent", "to": "spec-agent",
        "type": "info", "reply-to": "request-OTHER",
    })
    assert has_outbound_reply(active, in_reply_to_id="request-1", from_agent="cli-agent") is False


def test_has_outbound_reply_empty_dir_returns_false(tmp_path: Path):
    assert has_outbound_reply(tmp_path / "nope", in_reply_to_id="x", from_agent="x") is False


# ---------------------------------------------------------------------------
# End-to-end: otaman ack --resolved advisory


@pytest.fixture
def project(tmp_path: Path) -> Path:
    parent = tmp_path / "p"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: testproj\nrepos:\n  - name: svc\n    path: ../svc\n    owner: cli-agent\n",
        encoding="utf-8",
    )
    return meta


def _plant_request(meta: Path, stem: str, *, expects_response: bool = True) -> None:
    active = meta / ".agents" / "bus" / "active"
    fm = (
        f"---\nid: {stem}\nfrom: spec-agent\nto: cli-agent\n"
        f"priority: normal\ntype: question\ntimestamp: 2026-06-04T10:00:00Z\n"
    )
    if expects_response:
        fm += "expects-response: true\n"
    fm += "---\n\n## Subject: please answer\n"
    (active / f"{stem}.md").write_text(fm, encoding="utf-8")


def _run_ack(meta: Path, stem: str, *flags: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "ack", stem, *flags],
        capture_output=True, text=True, cwd=str(meta), env=env,
    )


def test_ack_resolved_fires_advisory_when_no_reply(project: Path):
    _plant_request(project, "20260604T100000-spec-agent-to-cli-question-x")
    rc = _run_ack(project, "20260604T100000-spec-agent-to-cli-question-x", "--resolved")
    assert rc.returncode == 0   # advisory, not blocking
    output = rc.stdout + rc.stderr
    assert "expects a response" in output
    assert "Ack as 'read'" in output


def test_ack_resolved_silent_when_reply_exists(project: Path):
    stem = "20260604T100000-spec-agent-to-cli-question-y"
    _plant_request(project, stem)
    # Plant an outbound reply with reply-to: <id>
    active = project / ".agents" / "bus" / "active"
    (active / "20260604T110000-cli-to-spec-reply.md").write_text(
        f"---\nid: reply-x\nfrom: cli-agent\nto: spec-agent\n"
        f"priority: normal\ntype: info\ntimestamp: 2026-06-04T11:00:00Z\n"
        f"reply-to: {stem}\n---\n\n## Subject: here you go\n",
        encoding="utf-8",
    )
    rc = _run_ack(project, stem, "--resolved")
    assert rc.returncode == 0
    output = rc.stdout + rc.stderr
    assert "expects a response" not in output


def test_ack_resolved_silent_when_message_does_not_expect_response(project: Path):
    _plant_request(project, "20260604T100000-spec-agent-to-cli-info-z", expects_response=False)
    rc = _run_ack(project, "20260604T100000-spec-agent-to-cli-info-z", "--resolved")
    assert rc.returncode == 0
    output = rc.stdout + rc.stderr
    assert "expects a response" not in output


def test_ack_read_does_not_fire_advisory(project: Path):
    """The advisory is for `resolved` only; `--read` is fine without a reply."""
    _plant_request(project, "20260604T100000-spec-agent-to-cli-question-q")
    rc = _run_ack(project, "20260604T100000-spec-agent-to-cli-question-q", "--read")
    assert rc.returncode == 0
    assert "expects a response" not in (rc.stdout + rc.stderr)


# ---------------------------------------------------------------------------
# End-to-end: cmd_check sort visible in output


def test_check_orders_by_response_contract(project: Path):
    # Plant 3 messages, all same priority `normal`, mixed types/expects.
    active = project / ".agents" / "bus" / "active"
    # info (XS, no expects-response) — should sort LAST
    (active / "msg-info.md").write_text(
        "---\nid: msg-info\nfrom: x\nto: cli-agent\npriority: normal\n"
        "type: info\ntimestamp: 2026-06-04T08:00:00Z\n---\n\n## Subject: FYI x\n",
        encoding="utf-8",
    )
    # task-assignment (M, implicit expects-response) — middle
    (active / "msg-task.md").write_text(
        "---\nid: msg-task\nfrom: x\nto: cli-agent\npriority: normal\n"
        "type: task-assignment\nexpects-response: true\n"
        "timestamp: 2026-06-04T09:00:00Z\n---\n\n## Subject: do thing\n",
        encoding="utf-8",
    )
    # question (S, explicit expects-response) — should sort FIRST
    (active / "msg-question.md").write_text(
        "---\nid: msg-question\nfrom: x\nto: cli-agent\npriority: normal\n"
        "type: question\nexpects-response: true\n"
        "timestamp: 2026-06-04T10:00:00Z\n---\n\n## Subject: ask\n",
        encoding="utf-8",
    )

    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "check", "cli-agent"],
        capture_output=True, text=True, cwd=str(project), env=env,
    )
    assert rc.returncode == 0, rc.stderr
    out = rc.stdout
    # Expected order in pending: msg-question (S, expects) → msg-task (M, expects) → msg-info (XS, no-expects)
    q_pos = out.index("msg-question")
    t_pos = out.index("msg-task")
    i_pos = out.index("msg-info")
    assert q_pos < t_pos < i_pos, (
        f"Sort order wrong: question={q_pos}, task={t_pos}, info={i_pos}\n{out}"
    )


def test_check_shows_deadline_indicator(project: Path):
    """Imminent deadlines surface a [DEADLINE …] indicator in the line."""
    active = project / ".agents" / "bus" / "active"
    imminent = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    (active / "msg-urgent.md").write_text(
        f"---\nid: msg-urgent\nfrom: x\nto: cli-agent\npriority: normal\n"
        f"type: question\nexpects-response: true\nresponse-deadline: {imminent}\n"
        f"timestamp: 2026-06-04T10:00:00Z\n---\n\n## Subject: imminent\n",
        encoding="utf-8",
    )
    env = {**os.environ, "OTAMAN_AGENT": "cli-agent", "NO_COLOR": "1"}
    rc = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "check", "cli-agent"],
        capture_output=True, text=True, cwd=str(project), env=env,
    )
    assert rc.returncode == 0, rc.stderr
    # Indicator should appear; color codes are stripped via NO_COLOR
    # (some color codes may still slip — match the bracket prefix)
    assert "[DEADLINE" in rc.stdout, rc.stdout
