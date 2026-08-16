"""bus-test-isolation task 2.1 — gated commands ledger-record on confirmation.

`approve` / `emergency-halt` / `hitl take` append a confirmation-ledger
record for the privileged bus file they produce, and refuse to write the
file when the append fails (fail closed: no record, no bus file).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from otaman_core.confirmations import LedgerError, hash_message, verify_confirmation

from otaman_cli.commands.approve import cmd_approve
from otaman_cli.commands.emergency_halt import cmd_emergency_halt
from otaman_cli.hitl.messages import HumanDecisionPayload, emit_human_decision


@pytest.fixture
def project(tmp_path, monkeypatch):
    meta = tmp_path / "meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: t\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(meta)
    monkeypatch.setenv("OTAMAN_ROOT", str(meta))
    return meta


def _confirm_yes(monkeypatch):
    monkeypatch.setattr(
        "otaman_cli.commands.emergency_halt.confirm_human_decision", lambda *a, **k: True
    )
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *a, **k: True)


def _active(meta: Path) -> Path:
    return meta / ".agents" / "bus" / "active"


def _fail_append(monkeypatch):
    def _boom(**_kw):
        raise LedgerError("disk says no")

    import otaman_core.confirmations as _conf

    monkeypatch.setattr(_conf, "append_confirmation", _boom)


# ---------------------------------------------------------------------------
# emergency-halt


def test_halt_appends_verifiable_record(project, monkeypatch, _isolated_ledger):
    _confirm_yes(monkeypatch)
    rc = cmd_emergency_halt(["--reason", "test isolation drill"])
    assert rc == 0
    files = list(_active(project).glob("*emergency-halt*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    msg_id = re.search(r"^id:\s*(\S+)", content, re.MULTILINE).group(1)
    assert verify_confirmation(
        message_id=msg_id, content_hash=hash_message(content), path=_isolated_ledger
    )


def test_halt_refuses_bus_write_when_ledger_fails(project, monkeypatch):
    _confirm_yes(monkeypatch)
    _fail_append(monkeypatch)
    rc = cmd_emergency_halt(["--reason", "should not be written"])
    assert rc == 1
    assert list(_active(project).glob("*emergency-halt*.md")) == []


# ---------------------------------------------------------------------------
# approve / reject


def _plant_proposal(meta: Path, stem: str) -> None:
    (_active(meta) / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: cli-agent\nto: human\npriority: high\n"
        "type: spec-change-request\ntimestamp: 2026-08-16T10:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: add widget\n",
        encoding="utf-8",
    )


def test_approve_appends_verifiable_record(project, monkeypatch, _isolated_ledger):
    _confirm_yes(monkeypatch)
    _plant_proposal(project, "20260816T100000-cli-agent-to-human-spec-change-request")
    rc = cmd_approve(["approve", "20260816T100000"])
    assert rc == 0
    files = list(_active(project).glob("*spec-change-approved*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    msg_id = re.search(r"^id:\s*(\S+)", content, re.MULTILINE).group(1)
    assert verify_confirmation(
        message_id=msg_id, content_hash=hash_message(content), path=_isolated_ledger
    )


def test_approve_refuses_everything_when_ledger_fails(project, monkeypatch):
    _confirm_yes(monkeypatch)
    _fail_append(monkeypatch)
    _plant_proposal(project, "20260816T100000-cli-agent-to-human-spec-change-request")
    rc = cmd_approve(["approve", "20260816T100000"])
    assert rc == 1
    assert list(_active(project).glob("*spec-change-approved*.md")) == []
    # the human ack must not exist either — no partial approval state
    assert list((_active(project) / "acks").glob("*.human.ack")) == []


def test_reject_refuses_everything_when_ledger_fails(project, monkeypatch):
    _confirm_yes(monkeypatch)
    _fail_append(monkeypatch)
    _plant_proposal(project, "20260816T100000-cli-agent-to-human-spec-change-request")
    rc = cmd_approve(["reject", "20260816T100000", "--desc", "nope"])
    assert rc == 1
    assert list(_active(project).glob("*spec-change-rejected*.md")) == []
    assert list((_active(project) / "acks").glob("*.human.ack")) == []


# ---------------------------------------------------------------------------
# hitl take (emit_human_decision is the write path)


def _payload() -> HumanDecisionPayload:
    return HumanDecisionPayload(
        in_reply_to="20260816T090000-req",
        session_id="sess-1",
        to_agent="cli-agent",
        decision="approved",
        decided_by="romans",
        rationale="",
        followup_actions="",
        subject="Re: please review",
    )


def test_emit_human_decision_appends_verifiable_record(tmp_path, _isolated_ledger):
    active = tmp_path / "bus" / "active"
    out = emit_human_decision(_payload(), active)
    content = out.read_text(encoding="utf-8")
    msg_id = re.search(r"^id:\s*(\S+)", content, re.MULTILINE).group(1)
    assert verify_confirmation(
        message_id=msg_id, content_hash=hash_message(content), path=_isolated_ledger
    )


def test_emit_human_decision_writes_nothing_when_ledger_fails(tmp_path, monkeypatch):
    # emit imports append_confirmation at call time from the module
    import otaman_cli.hitl.messages as _messages  # noqa: F401 - documents the patch target

    def _boom(**_kw):
        raise LedgerError("no")

    import otaman_core.confirmations as _conf

    monkeypatch.setattr(_conf, "append_confirmation", _boom)
    active = tmp_path / "bus" / "active"
    with pytest.raises(LedgerError):
        emit_human_decision(_payload(), active)
    assert not active.exists() or list(active.glob("*.md")) == []
