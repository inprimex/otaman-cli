"""registry-access-contract 2.1 — choosing and funding are different hats (D2).

"CTO chooses (technical judgment), CEO funds (budget authority) — two canonical hats
the current `accept-cost --solution` collapses. Team-mode: separate verbs, hat-checked.
Founder-mode: one INVOCATION may do both — friction-free for Roman — but the transition
log always records `choose` and `accept-cost` as distinct entries with their hats. The
record never collapses even when the keystroke does."

The mode is not a setting: it is a property of the acting human's hats. One person
holding both hats IS founder-mode — there is no second party whose decision the
combined keystroke would collapse. So these tests vary the ROSTER, not a config flag.
"""

from __future__ import annotations

import pytest
import yaml

import otaman_cli.registries.cli_outcome as CO
from otaman_cli.registries.roles import CHOOSE_HATS, FUND_HATS, held_hat, operating_mode

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
SOLUTIONS = [
    {"id": "SOL-A", "outcome-id": "JTBD-1-signin", "status": "Considering"},
    {"id": "SOL-B", "outcome-id": "JTBD-1-signin", "status": "Considering"},
]


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program whose roster this test parameterises: `write_roster(roles)`."""
    root = tmp_path / "meta"
    strat = tmp_path / "strategy"
    root.mkdir()
    strat.mkdir()

    def write_roster(roles: list[str], *, name: str = "roman") -> None:
        (root / "platform.yaml").write_text(
            "project: d\nversion: '1.0'\nrepos: []\n"
            "human-roster:\n"
            f"  - name: {name}\n    roles: [{', '.join(roles)}]\n",
            encoding="utf-8",
        )

    write_roster(["founder"])
    (strat / "outcomes.yaml").write_text(yaml.dump({"outcomes": [dict(OUTCOME)]}), encoding="utf-8")
    (strat / "solutions.yaml").write_text(
        yaml.dump({"solutions": [dict(s) for s in SOLUTIONS]}), encoding="utf-8"
    )
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr(CO, "find_project_root", lambda: root)
    monkeypatch.setattr(CO, "_ctx", lambda r: ("roman", ["ceo"], None))
    monkeypatch.setattr(CO, "hat_advisory", lambda *a, **k: None)
    monkeypatch.setattr(CO, "authz_advisory", lambda *a, **k: None)
    monkeypatch.setattr(CO, "_emit_bus", lambda *a, **k: None)

    class P:
        pass

    P.root = root
    P.outcomes = strat / "outcomes.yaml"
    P.write_roster = staticmethod(write_roster)
    return P


def _record(program) -> dict:
    return yaml.safe_load(program.outcomes.read_text(encoding="utf-8"))["outcomes"][0]


def _actions(program) -> list[str]:
    return [t["action"] for t in _record(program).get("transitions", [])]


# ---------------------------------------------------------------------------
# the mode follows from the hats


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (["founder"], "founder"),  # the stand-in holds both
        (["cto", "ceo"], "founder"),  # both hats, explicitly
        (["cto"], "team"),  # chooses but cannot fund
        (["ceo"], "team"),  # funds but cannot choose
        (["cpo", "approver"], "team"),  # neither
    ],
)
def test_the_mode_is_read_off_the_acting_humans_hats(program, roles, expected):
    program.write_roster(roles)
    assert operating_mode(program.root) == expected


def test_an_unresolved_human_is_team_mode(program, monkeypatch):
    """No identity → no hats → the cautious mode, which refuses the combined form.

    The default must be the one that cannot collapse two decisions by accident.
    """
    program.write_roster(["founder"])
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    assert operating_mode(program.root) == "team"


def test_the_specific_hat_wins_over_the_founder_stand_in():
    """A log entry should say `hat: cto` for someone who holds cto, not `founder`."""
    assert held_hat(frozenset({"founder", "cto"}), CHOOSE_HATS) == "cto"
    assert held_hat(frozenset({"founder"}), CHOOSE_HATS) == "founder"
    assert held_hat(frozenset({"cpo"}), FUND_HATS) is None


# ---------------------------------------------------------------------------
# founder-mode: the keystroke collapses, the record does not


def test_founder_mode_combined_records_both_decisions(program):
    program.write_roster(["cto", "ceo"])

    assert CO.cmd_accept_cost({"id": "JTBD-1-signin", "solution": "SOL-B"}) == 0

    record = _record(program)
    assert _actions(program) == ["choose", "accept-cost"], (
        "the combined invocation must append BOTH decisions, choose first"
    )
    choose, fund = record["transitions"]
    # shape-agnostic: the field audit is a `changes` list as of core #118, the flat
    # trio before it — the property is what was chosen, not which shape says so.
    from otaman_cli.registries.transitions import changed_field

    assert (changed_field(choose, "chosen-solution") or {}).get("new") == "SOL-B"
    assert "hat: cto" in choose["note"], f"the choose must carry the CTO hat: {choose}"
    assert "hat: ceo" in fund["note"], f"the fund must carry the CEO hat: {fund}"
    assert choose["approval"]["by"] == "roman"
    assert fund["approval"]["by"] == "roman"
    # and the outcome itself ends where both decisions put it
    assert record["chosen-solution"] == "SOL-B"
    assert record["cost-accepted"] is True
    assert record["status"] == "Approved"


def test_the_founder_stand_in_hat_is_named_as_itself(program):
    """A solo founder holds `founder`, and both entries say so — not `cto`/`ceo`."""
    program.write_roster(["founder"])

    assert CO.cmd_accept_cost({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0

    notes = [t.get("note", "") for t in _record(program)["transitions"]]
    assert _actions(program) == ["choose", "accept-cost"]
    assert all("hat: founder" in n for n in notes), notes


def test_the_combined_form_naming_the_existing_choice_funds_only(program):
    """Re-naming the solution already chosen is not a second choose.

    A `choose` entry recording old == new would claim a decision nobody made.
    """
    program.write_roster(["founder"])
    assert CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0
    before = len(_record(program)["transitions"])

    assert CO.cmd_accept_cost({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0

    added = _actions(program)[before:]
    assert added == ["accept-cost"], f"expected one entry, got {added}"


# ---------------------------------------------------------------------------
# team-mode: the verbs separate


def test_team_mode_refuses_the_combined_invocation_naming_both_verbs(program, capsys):
    program.write_roster(["ceo"])  # funds, but does not hold the choose hat
    before = program.outcomes.read_bytes()

    rc = CO.cmd_accept_cost({"id": "JTBD-1-signin", "solution": "SOL-B"})

    assert rc == 2
    assert program.outcomes.read_bytes() == before, "the refusal still wrote the register"
    out = capsys.readouterr()
    msg = out.out + out.err
    assert "otaman outcome choose JTBD-1-signin --solution SOL-B" in msg
    assert "otaman outcome accept-cost JTBD-1-signin" in msg
    assert "team-mode" in msg


def test_team_mode_funds_what_choose_already_chose(program):
    """The two-verb path: the CTO's decision is in the log with their hat on it."""
    program.write_roster(["cto"])
    assert CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"}) == 0
    program.write_roster(["ceo"])  # the hat changes hands

    assert CO.cmd_accept_cost({"id": "JTBD-1-signin"}) == 0

    record = _record(program)
    assert _actions(program)[0] == "choose"
    assert _actions(program)[-1] == "accept-cost"
    choose = next(t for t in record["transitions"] if t["action"] == "choose")
    fund = next(t for t in record["transitions"] if t["action"] == "accept-cost")
    assert "hat: cto" in choose["note"]
    assert "hat: ceo" in fund["note"]
    assert record["cost-accepted"] is True
    assert record["chosen-solution"] == "SOL-B"


def test_funding_nothing_refuses_and_names_choose(program, capsys):
    program.write_roster(["ceo"])
    before = program.outcomes.read_bytes()

    rc = CO.cmd_accept_cost({"id": "JTBD-1-signin"})

    assert rc == 2
    assert program.outcomes.read_bytes() == before
    out = capsys.readouterr()
    msg = out.out + out.err
    assert "no chosen-solution" in msg
    assert "otaman outcome choose JTBD-1-signin" in msg


def test_the_combined_form_cannot_choose_a_discarded_solution(program, capsys):
    """The combined form performs a choose, so it refuses what choose refuses."""
    program.write_roster(["founder"])
    sols = program.outcomes.parent / "solutions.yaml"
    data = yaml.safe_load(sols.read_text(encoding="utf-8"))
    data["solutions"][1]["status"] = "Discarded"
    sols.write_text(yaml.dump(data), encoding="utf-8")
    before = program.outcomes.read_bytes()

    rc = CO.cmd_accept_cost({"id": "JTBD-1-signin", "solution": "SOL-B"})

    assert rc != 0
    assert program.outcomes.read_bytes() == before
    out = capsys.readouterr()
    assert "discarded" in (out.out + out.err).lower()
