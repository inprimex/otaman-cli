"""platform.yaml extensions for the outcome/solution/persona registries.

Implements the schema in Appendix D of
`outcome-and-solution-registries/design.md`.

Only the `program.*` keys defined in Appendix D are modelled here; the rest
of platform.yaml stays in its existing loader and is opaque to this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from otaman_cli.registries.outcomes import (
    Impact,
    OutcomeRegistry,
    load_outcomes,
)
from otaman_cli.registries.personas import PersonaRegistry, load_personas
from otaman_cli.registries.solutions import (
    DependencyKind,
    SolutionRegistry,
    SolutionStatus,
    load_solutions,
)


DEFAULT_IMPACT_WEIGHTS: dict[str, float] = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}
DEFAULT_T_SHIRT_SCALE: dict[str, float] = {
    "Tiny": 1,
    "X-Small": 2,
    "Small": 3,
    "Small-Medium": 5,
    "Medium": 10,
    "Large": 15,
    "X-Large": 30,
}


class TriageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    impact_weights: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_IMPACT_WEIGHTS),
        alias="impact-weights",
    )

    @field_validator("impact_weights")
    @classmethod
    def _weights_positive(cls, v: dict[str, float]) -> dict[str, float]:
        valid_keys = {i.value for i in Impact}
        for k, w in v.items():
            if k not in valid_keys:
                raise ValueError(
                    f"impact-weights key {k!r} must be one of {sorted(valid_keys)}"
                )
            if w <= 0:
                raise ValueError(f"impact-weights[{k!r}] must be > 0; got {w}")
        return v


class OutcomesProcess(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    path: str = "outcomes.yaml"
    triage: TriageConfig = Field(default_factory=TriageConfig)


class SolutionsProcess(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    path: str = "solutions.yaml"


class PersonasProcess(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    path: str = "personas.yaml"


class ProgramProcesses(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    outcomes: OutcomesProcess = Field(default_factory=OutcomesProcess)
    solutions: SolutionsProcess = Field(default_factory=SolutionsProcess)
    personas: PersonasProcess = Field(default_factory=PersonasProcess)


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""


class ProgramExtensions(BaseModel):
    """The `program:` block under platform.yaml. Extra keys allowed."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    processes: ProgramProcesses = Field(default_factory=ProgramProcesses)
    roles: list[Role] = Field(default_factory=list)
    role_assignments: dict[str, str] = Field(default_factory=dict, alias="role-assignments")
    releases: list[Release] = Field(default_factory=list)
    t_shirt_scale: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_T_SHIRT_SCALE),
        alias="t-shirt-scale",
    )

    @field_validator("releases")
    @classmethod
    def _release_ids_unique(cls, v: list[Release]) -> list[Release]:
        seen: set[str] = set()
        for r in v:
            if r.id in seen:
                raise ValueError(f"duplicate release id: {r.id}")
            seen.add(r.id)
        return v

    @field_validator("t_shirt_scale")
    @classmethod
    def _scale_positive(cls, v: dict[str, float]) -> dict[str, float]:
        for k, n in v.items():
            if not k:
                raise ValueError("t-shirt-scale keys must be non-empty")
            if n <= 0:
                raise ValueError(f"t-shirt-scale[{k!r}] must be > 0; got {n}")
        return v

    @model_validator(mode="after")
    def _role_assignments_match_known_roles(self) -> ProgramExtensions:
        """Appendix D.9 rule 1: role-assignments keys should match role.id values."""
        # Warning only — does not raise. We surface mismatches via a separate
        # validation pass in the CLI layer. (Validation rule says "emit warning",
        # not error.) Pydantic models don't have a side-channel here, so we just
        # leave assertion to the loader/CLI command. No-op here.
        return self


def load_program_extensions(platform_yaml_path: Path) -> ProgramExtensions:
    """Load `program:` block from a platform.yaml file.

    Returns a default-populated `ProgramExtensions` if `program:` is absent
    (defaults match Appendix D.2 / D.8 conventions).
    """
    raw = yaml.safe_load(platform_yaml_path.read_text(encoding="utf-8")) or {}
    program_block: dict[str, Any] = raw.get("program") or {}
    return ProgramExtensions.model_validate(program_block)


# -----------------------------------------------------------------------------
# Cross-file FK validation (used by `pytest` regression suite + CLI loader)


class CrossRefIssue(BaseModel):
    """A single broken cross-reference, surfaced by `validate_cross_refs`."""

    kind: str  # "missing-persona" | "missing-release" | "missing-outcome" |
               # "missing-solution" | "outcome-id-mismatch" |
               # "missing-tshirt-key" | "missing-chosen-solution" | ...
    file: str  # "outcomes.yaml" | "solutions.yaml"
    entity_id: str
    detail: str


def validate_cross_refs(
    outcomes: OutcomeRegistry,
    solutions: SolutionRegistry,
    personas: PersonaRegistry,
    platform: ProgramExtensions,
) -> list[CrossRefIssue]:
    """Cross-file FK resolution per Appendix A.6 + B.7 rules.

    Returns a list of issues; empty list means everything resolves cleanly.
    """
    issues: list[CrossRefIssue] = []

    persona_ids = {p.id for p in personas.personas}
    outcome_ids = {o.id for o in outcomes.outcomes}
    solution_ids = {s.id for s in solutions.solutions}
    release_ids = {r.id for r in platform.releases}
    t_shirt_keys = set(platform.t_shirt_scale.keys())

    # -- outcomes.yaml cross-refs --
    for o in outcomes.outcomes:
        if o.persona is not None and o.persona not in persona_ids:
            issues.append(CrossRefIssue(
                kind="missing-persona",
                file="outcomes.yaml",
                entity_id=o.id,
                detail=f"persona '{o.persona}' not found in personas.yaml",
            ))
        if o.chosen_solution is not None and o.chosen_solution not in solution_ids:
            issues.append(CrossRefIssue(
                kind="missing-chosen-solution",
                file="outcomes.yaml",
                entity_id=o.id,
                detail=f"chosen-solution '{o.chosen_solution}' not found in solutions.yaml",
            ))
        if o.release is not None and release_ids and o.release not in release_ids:
            issues.append(CrossRefIssue(
                kind="missing-release",
                file="outcomes.yaml",
                entity_id=o.id,
                detail=f"release '{o.release}' not declared in platform.yaml program.releases",
            ))

    # Rule A.6.7: chosen-solution must point to a solution whose outcome-id == this.id
    for o in outcomes.outcomes:
        if o.chosen_solution and o.chosen_solution in solution_ids:
            sol = solutions.get(o.chosen_solution)
            if sol is not None and sol.outcome_id != o.id:
                issues.append(CrossRefIssue(
                    kind="outcome-id-mismatch",
                    file="solutions.yaml",
                    entity_id=sol.id,
                    detail=(
                        f"solution outcome-id={sol.outcome_id!r} doesn't match outcome "
                        f"{o.id} that selected it as chosen-solution"
                    ),
                ))

    # -- solutions.yaml cross-refs --
    for s in solutions.solutions:
        if s.outcome_id not in outcome_ids:
            issues.append(CrossRefIssue(
                kind="missing-outcome",
                file="solutions.yaml",
                entity_id=s.id,
                detail=f"outcome-id '{s.outcome_id}' not found in outcomes.yaml",
            ))
        if s.release is not None and release_ids and s.release not in release_ids:
            issues.append(CrossRefIssue(
                kind="missing-release",
                file="solutions.yaml",
                entity_id=s.id,
                detail=f"release '{s.release}' not declared in platform.yaml program.releases",
            ))
        if s.t_shirt is not None and t_shirt_keys and s.t_shirt not in t_shirt_keys:
            issues.append(CrossRefIssue(
                kind="missing-tshirt-key",
                file="solutions.yaml",
                entity_id=s.id,
                detail=(
                    f"t-shirt '{s.t_shirt}' not in platform.yaml program.t-shirt-scale "
                    f"({sorted(t_shirt_keys)})"
                ),
            ))
        # Dependency ref resolution
        for d in s.dependencies:
            if d.kind == DependencyKind.OUTCOME and d.ref and d.ref not in outcome_ids:
                issues.append(CrossRefIssue(
                    kind="missing-outcome",
                    file="solutions.yaml",
                    entity_id=s.id,
                    detail=f"dependency ref '{d.ref}' (kind=outcome) not in outcomes.yaml",
                ))
            elif d.kind == DependencyKind.SOLUTION and d.ref and d.ref not in solution_ids:
                issues.append(CrossRefIssue(
                    kind="missing-solution",
                    file="solutions.yaml",
                    entity_id=s.id,
                    detail=f"dependency ref '{d.ref}' (kind=solution) not in solutions.yaml",
                ))

    # B.7 rule 9: Solution.Complete requires the parent outcome's chosen-solution == this.id
    for s in solutions.solutions:
        if s.status == SolutionStatus.COMPLETE:
            parent = outcomes.get(s.outcome_id)
            if parent is None:
                continue  # already flagged above as missing-outcome
            if parent.chosen_solution != s.id:
                issues.append(CrossRefIssue(
                    kind="complete-without-chosen-solution",
                    file="solutions.yaml",
                    entity_id=s.id,
                    detail=(
                        f"solution status=Complete but parent outcome {parent.id}.chosen-solution"
                        f"={parent.chosen_solution!r} (expected {s.id!r})"
                    ),
                ))

    return issues


__all__ = [
    "DEFAULT_IMPACT_WEIGHTS",
    "DEFAULT_T_SHIRT_SCALE",
    "TriageConfig",
    "OutcomesProcess",
    "SolutionsProcess",
    "PersonasProcess",
    "ProgramProcesses",
    "Role",
    "Release",
    "ProgramExtensions",
    "load_program_extensions",
    "CrossRefIssue",
    "validate_cross_refs",
    "load_outcomes",
    "load_solutions",
    "load_personas",
]
