"""ratify-spec-approve-split 1.4 — ratify on an AUTHORED change.

Ratification is a floor at `approved` (core 1.1, monotonic). On an authored
change that floor is already behind the current stage, so a plain ratify would
record an attestation and move nothing — leaving the change short of
spec-approved and still blocked at the dispatch gate, which is precisely the
contradictory record `otaman spec reconcile` reports.

Canon: advance to spec-approved when the ratifier passes the same approver check
that mints it (naming which gate was advanced), else refuse pointing at
`otaman spec approve`. A ratify that cannot produce a correct stage fails loudly
rather than writing an incorrect one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.commands.spec import _eligible_spec_approver, cmd_ratify


def _roster(*entries: dict) -> list[dict]:
    return list(entries)


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program whose specs repo holds one authored and one dispatched change."""
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "specs": {"path": "../specs"},
                "human-roster": _roster(
                    {"name": "roman", "email": "r@x.io", "roles": ["cto"]},
                    {"name": "sam", "email": "s@x.io", "roles": ["developer"]},
                ),
            }
        ),
        encoding="utf-8",
    )
    changes = tmp_path / "specs" / "openspec" / "changes"
    for name, stage in (("authored-one", "authored"), ("moving", "dispatched")):
        d = changes / name
        d.mkdir(parents=True)
        (d / ".openspec.yaml").write_text(f"stage: {stage}\n", encoding="utf-8")
        (d / "proposal.md").write_text("# p\n", encoding="utf-8")

    # The autouse conftest sandbox pins OTAMAN_ROOT at its own tmp program;
    # repoint it (and the legacy var) so find_project_root resolves THIS fixture.
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_ROOT", str(root))
    monkeypatch.setenv("MAESTRO_ROOT", str(root))
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    # HUMAN-DECISION confirm is a TTY interaction; the tests drive the logic
    # around it, so it is satisfied explicitly per-test.
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *a, **k: True)
    return root, changes


def _stage(changes: Path, name: str) -> str:
    data = yaml.safe_load((changes / name / ".openspec.yaml").read_text(encoding="utf-8")) or {}
    return data.get("stage", "")


def _data(changes: Path, name: str) -> dict:
    return yaml.safe_load((changes / name / ".openspec.yaml").read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# an approver ratifying an authored change advances the dispatch gate


def test_authored_advances_to_spec_approved(program, capsys):
    root, changes = program
    rc = cmd_ratify(["authored-one", "--reason", "urgent unblock"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert _stage(changes, "authored-one") == "spec-approved"


def test_advance_names_the_gate_it_moved(program, capsys):
    root, changes = program
    cmd_ratify(["authored-one", "--reason", "x"])
    out = capsys.readouterr().out
    assert "DISPATCH gate" in out
    assert "authored → spec-approved" in out
    assert "roman" in out  # the approver is named


def test_advance_records_both_markers(program):
    """The ratification attestation AND the approver are both recorded — the
    advance composes onto apply_ratification, it doesn't replace it."""
    root, changes = program
    cmd_ratify(["authored-one", "--reason", "why"])
    data = _data(changes, "authored-one")
    assert data["ratified"] is True
    assert data["ratified_at"]
    assert "roman" in str(data.get("approved_by", ""))
    assert data.get("spec_approved_by") == "roman"


def test_reported_stage_is_the_stage_actually_written(program, capsys):
    """The old output hardcoded "→ stage=approved", which became untrue the
    moment ratify went monotonic. A wrong-stage REPORT is the same class of lie
    as a wrong-stage write."""
    root, changes = program
    cmd_ratify(["moving", "--reason", "keep going"])
    out = capsys.readouterr().out
    assert _stage(changes, "moving") == "dispatched"  # floor, not assignment
    assert "stage=dispatched" in out
    assert "stage=approved" not in out


# ---------------------------------------------------------------------------
# a non-approver is refused, loudly, with the verb that works


def test_non_approver_is_refused_on_authored(program, capsys, monkeypatch):
    root, changes = program
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")  # developer, no cto/approver hat
    rc = cmd_ratify(["authored-one", "--reason", "please"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "otaman spec approve authored-one" in out  # points at the verb that does
    assert "authored" in out


def test_refusal_leaves_no_wrong_stage_write(program, capsys, monkeypatch):
    root, changes = program
    before = _data(changes, "authored-one")
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")
    cmd_ratify(["authored-one", "--reason", "please"])
    capsys.readouterr()
    after = _data(changes, "authored-one")
    assert after == before  # untouched: no ratified marker, no stage change
    assert _stage(changes, "authored-one") == "authored"


def test_refusal_happens_before_the_human_confirm(program, capsys, monkeypatch):
    """A doomed ratify must refuse rather than prompt a human to confirm it."""
    root, changes = program
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")
    called: list[str] = []
    monkeypatch.setattr(
        "otaman_cli.safety.confirm_human_decision",
        lambda *a, **k: called.append("asked") or True,
    )
    assert cmd_ratify(["authored-one", "--reason", "x"]) == 2
    capsys.readouterr()
    assert called == []  # never asked


def test_confirm_prompt_announces_the_advance(program, capsys, monkeypatch):
    root, changes = program
    prompts: list[str] = []
    monkeypatch.setattr(
        "otaman_cli.safety.confirm_human_decision",
        lambda desc, *a, **k: prompts.append(desc) or True,
    )
    cmd_ratify(["authored-one", "--reason", "x"])
    capsys.readouterr()
    assert prompts and "spec-approved" in prompts[0]
    assert "dispatch gate" in prompts[0].lower()


# ---------------------------------------------------------------------------
# non-authored stages keep their existing behavior


def test_dispatched_change_is_not_advanced(program, capsys):
    root, changes = program
    rc = cmd_ratify(["moving", "--reason", "carry on"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _stage(changes, "moving") == "dispatched"
    assert "DISPATCH gate" not in out  # nothing was advanced
    assert _data(changes, "moving")["ratified"] is True


def test_non_approver_may_still_ratify_a_non_authored_change(program, capsys, monkeypatch):
    """The new refusal is scoped to the authored case — it must not tighten
    ratify everywhere (that would be an unrequested policy change)."""
    root, changes = program
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")
    rc = cmd_ratify(["moving", "--reason", "still fine"])
    capsys.readouterr()
    assert rc == 0
    assert _data(changes, "moving")["ratified"] is True


# ---------------------------------------------------------------------------
# the shared approver resolution


def test_eligible_spec_approver_resolves_the_hat(program):
    root, _ = program
    entry = _eligible_spec_approver(root)
    assert entry is not None and entry.name == "roman"


def test_eligible_spec_approver_none_without_the_hat(program, monkeypatch):
    root, _ = program
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")
    assert _eligible_spec_approver(root) is None


def test_eligible_spec_approver_none_when_unresolvable(tmp_path):
    bare = tmp_path / "nothing"
    bare.mkdir()
    assert _eligible_spec_approver(bare) is None  # graceful, never raises


# ---------------------------------------------------------------------------
# founder-mode: a program with NO roster must still reach spec-approved
#
# resolve_spec_approver has no stand-in for an absent roster, so refusing here
# would leave a rosterless program with NO path to spec-approved (`otaman spec
# approve` needs the same hat) — the very dead end this change removes, just
# relocated. These pin the deliberate carve-out.


@pytest.fixture
def rosterless(tmp_path, monkeypatch):
    root = tmp_path / "solo"
    (root / ".agents").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: solo\nspecs:\n  path: ../specs\n", encoding="utf-8"
    )
    changes = tmp_path / "specs" / "openspec" / "changes"
    d = changes / "keystone"
    d.mkdir(parents=True)
    (d / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_ROOT", str(root))
    monkeypatch.setenv("MAESTRO_ROOT", str(root))
    monkeypatch.setenv("OTAMAN_HUMAN", "solo-founder")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *a, **k: True)
    return root, changes


def test_rosterless_program_can_still_advance(rosterless, capsys):
    root, changes = rosterless
    rc = cmd_ratify(["keystone", "--reason", "solo operator"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert _stage(changes, "keystone") == "spec-approved"  # a path EXISTS
    assert _data(changes, "keystone")["spec_approved_by"] == "solo-founder"


def test_rosterless_advance_says_it_was_founder_mode(rosterless, capsys):
    """Honest output: the advance happened without a hat check, and says so."""
    root, _ = rosterless
    cmd_ratify(["keystone", "--reason", "solo"])
    out = capsys.readouterr().out.lower()
    assert "founder-mode" in out and "no human-roster" in out


def test_no_roster_means_no_refusal(rosterless):
    from otaman_cli.commands.spec import _has_human_roster, _ratify_advance_approver

    root, _ = rosterless
    assert _has_human_roster(root) is False
    approver, refusal = _ratify_advance_approver(root, "solo-founder")
    assert refusal is None
    assert approver is not None and approver.name == "solo-founder"
