"""The v-act stamps `approved_by` in the same write that advances the stage.

Spec-agent's conformance task (20261001T122219), from today's all-red CI: four
genuinely-approved changes carried `stage: spec-approved` and no `approved_by`,
so core's merge-gate conjunct (#91) refused them as
spec-approved-without-attestation. The cause was `advance_to_spec_approved`
calling bare `set_stage` — advancing the stage and writing no attestation.

Under the scoped-fields ruling the v-act is THE writer of that field. The
backfill was interim; this is the pattern, and the clause-3 requirement is that
a stage advance without the stamp fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.console import artifacts


@pytest.fixture
def program(tmp_path, monkeypatch):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nspecs:\n  path: ../specs\nrepos: []\n", encoding="utf-8"
    )
    change = tmp_path / "specs" / "openspec" / "changes" / "demo"
    change.mkdir(parents=True)
    (change / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    (change / "proposal.md").write_text("# demo\n", encoding="utf-8")
    (change / "tasks.md").write_text("- [ ] 1.1 @otaman-cli do it\n", encoding="utf-8")
    (change / "design.md").write_text("# design\n", encoding="utf-8")

    class _Approver:
        name = "roman"
        roles = ("cto",)

    monkeypatch.setattr(artifacts, "_eligible_approver", lambda p: _Approver())
    monkeypatch.setattr(artifacts, "_specs_changes_dir", lambda p: change.parent)
    monkeypatch.setattr(artifacts, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(
        "otaman_cli.console.lifecycle._commit_push", lambda *a, **k: (True, True, "")
    )
    monkeypatch.setattr("otaman_cli.console.lifecycle._specs_root", lambda p: change.parent)
    return Program(name="demo", root=root), change


def _written(change):
    return yaml.safe_load((change / ".openspec.yaml").read_text(encoding="utf-8")) or {}


def test_the_v_act_advances_the_stage(program):
    prog, change = program
    ok, message = artifacts.advance_to_spec_approved(prog, "demo")
    assert ok, message
    assert _written(change)["stage"] == "spec-approved"


def test_the_v_act_stamps_approved_by(program):
    """The conformance task, and clause 3: a stage advance without the stamp is
    the defect that reddened CI."""
    prog, change = program
    artifacts.advance_to_spec_approved(prog, "demo")
    data = _written(change)
    assert data.get("approved_by"), "stage advanced with no attestation"
    assert "roman" in data["approved_by"]


def test_the_stamp_carries_the_ruled_format(program):
    """spec-agent ruled the shape: who, that it was spec-approved, when, and by
    which act — so a later reader can tell a v-act stamp from a backfill."""
    prog, change = program
    artifacts.advance_to_spec_approved(prog, "demo")
    stamp = _written(change)["approved_by"]
    assert "spec-approved" in stamp
    assert "via otaman -i" in stamp
    assert "T" in stamp and "Z" in stamp, "no timestamp in the stamp"


def test_the_identity_field_is_written_too(program):
    """`spec_approved_by` is core's (D5, the identity); `approved_by` is the
    merge gate's attestation. Scoped fields — both, not either."""
    prog, change = program
    artifacts.advance_to_spec_approved(prog, "demo")
    data = _written(change)
    assert data.get("spec_approved_by") == "roman"
    assert data.get("approved_by")


def test_both_signals_land_in_one_write(program):
    """A stage advanced in one write and attested in another is a window where
    the repo states something untrue about itself."""
    prog, change = program
    writes = []
    import otaman_cli.console.lifecycle as lc

    original = lc._write_openspec

    def counting(path, data):
        writes.append(dict(data))
        return original(path, data)

    lc._write_openspec = counting
    try:
        artifacts.advance_to_spec_approved(prog, "demo")
    finally:
        lc._write_openspec = original
    assert len(writes) == 1, "stage and attestation must not be two writes"
    assert writes[0]["stage"] == "spec-approved" and writes[0]["approved_by"]


def test_the_merge_gate_accepts_what_the_v_act_wrote(program):
    """The real subject: core's conjunct refused these four this morning."""
    from otaman_core.spec_lifecycle import spec_approved_reached

    prog, change = program
    artifacts.advance_to_spec_approved(prog, "demo")
    data = _written(change)
    assert spec_approved_reached(data), "core still does not consider this spec-approved"


def test_an_already_approved_change_is_refused(program):
    prog, change = program
    (change / ".openspec.yaml").write_text(
        "stage: spec-approved\napproved_by: someone\n", encoding="utf-8"
    )
    ok, message = artifacts.advance_to_spec_approved(prog, "demo")
    assert not ok and "already at or past" in message


def test_a_non_approver_cannot_stamp(program, monkeypatch):
    """The stamp is an attestation — it must not be writable by whoever asks."""
    prog, change = program
    monkeypatch.setattr(artifacts, "_eligible_approver", lambda p: None)
    ok, message = artifacts.advance_to_spec_approved(prog, "demo")
    assert not ok and "approver" in message
    assert "approved_by" not in _written(change), "a refused act must write nothing"


def test_the_cli_approve_verb_uses_the_same_act():
    """`otaman spec approve` delegates rather than carrying a second
    implementation — which is why this fix covers both surfaces."""
    import inspect

    from otaman_cli.commands.spec import _cmd_approve

    assert "advance_to_spec_approved" in inspect.getsource(_cmd_approve)


def test_the_act_no_longer_calls_bare_set_stage():
    """The defect itself: `set_stage` advances and attests nothing."""
    src = Path(artifacts.__file__).read_text(encoding="utf-8")
    body = src[src.index("def advance_to_spec_approved") :]
    body = body[: body.index("\ndef ")]
    assert "set_stage(" not in body
    assert "apply_spec_approved(" in body
