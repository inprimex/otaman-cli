"""interactive-human-console 1.2 — decision verbs approve / reject / defer.

Each verb records a reason and leaves a bus audit entry. SCR approve mints the
`spec-change-approved` broadcast with parity to `/otaman:approve`; SCR reject
mints `spec-change-rejected`; outcome-proposal decisions are audit sign-offs
(no invented cross-domain signal); defer records an audit entry and leaves the
item pending. Logic is tested directly (the Textual loop isn't unit-tested).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.console import bus, decision  # noqa: E402
from otaman_cli.console.identity import resolve_identity  # noqa: E402


def _program(root: Path):
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    return bus.Program(name="p", root=root)


def _stage(root: Path, stem: str, *, mtype, subject="a thing"):
    (root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nfrom: core-agent\nto: human\npriority: high\ntype: {mtype}\n"
        f"timestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n## Subject: {subject}\n\nbody\n",
        encoding="utf-8",
    )


def _active(root):
    return root / ".agents" / "bus" / "active"


# ---- SCR: parity signals ----


def test_scr_approve_mints_spec_change_approved(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000001-a-to-human-spec-change-request", mtype="spec-change-request")
    op = bus.list_pending_proposals(prog)[0]
    ok, _msg = decision.approve(prog, op, resolve_identity(prog.root), reason="LGTM")
    assert ok is True
    approved = list(_active(tmp_path).glob("*spec-change-approved*"))
    assert approved, "SCR approve must mint the spec-change-approved broadcast (parity)"
    assert "LGTM" in approved[0].read_text(encoding="utf-8")  # reason recorded
    assert bus.list_pending_proposals(prog) == []  # acked → drops from queue


def test_scr_reject_mints_spec_change_rejected(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000002-a-to-human-spec-change-request", mtype="spec-change-request")
    op = bus.list_pending_proposals(prog)[0]
    ok, _msg = decision.reject(prog, op, resolve_identity(prog.root), reason="too broad")
    assert ok is True
    rejected = list(_active(tmp_path).glob("*spec-change-rejected*"))
    assert rejected and "too broad" in rejected[0].read_text(encoding="utf-8")


# ---- defer: audit entry, item stays pending, both types ----


def test_defer_records_audit_and_keeps_pending(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000003-a-to-human-spec-change-request", mtype="spec-change-request")
    op = bus.list_pending_proposals(prog)[0]
    ok, msg = decision.defer(prog, op, resolve_identity(prog.root), reason="need more info")
    assert ok is True and "still pending" in msg
    audit = list(_active(tmp_path).glob("*console-deferred*"))
    assert audit and "need more info" in audit[0].read_text(encoding="utf-8")
    # defer is NOT a resolution — the item is still in the queue
    assert len(bus.list_pending_proposals(prog)) == 1


def test_defer_works_on_outcome_proposal(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000004-c-to-human-outcome-proposal", mtype="outcome-proposal")
    op = bus.list_pending_proposals(prog)[0]
    ok, _msg = decision.defer(prog, op, resolve_identity(prog.root))
    assert ok is True
    assert len(bus.list_pending_proposals(prog)) == 1  # still pending


# ---- outcome-proposal: audit sign-off, no spec-change signal ----


def test_outcome_reject_is_audit_only(tmp_path):
    prog = _program(tmp_path)
    _stage(tmp_path, "20260101T000005-c-to-human-outcome-proposal", mtype="outcome-proposal")
    op = bus.list_pending_proposals(prog)[0]
    ok, _msg = decision.reject(prog, op, resolve_identity(prog.root), reason="off-strategy")
    assert ok is True
    assert not list(_active(tmp_path).glob("*spec-change-rejected*"))  # no spec signal
    audit = list(_active(tmp_path).glob("*console-rejected*"))
    assert audit and "off-strategy" in audit[0].read_text(encoding="utf-8")
    assert bus.list_pending_proposals(prog) == []  # acked
