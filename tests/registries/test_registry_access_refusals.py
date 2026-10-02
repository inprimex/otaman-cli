"""registry-access-contract 1.2 — what the chokepoint REFUSES.

Two refusals arrive with the rewire, and both are deliberate behaviour changes:

1. **No resolved roster human → no authority write.** `accept-cost`, `choose` and
   `reject-cost` are core's `APPROVAL_REQUIRED_ACTIONS`: the contract raises on a missing
   or malformed approval, so the only choice this surface has is between a clean refusal
   with a remedy and an exception. Mode 1 only *advised* on the hat.
2. **No contract in the bundle → no write at all.** Keeping the old `yaml_dump` alive as
   a fallback would leave exactly the second access home the capability spec calls a
   conformance defect, so an old bundle refuses instead of writing around the door.

Both are pinned on the file bytes as well as the exit code: a refusal that still wrote
is the failure these tests exist to catch.
"""

from __future__ import annotations

import pytest
import yaml

import otaman_cli.registries.cli_outcome as CO
import otaman_cli.registries.cli_persona as CP
import otaman_cli.registries.cli_solution as CS
from otaman_cli.registries import access

OUTCOME = {
    "id": "JTBD-1-signin",
    "status": "Backlog",
    "created": "2026-09-01",
    "statement": {
        "as-a": "an operator",
        "i-want-to": "sign in",
        "incremental-outcome": "fewer lockouts",
        "so-i-can": "get to work",
    },
}
SOLUTION = {"id": "SOL-A", "outcome-id": "JTBD-1-signin", "status": "Considering"}
PERSONA = {
    "id": "persona-operator",
    "name": "Operator",
    "description": "runs the fleet",
    "kind": "internal",
}


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A minimal program whose registers are schema-valid (the writers validate)."""
    root = tmp_path / "meta"
    strat = tmp_path / "strategy"
    root.mkdir()
    strat.mkdir()
    (root / "platform.yaml").write_text(
        "project: d\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - name: roman\n    roles: [cto, cofounder, approver]\n",
        encoding="utf-8",
    )
    (strat / "outcomes.yaml").write_text(yaml.dump({"outcomes": [dict(OUTCOME)]}), encoding="utf-8")
    (strat / "solutions.yaml").write_text(
        yaml.dump({"solutions": [dict(SOLUTION)]}), encoding="utf-8"
    )
    (strat / "personas.yaml").write_text(yaml.dump({"personas": [dict(PERSONA)]}), encoding="utf-8")
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    for mod in (CO, CS, CP):
        monkeypatch.setattr(mod, "find_project_root", lambda: root)
        monkeypatch.setattr(mod, "_ctx", lambda r: ("roman", ["cto"], None))
        monkeypatch.setattr(mod, "_emit_bus", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(mod, "hat_advisory", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(mod, "authz_advisory", lambda *a, **k: None, raising=False)
    return strat


# ---------------------------------------------------------------------------
# 1. the authority actions refuse without a resolved roster human


@pytest.mark.parametrize(
    ("verb", "args"),
    [
        ("cmd_choose", {"id": "JTBD-1-signin", "solution": "SOL-A"}),
        ("cmd_accept_cost", {"id": "JTBD-1-signin", "solution": "SOL-A"}),
        ("cmd_reject_cost", {"id": "JTBD-1-signin", "reason": "too dear"}),
    ],
)
def test_an_authority_verb_refuses_when_no_human_resolves(program, capsys, verb, args):
    before = (program / "outcomes.yaml").read_bytes()

    rc = getattr(CO, verb)(args)

    assert rc == 2, f"{verb} must refuse with exit 2, got {rc}"
    assert (program / "outcomes.yaml").read_bytes() == before, (
        f"{verb} refused but still wrote the register"
    )
    captured = capsys.readouterr()
    err = captured.out + captured.err  # UI.error prints on stdout
    assert "human-roster" in err, f"the refusal must name what is missing; got: {err!r}"
    assert "OTAMAN_HUMAN" in err, f"the refusal must name the remedy; got: {err!r}"


def test_the_refusal_names_the_proposal_route_for_an_agent(program, capsys):
    """An agent with no human behind it is told what to do INSTEAD, not just 'no'."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    captured = capsys.readouterr()
    assert "outcome-proposal" in captured.out + captured.err


def test_an_authority_verb_records_the_approval_when_a_human_resolves(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")

    assert CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0

    data = yaml.safe_load((program / "outcomes.yaml").read_text(encoding="utf-8"))
    chooses = [t for t in data["outcomes"][0]["transitions"] if t["action"] == "choose"]
    assert len(chooses) == 1
    approval = chooses[0].get("approval")
    assert approval, "a choose with authority must record HOW it was authorized"
    assert approval["by"] == "roman"
    assert approval["via"] in ("hitl", "roster-role", "hat")
    assert approval["spec"] == CO.APPROVAL_SPEC
    assert approval["at"]


# ---------------------------------------------------------------------------
# 2. no contract → no write, in any of the three rewired surfaces


@pytest.mark.parametrize(
    ("module", "verb", "args", "register"),
    [
        (CO, "cmd_promote", {"id": "JTBD-1-signin"}, "outcomes.yaml"),
        (CS, "cmd_discard", {"id": "SOL-A"}, "solutions.yaml"),
        (CP, "cmd_retire", {"id": "persona-operator"}, "personas.yaml"),
    ],
)
def test_a_write_verb_refuses_when_the_bundle_has_no_contract(
    program, capsys, monkeypatch, module, verb, args, register
):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr(access, "contract", lambda: None)
    before = (program / register).read_bytes()

    rc = getattr(module, verb)(args)

    assert rc == 2, f"{verb} must refuse an old bundle with exit 2, got {rc}"
    assert (program / register).read_bytes() == before, (
        f"{verb} refused the contract-less bundle but still wrote {register}"
    )
    captured = capsys.readouterr()
    err = captured.out + captured.err
    assert "registry-access-contract" in err
    assert "Refusing to write the register directly" in err
