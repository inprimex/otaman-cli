"""interactive-human-console 1.1 — outcome-proposals in the HITL queue + the
private-server (fleet-socket) refusal guard.

The queue already listed spec-change-requests + rendered a full-body read view;
1.1 adds outcome-proposals to the queue (de-duping CC copies) and refuses launch
on the fleet tmux server (D1: the never-inject boundary is structural).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.console import bus, decision, seat  # noqa: E402


def _program(root: Path, name: str = "prog"):
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text(
        f"project: {name}\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name=name, root=root)


def _stage(root: Path, stem: str, *, mtype, to="human", subject="idea", x_cc=False, acked=False):
    active = root / ".agents" / "bus" / "active"
    xcc = "x-cc: true\n" if x_cc else ""
    (active / f"{stem}.md").write_text(
        f"---\nfrom: cofounder-agent\nto: {to}\npriority: high\ntype: {mtype}\n{xcc}"
        f"timestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n"
        f"## Subject: {subject}\n\nbody text\n",
        encoding="utf-8",
    )
    if acked:
        (active / "acks" / f"{stem}.human.ack").write_text("ok\n", encoding="utf-8")


# ---- queue includes outcome-proposals ----


def test_queue_includes_scr_and_outcome_proposal(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000001-a-to-human-spec-change-request", mtype="spec-change-request")
    _stage(tmp_path, "20260101T000002-c-to-human-outcome-proposal", mtype="outcome-proposal")
    pending = bus.list_pending_proposals(prog)
    by_type = {p.msg_type for p in pending}
    assert by_type == {"spec-change-request", "outcome-proposal"}


def test_queue_skips_outcome_proposal_cc_copies(tmp_path):
    prog = _program(tmp_path)
    # the human's primary (kept) + a strategic CC copy (x-cc → skipped)
    _stage(tmp_path, "20260101T000003-c-to-human-outcome-proposal", mtype="outcome-proposal")
    _stage(
        tmp_path,
        "20260101T000003-c-to-cpo-agent-outcome-proposal",
        mtype="outcome-proposal",
        to="cpo-agent",
        x_cc=True,
    )
    pending = bus.list_pending_proposals(prog)
    assert len(pending) == 1 and pending[0].from_agent == "cofounder-agent"


def test_queue_excludes_acked_outcome_proposal(tmp_path):
    prog = _program(tmp_path)
    _stage(
        tmp_path,
        "20260101T000004-c-to-human-outcome-proposal",
        mtype="outcome-proposal",
        acked=True,
    )
    assert bus.list_pending_proposals(prog) == []


# ---- fleet-socket refusal guard ----


def test_on_fleet_server_detection(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    assert seat.on_fleet_server() is True
    monkeypatch.setenv("TMUX", f"/tmp/tmux-1000/{seat.PRIVATE_SOCKET},123,0")
    assert seat.on_fleet_server() is False
    monkeypatch.delenv("TMUX", raising=False)
    assert seat.on_fleet_server() is False  # not inside tmux → fine


def test_run_console_refuses_on_fleet_socket(monkeypatch, capsys):
    from otaman_cli.console import launch

    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    rc = launch.run_console(["--no-seat"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "private" in out.lower() and seat.PRIVATE_SOCKET in out


# ---- outcome-proposal decisions: audit sign-off (1.2 supersedes the 1.1 guard) ----


def test_outcome_proposal_approve_is_audit_signoff(tmp_path):
    from otaman_cli.console.identity import resolve_identity

    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000009-c-to-human-outcome-proposal", mtype="outcome-proposal")
    op = bus.list_pending_proposals(prog)[0]
    ident = resolve_identity(prog.root)
    ok, msg = decision.approve(prog, op, ident, reason="worth pursuing")
    assert ok is True and "audit" in msg
    # audit entry written + item acked (drops from the queue), no spec-change signal
    assert bus.list_pending_proposals(prog) == []
    active = tmp_path / ".agents" / "bus" / "active"
    assert not list(active.glob("*spec-change-approved*"))
    assert list(active.glob("*console-approved*"))
