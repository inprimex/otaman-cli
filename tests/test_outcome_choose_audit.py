"""`choose` records what it replaced (cofounder-agent 20260919T232356).

The transition carried only `note: chose <SOL-id>`. Re-choosing therefore lost
history: an outcome chosen as SOL-A and later re-chosen as SOL-B left two
`choose` entries each naming only their own target, so a reader could not tell
what the previous choice was except by inferring it from ordering.

The action STAYS `choose` — that is what makes the decision queryable. The
old/new pair is what makes it auditable. The reporter wanted both, and was
explicit that it should not become an `update-field`.
"""

from __future__ import annotations

import pytest
import yaml

import otaman_cli.registries.cli_outcome as CO


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    strat = tmp_path / "strategy"
    root.mkdir()
    strat.mkdir()
    # A human-roster entry + OTAMAN_HUMAN: `choose` is an APPROVAL_REQUIRED_ACTION as of
    # rac 1.2, so the write carries an approval naming a resolved roster human and core
    # refuses without one. The fixture supplies the authority the verb now attests to.
    (root / "platform.yaml").write_text(
        "project: d\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - name: roman\n    roles: [cto, cofounder, approver]\n",
        encoding="utf-8",
    )
    # Schema-valid: the writer VALIDATES before saving, so a minimal stub is
    # refused (id pattern, statement, created are all required).
    (strat / "outcomes.yaml").write_text(
        yaml.dump(
            {
                "outcomes": [
                    {
                        "id": "JTBD-1-signin",
                        "status": "Approved",
                        "created": "2026-09-01",
                        "statement": {
                            "as-a": "an operator",
                            "i-want-to": "sign in",
                            "incremental-outcome": "fewer lockouts",
                            "so-i-can": "get to work",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (strat / "solutions.yaml").write_text(
        yaml.dump(
            {
                "solutions": [
                    {"id": "SOL-A", "outcome-id": "JTBD-1-signin", "status": "Considering"},
                    {"id": "SOL-B", "outcome-id": "JTBD-1-signin", "status": "Considering"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr(CO, "find_project_root", lambda: root)
    monkeypatch.setattr(CO, "_ctx", lambda r: ("roman", ["cto"], None))
    monkeypatch.setattr(CO, "hat_advisory", lambda *a, **k: None)
    monkeypatch.setattr(CO, "_emit_bus", lambda *a, **k: None)
    return strat / "outcomes.yaml"


def _transitions(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc["outcomes"][0].get("transitions") or []


def _chooses(path):
    return [t for t in _transitions(path) if t.get("action") == "choose"]


def _change(entry, field="chosen-solution"):
    """The field audit, whichever shape the contract wrote it in.

    Shape-agnostic ON PURPOSE. core's A.5 pin replaces the flat `field`/`old`/`new`
    trio with a `changes` list (one entry per changed field), and these tests are about
    the DECISION RECORD, not about which of the two shapes carries it — a test that
    pinned the shape would go red on a sibling's merge and say nothing about the
    property the reporter asked for.
    """
    from otaman_cli.registries.transitions import changed_field

    return changed_field(entry, field) or {}


def test_a_first_choose_records_the_field_and_new_value(program):
    assert CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0
    (entry,) = _chooses(program)
    assert entry["action"] == "choose"  # stays queryable by action
    assert _change(entry)["new"] == "SOL-A"
    # The hat rides the note as of rac 1.2: core's approval carries `via: hat` but not
    # WHICH hat, and the delta's founder-mode scenario requires the log to show it.
    assert "chose SOL-A" in entry["note"]
    assert "hat: cto" in entry["note"]


def test_a_first_choose_records_no_MISLEADING_old(program):
    """Nothing was replaced, so nothing may claim to have been.

    The reporter's ask was that a first-time choose stays unaffected, and cli's
    `make_transition` delivered it by omitting a None `old`. The contract's
    `apply_transition` (rac 1.1) sets `old` unconditionally, so the key can now be
    present with a null value. That is noise rather than a wrong claim — reported to
    core as a nit — so this asserts what the ask was actually about: no PREVIOUS
    solution is attributed to a first choice.
    """
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    (entry,) = _chooses(program)
    change = _change(entry)
    assert change.get("old") is None, "a first choice replaced nothing"
    assert change["new"] == "SOL-A"


def test_a_re_choose_records_what_it_replaced(program):
    """THE GAP: two `choose` entries each naming only their own target meant the
    previous choice could only be inferred from ordering."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    first, second = _chooses(program)
    assert _change(second)["old"] == "SOL-A"
    assert _change(second)["new"] == "SOL-B"
    # and the entry is self-contained: readable without consulting the first
    assert _change(first)["new"] == "SOL-A"


def test_the_action_is_not_downgraded_to_update_field(program):
    """Explicitly requested: keep `choose`. The named action is what makes the
    decision queryable; old/new is what makes it auditable.

    Asserted on the CHOOSE entries rather than on the whole list, which is what the ask
    was about. A bookkeeping `update-field` row carrying `updated` MAY follow a choose
    (it does on a core whose contract records only a single field's triple, and does
    not on one that records a `changes` list) — either way an `update-field` row may
    only ever carry bookkeeping, never a decision, which is what this pins.
    """
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    assert [t["action"] for t in _chooses(program)] == ["choose", "choose"]
    assert "update-field" not in [t["action"] for t in _chooses(program)]
    bookkeeping = [t for t in _transitions(program) if t["action"] == "update-field"]
    from otaman_cli.registries.transitions import changes_of

    for row in bookkeeping:
        fields = {c.get("field") for c in changes_of(row)}
        assert fields <= {"updated"}, (
            f"an update-field row may only ever carry bookkeeping, never a decision: {fields}"
        )


def test_the_chosen_solution_still_lands(program):
    """The behaviour cofounder-agent confirmed as correct must not shift."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    doc = yaml.safe_load(program.read_text(encoding="utf-8"))
    assert doc["outcomes"][0]["chosen-solution"] == "SOL-B"
