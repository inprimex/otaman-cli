"""hitl-default-approver conformance — doctor surfaces the missing-approver ERROR.

`otaman doctor` calls core's ``check_approver_config`` UNCONDITIONALLY: an
absent/empty human-roster while the approval path is live (HITL configured OR
pending proposals) is an ERROR. Regression for deploy's opt-out 3.2 E2E, where
a tenant with NO human-roster block + a pending proposal wrongly passed.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands.doctor import (
    _check_approver_config,
    _has_pending_proposals,
    _print_approver_config_report,
)


def _program(tmp_path: Path, *, roster: str | None) -> Path:
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    body = "project: p\nversion: '1.0'\nrepos: []\n"
    if roster is not None:
        body += "human-roster:\n" + roster
    (root / "platform.yaml").write_text(body, encoding="utf-8")
    return root


def _stage_proposal(root: Path, stem: str, *, acked: bool = False) -> None:
    active = root / ".agents" / "bus" / "active"
    (active / f"{stem}.md").write_text(
        "---\nfrom: a\nto: human\ntype: spec-change-request\n"
        "timestamp: t\nstatus: pending\n---\n\n## Subject: x\n",
        encoding="utf-8",
    )
    if acked:
        (active / "acks" / f"{stem}.human.ack").write_text("approved\n", encoding="utf-8")


# --- the core regression: absent roster + pending proposal must ERROR ---------


def test_absent_roster_with_pending_proposal_is_error(tmp_path):
    root = _program(tmp_path, roster=None)  # NO human-roster block
    _stage_proposal(root, "20260101T0-a-to-human-spec-change-request")
    rc, findings = _check_approver_config(root)
    assert rc == 1  # folds into doctor's exit code
    assert any(f["level"] == "error" and "approver" in f["message"] for f in findings)


def test_absent_roster_without_live_path_is_clean(tmp_path):
    # no roster, but no pending proposals and no HITL config → path not live
    root = _program(tmp_path, roster=None)
    rc, findings = _check_approver_config(root)
    assert rc == 0 and findings == []


def test_approver_present_with_pending_is_clean(tmp_path):
    root = _program(tmp_path, roster="  - name: roman\n    email: r@x.io\n    roles: [approver]\n")
    _stage_proposal(root, "20260101T0-a-to-human-spec-change-request")
    rc, _ = _check_approver_config(root)
    assert rc == 0


def test_roster_without_approver_and_pending_is_error(tmp_path):
    root = _program(tmp_path, roster="  - name: roman\n    email: r@x.io\n    roles: [developer]\n")
    _stage_proposal(root, "20260101T0-a-to-human-spec-change-request")
    rc, findings = _check_approver_config(root)
    assert rc == 1
    assert any(f["level"] == "error" for f in findings)


# --- pending-proposal detection ----------------------------------------------


def test_pending_detection_ignores_acked_and_non_scr(tmp_path):
    root = _program(tmp_path, roster=None)
    assert _has_pending_proposals(root) is False
    _stage_proposal(root, "20260101T0-a-to-human-spec-change-request", acked=True)
    assert _has_pending_proposals(root) is False  # acked → not pending
    (root / ".agents" / "bus" / "active" / "20260102T0-b-to-human-info.md").write_text(
        "---\nfrom: b\nto: human\ntype: info\ntimestamp: t\n---\n\nx\n", encoding="utf-8"
    )
    assert _has_pending_proposals(root) is False  # info is not a proposal
    _stage_proposal(root, "20260103T0-c-to-human-spec-change-request")
    assert _has_pending_proposals(root) is True  # un-acked SCR → pending


# --- report rendering ---------------------------------------------------------


def test_report_prints_fail_for_error_and_nothing_when_clean(capsys):
    _print_approver_config_report([])
    assert capsys.readouterr().out == ""
    _print_approver_config_report([{"level": "error", "message": "no approver"}])
    out = capsys.readouterr().out
    assert "FAIL" in out and "no approver" in out
    _print_approver_config_report([{"level": "warn", "message": "missing email"}])
    assert "WARN" in capsys.readouterr().out
