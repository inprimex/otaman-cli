"""Textual-free detail renderers for outcome/solution tree nodes (cofounder
addendum): enter opens the full `otaman <kind> show` content, in-console."""

from __future__ import annotations

import pytest

from otaman_cli.console import bus
from otaman_cli.console.registry_detail import (
    node_detail_text,
    outcome_detail_text,
    solution_detail_text,
)


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    strat = tmp_path / "strat"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    (strat / "outcomes.yaml").write_text(
        "outcomes:\n"
        "  - id: JTBD-1-account\n"
        "    status: Approved\n"
        "    priority: P1\n"
        "    impact: M\n"
        "    chosen-solution: SOL-1-a\n"
        "    product-notes: |\n"
        "      keep it simple\n"
        "    statement:\n"
        "      as-a: user\n"
        "      i-want-to: sign in\n"
        "      incremental-outcome: access my data\n"
        "      so-i-can: work\n"
        "      ultimate-outcome: retention\n"
        "    transitions:\n"
        "      - {at: '2026-09-01', by: cpo-agent, action: create, to: Drafting}\n"
        "      - {at: '2026-09-02', by: human, action: promote, from: Drafting, to: Approved}\n",
        encoding="utf-8",
    )
    (strat / "solutions.yaml").write_text(
        "solutions:\n"
        "  - id: SOL-1-a\n"
        "    outcome-id: JTBD-1-account\n"
        "    status: Considering\n"
        "    t-shirt: Small\n"
        "    effort-days: 3\n"
        "    description: build the login form\n"
        "    pros: [fast]\n"
        "    cons: [minimal]\n"
        "    cto-notes: reuse the auth lib\n",
        encoding="utf-8",
    )
    return bus.Program(name="demo", root=root)


def test_outcome_detail_has_statement_and_fields(program):
    text = outcome_detail_text(program, "JTBD-1-account")
    assert "Outcome: JTBD-1-account" in text
    assert "Status:" in text and "Approved" in text
    assert "Priority:" in text and "P1" in text
    assert "Impact:" in text and "M" in text
    assert "Chosen-solution:" in text and "SOL-1-a" in text
    # full JTBD statement
    assert "sign in" in text and "access my data" in text and "retention" in text
    assert "keep it simple" in text  # product-notes
    assert "Transitions (recent)" in text and "promote" in text  # transitions tail


def test_solution_detail_has_description_and_meta(program):
    text = solution_detail_text(program, "SOL-1-a")
    assert "Solution: SOL-1-a" in text
    assert "Outcome:" in text and "JTBD-1-account" in text
    assert "T-shirt:" in text and "Small" in text
    assert "Effort-days:" in text and "3" in text
    assert "build the login form" in text
    assert "fast" in text and "minimal" in text  # pros/cons
    assert "reuse the auth lib" in text  # cto-notes


def test_outcome_not_found(program):
    assert "not found" in outcome_detail_text(program, "JTBD-nope").lower()


def test_node_detail_text_dispatch(program):
    assert node_detail_text(program, "outcome", "JTBD-1-account").startswith("Outcome:")
    assert node_detail_text(program, "solution", "SOL-1-a").startswith("Solution:")
    assert node_detail_text(program, "change", "whatever") is None


def test_registry_unavailable_is_graceful(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_STRATEGY_DIR", raising=False)
    root = tmp_path / "prog"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    prog = bus.Program(name="demo", root=root)
    assert "unavailable" in outcome_detail_text(prog, "JTBD-1-account")


# ---------------------------------------------------------------------------
# team-mode 2.4b part 3 — role-scoped surface visibility


def test_outcome_costing_scope_hides_value(program):
    text = outcome_detail_text(program, "JTBD-1-account", scope="costing")
    assert "role view: costing" in text
    assert "Chosen-solution:" in text and "Cost-accepted:" in text  # costing shown
    assert "Persona:" not in text and "Product notes" not in text  # value hidden
    assert "sign in" in text  # core JTBD statement always shown


def test_outcome_value_scope_hides_costing(program):
    text = outcome_detail_text(program, "JTBD-1-account", scope="value")
    assert "role view: value" in text
    assert "Chosen-solution:" not in text and "Cost-accepted:" not in text
    assert "keep it simple" in text  # product notes (value) shown


def test_solution_costing_scope_shows_costing_hides_proscons(program):
    text = solution_detail_text(program, "SOL-1-a", scope="costing")
    assert "T-shirt:" in text and "Effort-days:" in text and "CTO notes" in text
    # section headers gone (note: "Cons" is a substring of the "Considering" status)
    assert "\n  Pros" not in text and "\n  Cons" not in text


def test_solution_value_scope_hides_costing(program):
    text = solution_detail_text(program, "SOL-1-a", scope="value")
    assert "T-shirt:" not in text and "CTO notes" not in text
    assert "fast" in text and "minimal" in text  # pros/cons shown


def test_no_scope_shows_everything(program):
    text = outcome_detail_text(program, "JTBD-1-account")
    assert "Persona:" in text and "Chosen-solution:" in text and "role view" not in text


def test_role_scope_founder_sees_all(program, monkeypatch):
    from otaman_cli.console.registry_detail import role_scope

    # the fixture's platform.yaml has no roster; add one with a founder
    (program.root / "platform.yaml").write_text(
        (program.root / "platform.yaml").read_text(encoding="utf-8")
        + "human-roster:\n  - {name: roman, roles: [founder]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert role_scope(program) is None  # founder-mode: all keys


def test_role_scope_cto_is_costing(program, monkeypatch):
    from otaman_cli.console.registry_detail import role_scope

    (program.root / "platform.yaml").write_text(
        (program.root / "platform.yaml").read_text(encoding="utf-8")
        + "human-roster:\n  - {name: tina, roles: [cto]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "tina")
    assert role_scope(program) == "costing"


def test_role_scope_unverified_shows_all(program, monkeypatch):
    from otaman_cli.console.registry_detail import role_scope

    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    assert role_scope(program) is None
