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
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
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


def test_a_first_choose_records_the_field_and_new_value(program):
    assert CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"}) == 0
    (entry,) = _chooses(program)
    assert entry["action"] == "choose"  # stays queryable by action
    assert entry["field"] == "chosen-solution"
    assert entry["new"] == "SOL-A"
    assert entry["note"] == "chose SOL-A"


def test_a_first_choose_emits_no_empty_old(program):
    """`make_transition` omits None, so nothing replaced means no `old:` noise —
    the reporter was explicit that first-time choose stays unaffected."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    (entry,) = _chooses(program)
    assert "old" not in entry


def test_a_re_choose_records_what_it_replaced(program):
    """THE GAP: two `choose` entries each naming only their own target meant the
    previous choice could only be inferred from ordering."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    first, second = _chooses(program)
    assert second["old"] == "SOL-A"
    assert second["new"] == "SOL-B"
    # and the entry is self-contained: readable without consulting the first
    assert first["new"] == "SOL-A"


def test_the_action_is_not_downgraded_to_update_field(program):
    """Explicitly requested: keep `choose`. The named action is what makes the
    decision queryable; old/new is what makes it auditable."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-A"})
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    assert [t["action"] for t in _transitions(program)] == ["choose", "choose"]


def test_the_chosen_solution_still_lands(program):
    """The behaviour cofounder-agent confirmed as correct must not shift."""
    CO.cmd_choose({"id": "JTBD-1-signin", "solution": "SOL-B"})
    doc = yaml.safe_load(program.read_text(encoding="utf-8"))
    assert doc["outcomes"][0]["chosen-solution"] == "SOL-B"
