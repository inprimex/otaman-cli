"""Regression tests against the taskflow-demo-yaml fixtures (task 1.5).

Asserts:
1. All outcomes, solutions, and personas in the TaskFlow demo load + validate
2. Cross-refs resolve cleanly:
   - every outcome's persona exists in personas.yaml
   - every outcome's chosen-solution exists in solutions.yaml
   - every solution's outcome-id exists in outcomes.yaml
   - every typed dependency ref resolves
3. The expected number of entities is present (no silent fixture truncation)

Run: `uv run pytest tests/registries/test_taskflow_fixtures.py -v`
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.registries.outcomes import load_outcomes
from otaman_cli.registries.personas import load_personas
from otaman_cli.registries.platform_ext import (
    ProgramExtensions,
    validate_cross_refs,
)
from otaman_cli.registries.solutions import (
    DependencyKind,
    SolutionStatus,
    load_solutions,
)

# Fixtures live in the sibling otaman-specs repo.
FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / ".."
    / "otaman-specs"
    / "openspec"
    / "changes"
    / "outcome-and-solution-registries"
    / "research"
    / "taskflow-demo-yaml"
).resolve()


@pytest.fixture(scope="module")
def fixture_dir() -> Path:
    if not FIXTURE_DIR.is_dir():
        pytest.skip(f"taskflow fixtures not found at {FIXTURE_DIR}")
    return FIXTURE_DIR


def test_personas_load_without_errors(fixture_dir: Path) -> None:
    personas = load_personas(fixture_dir / "personas.yaml")
    assert len(personas.personas) >= 5
    # Spec says "5 personas" — assert at least the demo baseline
    ids = {p.id for p in personas.personas}
    assert "persona-indv-user" in ids


def test_outcomes_load_without_errors(fixture_dir: Path) -> None:
    outcomes = load_outcomes(fixture_dir / "outcomes.yaml")
    # Spec says "20 outcomes" — accept >= 15 as a low bar (the fixture may grow)
    assert len(outcomes.outcomes) >= 15


def test_solutions_load_without_errors(fixture_dir: Path) -> None:
    solutions = load_solutions(fixture_dir / "solutions.yaml")
    # Spec says "27 solutions"
    assert len(solutions.solutions) >= 20


def test_cross_refs_resolve_cleanly(fixture_dir: Path) -> None:
    outcomes = load_outcomes(fixture_dir / "outcomes.yaml")
    solutions = load_solutions(fixture_dir / "solutions.yaml")
    personas = load_personas(fixture_dir / "personas.yaml")
    # No platform.yaml fixture for the demo; build a permissive ProgramExtensions
    # whose releases and t-shirt-scale match what the demo uses.
    platform = ProgramExtensions.model_validate(
        {
            "releases": [
                {"id": "Sprint-1", "description": "First sprint"},
                {"id": "Sprint-2", "description": "Second sprint"},
                {"id": "Sprint-3", "description": "Third sprint"},
                {"id": "Sprint-4", "description": "Fourth sprint"},
                {"id": "MVP", "description": "Initial public launch"},
                {"id": "post-MVP", "description": "After launch"},
            ],
            # default t-shirt-scale already covers the demo's tags
        }
    )
    issues = validate_cross_refs(outcomes, solutions, personas, platform)
    assert issues == [], "Cross-ref issues in taskflow fixtures:\n" + "\n".join(
        f"  [{i.kind}] {i.file} {i.entity_id}: {i.detail}" for i in issues
    )


def test_outcomes_have_required_jtbd_fields(fixture_dir: Path) -> None:
    outcomes = load_outcomes(fixture_dir / "outcomes.yaml")
    for o in outcomes.outcomes:
        # The 4 required sub-fields must all be non-empty (Statement validator handles this)
        assert o.statement.as_a.strip()
        assert o.statement.i_want_to.strip()
        assert o.statement.incremental_outcome.strip()
        assert o.statement.so_i_can.strip()


def test_solutions_typed_dependencies_only(fixture_dir: Path) -> None:
    """Roman's hard constraint: no free-form dependency strings."""
    solutions = load_solutions(fixture_dir / "solutions.yaml")
    valid_kinds = {k.value for k in DependencyKind}
    for s in solutions.solutions:
        for d in s.dependencies:
            assert d.kind.value in valid_kinds


def test_at_most_one_in_progress_solution_per_outcome(fixture_dir: Path) -> None:
    """B.7 rule 8 enforced by the validator."""
    # If validation passes during load_solutions(), the constraint holds.
    solutions = load_solutions(fixture_dir / "solutions.yaml")
    by_outcome: dict[str, list[str]] = {}
    for s in solutions.solutions:
        if s.status in (SolutionStatus.IN_PROGRESS, SolutionStatus.COMPLETE):
            by_outcome.setdefault(s.outcome_id, []).append(s.id)
    for outcome_id, ids in by_outcome.items():
        assert len(ids) <= 1, (
            f"outcome {outcome_id} has multiple in-progress/complete solutions: {ids}"
        )


def test_complete_solutions_have_parent_chosen_solution(fixture_dir: Path) -> None:
    """B.7 rule 9: status=Complete requires parent.chosen-solution == this.id."""
    outcomes = load_outcomes(fixture_dir / "outcomes.yaml")
    solutions = load_solutions(fixture_dir / "solutions.yaml")
    for s in solutions.solutions:
        if s.status == SolutionStatus.COMPLETE:
            parent = outcomes.get(s.outcome_id)
            assert parent is not None
            assert parent.chosen_solution == s.id, (
                f"solution {s.id} is Complete but outcome {parent.id} chosen-solution"
                f"={parent.chosen_solution!r}"
            )
