"""spec-direct-disposition 1.1 — doctor's Approved-without-a-disposition lint.

Twenty Approved outcomes were solutioned through spec changes before the
solutions register existed, so "Approved with no linked solution" conflated two
different things and the number was noise. With the canonical
`SOLUTION: spec-direct (<change>)` marker applied, the count becomes a true
signal: no linked solution, no marker, no R1 'solution inline' note.

Verified against this program's live registry, where it yields exactly the 5 the
change predicts (JTBD-111..115, the research-first D-set).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.commands.doctor import (
    _check_solution_disposition,
    _print_solution_disposition_report,
)


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program with the outcomes process enabled and a strategy registry dir."""
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "program": {"processes": {"outcomes": {"enabled": True}}},
            }
        ),
        encoding="utf-8",
    )
    strat = tmp_path / "strategy"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    return root, strat


def _write(strat: Path, outcomes: list[dict], solutions: list[dict] | None = None) -> None:
    (strat / "outcomes.yaml").write_text(yaml.dump({"outcomes": outcomes}), encoding="utf-8")
    if solutions is not None:
        (strat / "solutions.yaml").write_text(yaml.dump({"solutions": solutions}), encoding="utf-8")


def _outcome(oid: str, *, status: str = "Approved", notes: str = "") -> dict:
    o: dict = {"id": oid, "status": status}
    if notes:
        o["product-notes"] = notes
    return o


# ---------------------------------------------------------------------------
# the two spec scenarios


def test_truly_unsolutioned_outcome_is_reported_by_id(program):
    root, strat = program
    _write(strat, [_outcome("JTBD-111-bus-reception")])
    r = _check_solution_disposition(root)
    assert r["applicable"] is True
    assert r["unsolutioned"] == ["JTBD-111-bus-reception"]


def test_spec_direct_marker_clears_the_lint(program):
    root, strat = program
    _write(strat, [_outcome("JTBD-79", notes="SOLUTION: spec-direct (agent-credential-access)")])
    r = _check_solution_disposition(root)
    assert r["unsolutioned"] == []
    assert r["spec_direct"] == 1


def test_marker_with_nested_parens_is_detected(program):
    """The real live format nests parens — detection must not depend on parsing
    the change name out of them."""
    root, strat = program
    _write(
        strat,
        [
            _outcome(
                "JTBD-46",
                notes=(
                    "SOLUTION: spec-direct (hitl-adapters (approved 2026-09-01 batch)) "
                    "- pre-solutions-register lineage"
                ),
            )
        ],
    )
    assert _check_solution_disposition(root)["unsolutioned"] == []


def test_marker_is_case_insensitive_and_position_independent(program):
    root, strat = program
    _write(
        strat,
        [_outcome("JTBD-1", notes="context first\nsolution: SPEC-DIRECT (some-change)\nmore")],
    )
    assert _check_solution_disposition(root)["unsolutioned"] == []


# ---------------------------------------------------------------------------
# the other two dispositions


def test_live_linked_solution_clears_the_lint(program):
    root, strat = program
    _write(
        strat,
        [_outcome("JTBD-36")],
        [{"id": "SOL-1", "outcome-id": "JTBD-36", "status": "Considering"}],
    )
    r = _check_solution_disposition(root)
    assert r["unsolutioned"] == [] and r["linked"] == 1


def test_inline_solution_note_clears_the_lint(program):
    root, strat = program
    _write(strat, [_outcome("JTBD-2", notes="solution inline — described in the statement")])
    r = _check_solution_disposition(root)
    assert r["unsolutioned"] == [] and r["inline"] == 1


def test_discarded_only_solutions_do_NOT_clear_the_lint(program):
    """A linked solution must be a LIVE one. An outcome whose only solutions were
    discarded is genuinely unsolutioned — counting it as solutioned would put the
    noise straight back into the number."""
    root, strat = program
    _write(
        strat,
        [_outcome("JTBD-9")],
        [{"id": "SOL-9", "outcome-id": "JTBD-9", "status": "Discarded"}],
    )
    r = _check_solution_disposition(root)
    assert r["unsolutioned"] == ["JTBD-9"]
    assert r["linked"] == 0


def test_one_live_solution_among_discarded_still_clears(program):
    root, strat = program
    _write(
        strat,
        [_outcome("JTBD-9")],
        [
            {"id": "SOL-9a", "outcome-id": "JTBD-9", "status": "Discarded"},
            {"id": "SOL-9b", "outcome-id": "JTBD-9", "status": "Complete"},
        ],
    )
    assert _check_solution_disposition(root)["unsolutioned"] == []


# ---------------------------------------------------------------------------
# scope: only Approved outcomes


@pytest.mark.parametrize("status", ["Drafting", "Backlog", "In-Progress", "Done", "Retired"])
def test_non_approved_statuses_are_out_of_scope(program, status):
    root, strat = program
    _write(strat, [_outcome("JTBD-x", status=status)])
    r = _check_solution_disposition(root)
    assert r["unsolutioned"] == [] and r["approved"] == 0


def test_mixed_registry_counts_each_disposition(program):
    root, strat = program
    _write(
        strat,
        [
            _outcome("linked-one"),
            _outcome("marked-one", notes="SOLUTION: spec-direct (some-change)"),
            _outcome("inline-one", notes="solution inline"),
            _outcome("bare-one"),
            _outcome("bare-two"),
            _outcome("draft-one", status="Drafting"),
        ],
        [{"id": "SOL-1", "outcome-id": "linked-one", "status": "Considering"}],
    )
    r = _check_solution_disposition(root)
    assert r["approved"] == 5  # the Drafting one is excluded
    assert r["linked"] == 1 and r["spec_direct"] == 1 and r["inline"] == 1
    assert r["unsolutioned"] == ["bare-one", "bare-two"]


# ---------------------------------------------------------------------------
# applicability + tolerance


def test_not_applicable_when_outcomes_process_disabled(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "demo", "version": "1.0", "repos": []}), encoding="utf-8"
    )
    monkeypatch.delenv("OTAMAN_STRATEGY_DIR", raising=False)
    assert _check_solution_disposition(root)["applicable"] is False


def test_not_applicable_when_registry_absent(program):
    root, _ = program  # process enabled but no outcomes.yaml written
    assert _check_solution_disposition(root)["applicable"] is False


def test_unparseable_registry_is_not_applicable_not_a_crash(program):
    root, strat = program
    (strat / "outcomes.yaml").write_text("a: b: c\n", encoding="utf-8")
    assert _check_solution_disposition(root)["applicable"] is False


def test_malformed_entries_are_skipped(program):
    root, strat = program
    (strat / "outcomes.yaml").write_text(
        yaml.dump({"outcomes": ["not-a-dict", {"status": "Approved"}]}), encoding="utf-8"
    )
    r = _check_solution_disposition(root)
    # the dict without an id still counts as approved, named so a human can find it
    assert r["unsolutioned"] == ["(unnamed outcome)"]


def test_missing_solutions_file_is_tolerated(program):
    root, strat = program
    _write(strat, [_outcome("JTBD-1")])  # no solutions.yaml at all
    assert _check_solution_disposition(root)["unsolutioned"] == ["JTBD-1"]


# ---------------------------------------------------------------------------
# the report


def test_report_is_silent_when_not_applicable(capsys):
    _print_solution_disposition_report({"applicable": False})
    assert capsys.readouterr().out == ""


def test_report_shows_ok_and_the_breakdown_when_clean(capsys):
    _print_solution_disposition_report(
        {
            "applicable": True,
            "unsolutioned": [],
            "approved": 3,
            "linked": 2,
            "spec_direct": 1,
            "inline": 0,
        }
    )
    out = capsys.readouterr().out
    assert "OK" in out and "3 approved" in out and "1 spec-direct" in out


def test_report_lists_ids_and_explains_the_expected_members(capsys):
    _print_solution_disposition_report(
        {
            "applicable": True,
            "unsolutioned": ["JTBD-111", "JTBD-112"],
            "approved": 5,
            "linked": 1,
            "spec_direct": 2,
            "inline": 0,
        }
    )
    out = capsys.readouterr().out
    assert "JTBD-111" in out and "JTBD-112" in out
    assert "2 unsolutioned" in out
    assert "spec-direct" in out  # names the marker that would clear it
    assert "research-stage" in out.lower()  # names who legitimately belongs here
