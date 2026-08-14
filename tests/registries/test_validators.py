"""Unit tests for registry validators (independent of taskflow fixtures).

Covers invariants in Appendix A.6, B.7, C.4, D.9:
- ID regex rejection
- Required-field checks
- Status machine legality (outcomes)
- Self-reference rejection (solutions)
- At-most-one-in-progress-per-outcome (solutions)
- Typed-only dependency shape
- platform.yaml extension validation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from otaman_cli.registries.outcomes import (
    Outcome,
    OutcomeRegistry,
    OutcomeStatus,
    demote_target,
    promote_target,
)
from otaman_cli.registries.personas import Persona, PersonaRegistry
from otaman_cli.registries.platform_ext import (
    DEFAULT_IMPACT_WEIGHTS,
    ProgramExtensions,
    TriageConfig,
    validate_cross_refs,
)
from otaman_cli.registries.solutions import (
    Dependency,
    Solution,
    SolutionRegistry,
)

# ---------------------------------------------------------------------------
# Helpers


def _statement() -> dict:
    return {
        "as-a": "new user",
        "i-want-to": "do a thing",
        "incremental-outcome": "thing done",
        "so-i-can": "achieve outcome",
    }


def _make_outcome(**overrides):
    base = {
        "id": "JTBD-1-create-account",
        "statement": _statement(),
        "status": "Drafting",
        "priority": "P2",
        "estimate-requested": False,
        "cost-accepted": None,
        "created": "2026-04-01",
        "updated": "2026-04-01",
        "transitions": [
            {
                "at": "2026-04-01T09:00:00Z",
                "by": "cpo-agent",
                "action": "create",
                "to": "Drafting",
            }
        ],
    }
    base.update(overrides)
    return base


def _make_solution(**overrides):
    base = {
        "id": "SOL-1-foo",
        "outcome-id": "JTBD-1-create-account",
        "description": "do the thing",
        "status": "Considering",
        "created": "2026-04-01",
        "updated": "2026-04-01",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Personas


def test_persona_id_must_match_pattern():
    with pytest.raises(ValidationError):
        Persona.model_validate(
            {
                "id": "BadID",
                "name": "X",
                "description": "Y",
                "kind": "end-user",
            }
        )


def test_persona_kind_enum_rejects_unknown():
    with pytest.raises(ValidationError):
        Persona.model_validate(
            {
                "id": "persona-foo",
                "name": "X",
                "description": "Y",
                "kind": "robot",
            }
        )


def test_persona_registry_rejects_duplicate_ids():
    p1 = {"id": "persona-x", "name": "X", "description": "Y", "kind": "end-user"}
    with pytest.raises(ValidationError):
        PersonaRegistry.model_validate({"personas": [p1, p1]})


def test_persona_required_fields():
    with pytest.raises(ValidationError):
        Persona.model_validate(
            {"id": "persona-x", "name": "", "description": "Y", "kind": "end-user"}
        )


# ---------------------------------------------------------------------------
# Outcomes


def test_outcome_id_must_match_pattern():
    with pytest.raises(ValidationError):
        Outcome.model_validate(_make_outcome(id="bad-id"))


def test_outcome_statement_requires_four_fields():
    bad = _make_outcome()
    bad["statement"] = {"as-a": "", "i-want-to": "x", "incremental-outcome": "x", "so-i-can": "x"}
    with pytest.raises(ValidationError):
        Outcome.model_validate(bad)


def test_outcome_cost_accepted_requires_chosen_solution():
    bad = _make_outcome(**{"cost-accepted": True})
    with pytest.raises(ValidationError, match="cost-accepted=true requires chosen-solution"):
        Outcome.model_validate(bad)


def test_outcome_promote_state_machine():
    assert promote_target(OutcomeStatus.DRAFTING) == OutcomeStatus.BACKLOG
    assert promote_target(OutcomeStatus.BACKLOG) == OutcomeStatus.APPROVED
    assert promote_target(OutcomeStatus.APPROVED) == OutcomeStatus.IN_PROGRESS
    assert promote_target(OutcomeStatus.IN_PROGRESS) == OutcomeStatus.DONE
    assert promote_target(OutcomeStatus.DONE) is None
    assert promote_target(OutcomeStatus.RETIRED) is None


def test_outcome_demote_state_machine():
    assert demote_target(OutcomeStatus.IN_PROGRESS) == OutcomeStatus.APPROVED
    assert demote_target(OutcomeStatus.APPROVED) == OutcomeStatus.BACKLOG
    assert demote_target(OutcomeStatus.BACKLOG) == OutcomeStatus.DRAFTING
    assert demote_target(OutcomeStatus.DRAFTING) is None


def test_outcome_transitions_must_be_legal_promote():
    """A promote from Drafting must target Backlog, not Approved."""
    bad = _make_outcome(status="Backlog")
    bad["transitions"] = [
        {"at": "2026-04-01T09:00:00Z", "by": "cpo-agent", "action": "create", "to": "Drafting"},
        {
            "at": "2026-04-01T10:00:00Z",
            "by": "cpo-agent",
            "action": "promote",
            "from": "Drafting",
            "to": "Approved",
        },  # illegal — skips Backlog
    ]
    with pytest.raises(ValidationError, match="illegal promote"):
        Outcome.model_validate(bad)


def test_outcome_transitions_final_state_must_match_status():
    """Transitions end on Drafting but outcome.status says Backlog → mismatch."""
    bad = _make_outcome(status="Backlog")  # transitions only have create→Drafting
    with pytest.raises(ValidationError, match="doesn't match the final state"):
        Outcome.model_validate(bad)


def test_outcome_registry_rejects_duplicate_ids():
    o = _make_outcome()
    with pytest.raises(ValidationError, match="duplicate outcome id"):
        OutcomeRegistry.model_validate({"outcomes": [o, o]})


# ---------------------------------------------------------------------------
# Solutions


def test_solution_id_must_match_pattern():
    with pytest.raises(ValidationError):
        Solution.model_validate(_make_solution(id="bad-id"))


def test_solution_no_self_reference():
    bad = _make_solution(dependencies=[{"kind": "solution", "ref": "SOL-1-foo"}])
    with pytest.raises(ValidationError, match="self-reference in dependencies"):
        Solution.model_validate(bad)


def test_solution_effort_days_must_be_positive():
    with pytest.raises(ValidationError, match=r"effort-days must be > 0"):
        Solution.model_validate(_make_solution(**{"effort-days": 0}))


def test_dependency_external_requires_name():
    with pytest.raises(ValidationError, match="external requires `name`"):
        Dependency.model_validate({"kind": "external"})


def test_dependency_internal_requires_ref():
    with pytest.raises(ValidationError, match=r"requires `ref`"):
        Dependency.model_validate({"kind": "solution"})


def test_dependency_external_must_not_set_ref():
    with pytest.raises(ValidationError, match="must not set `ref`"):
        Dependency.model_validate({"kind": "external", "name": "X", "ref": "Y"})


def test_solution_registry_rejects_two_in_progress_per_outcome():
    s1 = _make_solution(id="SOL-1-a", status="In-Progress")
    s2 = _make_solution(id="SOL-2-b", status="In-Progress")
    with pytest.raises(ValidationError, match="multiple solutions are In-Progress"):
        SolutionRegistry.model_validate({"solutions": [s1, s2]})


def test_solution_registry_rejects_duplicate_ids():
    s = _make_solution()
    with pytest.raises(ValidationError, match="duplicate solution id"):
        SolutionRegistry.model_validate({"solutions": [s, s]})


# ---------------------------------------------------------------------------
# platform.yaml extensions (Appendix D)


def test_triage_default_impact_weights():
    cfg = TriageConfig()
    assert cfg.enabled is True
    assert cfg.impact_weights == DEFAULT_IMPACT_WEIGHTS


def test_triage_rejects_invalid_impact_key():
    with pytest.raises(ValidationError, match=r"impact-weights key"):
        TriageConfig.model_validate({"impact-weights": {"ZZ": 1}})


def test_triage_rejects_non_positive_weight():
    with pytest.raises(ValidationError, match=r"must be > 0"):
        TriageConfig.model_validate({"impact-weights": {"M": -1}})


def test_program_extensions_rejects_duplicate_release_ids():
    with pytest.raises(ValidationError, match="duplicate release id"):
        ProgramExtensions.model_validate(
            {
                "releases": [{"id": "X", "description": ""}, {"id": "X", "description": ""}],
            }
        )


def test_t_shirt_scale_must_be_positive():
    with pytest.raises(ValidationError, match=r"t-shirt-scale.*must be > 0"):
        ProgramExtensions.model_validate({"t-shirt-scale": {"Tiny": 0}})


# ---------------------------------------------------------------------------
# Cross-validator


def test_cross_validator_flags_missing_persona():
    outcomes = OutcomeRegistry.model_validate(
        {"outcomes": [_make_outcome(persona="persona-missing")]}
    )
    issues = validate_cross_refs(
        outcomes,
        SolutionRegistry(),
        PersonaRegistry(),
        ProgramExtensions(),
    )
    kinds = [i.kind for i in issues]
    assert "missing-persona" in kinds


def test_cross_validator_flags_missing_chosen_solution():
    o = _make_outcome(**{"chosen-solution": "SOL-99-ghost", "cost-accepted": True})
    outcomes = OutcomeRegistry.model_validate({"outcomes": [o]})
    issues = validate_cross_refs(
        outcomes,
        SolutionRegistry(),
        PersonaRegistry(),
        ProgramExtensions(),
    )
    assert any(i.kind == "missing-chosen-solution" for i in issues)


def test_cross_validator_flags_missing_outcome_for_solution():
    s = _make_solution(**{"outcome-id": "JTBD-99-ghost"})
    solutions = SolutionRegistry.model_validate({"solutions": [s]})
    issues = validate_cross_refs(
        OutcomeRegistry(),
        solutions,
        PersonaRegistry(),
        ProgramExtensions(),
    )
    assert any(i.kind == "missing-outcome" for i in issues)


def test_cross_validator_clean_when_everything_resolves():
    o = _make_outcome()
    s = _make_solution()
    issues = validate_cross_refs(
        OutcomeRegistry.model_validate({"outcomes": [o]}),
        SolutionRegistry.model_validate({"solutions": [s]}),
        PersonaRegistry(),
        ProgramExtensions(),
    )
    assert issues == []
