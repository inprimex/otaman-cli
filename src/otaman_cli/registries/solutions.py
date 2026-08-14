"""Solution registry model + loader.

Implements the schema in Appendix B of
`outcome-and-solution-registries/design.md`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SOLUTION_ID_RE = re.compile(r"^SOL-\d+-[a-z0-9-]+$")


class SolutionStatus(str, Enum):
    CONSIDERING = "Considering"
    IN_PROGRESS = "In-Progress"
    COMPLETE = "Complete"
    DISCARDED = "Discarded"


class DependencyKind(str, Enum):
    OUTCOME = "outcome"
    SOLUTION = "solution"
    EXTERNAL = "external"


# Valid transition.action values for solutions (Appendix B.6).
SolutionTransitionAction = Literal[
    "create",
    "propose",
    "select",
    "promote-to-complete",
    "discard",
    "update-field",
]


class Dependency(BaseModel):
    """Typed dependency entry (Appendix B.4)."""

    model_config = ConfigDict(extra="forbid")

    kind: DependencyKind
    ref: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _ref_or_name_per_kind(self) -> Dependency:
        # Internal kinds (outcome, solution) require `ref`; external requires `name`
        if self.kind in (DependencyKind.OUTCOME, DependencyKind.SOLUTION):
            if not self.ref:
                raise ValueError(f"dependency kind={self.kind.value} requires `ref`")
            if self.name is not None:
                raise ValueError(
                    f"dependency kind={self.kind.value} must not set `name`; use `ref`"
                )
        elif self.kind == DependencyKind.EXTERNAL:
            if not self.name:
                raise ValueError("dependency kind=external requires `name`")
            if self.ref is not None:
                raise ValueError("dependency kind=external must not set `ref`; use `name`")
        return self


class SolutionTransition(BaseModel):
    """Audit-trail entry. Same shape as outcome transition (Appendix A.5)."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    by: str
    action: SolutionTransitionAction
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    field: str | None = None
    old: Any | None = None
    new: Any | None = None
    note: str | None = None


class Solution(BaseModel):
    """A single solution entry in `solutions.yaml`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    outcome_id: str = Field(alias="outcome-id")
    release: str | None = None
    description: str
    t_shirt: str | None = Field(default=None, alias="t-shirt")
    effort_days: float | None = Field(default=None, alias="effort-days")
    dependencies: list[Dependency] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    cto_notes: str = Field(default="", alias="cto-notes")

    @field_validator("cto_notes", mode="before")
    @classmethod
    def _cto_notes_null_to_empty(cls, v: Any) -> str:
        return "" if v is None else v

    status: SolutionStatus = SolutionStatus.CONSIDERING
    created: date
    updated: date
    transitions: list[SolutionTransition] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _SOLUTION_ID_RE.match(v):
            raise ValueError(f"solution id must match ^SOL-\\d+-[a-z0-9-]+$; got {v!r}")
        return v

    @field_validator("description")
    @classmethod
    def _description_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must be a non-empty string")
        return v

    @field_validator("effort_days")
    @classmethod
    def _effort_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"effort-days must be > 0; got {v}")
        return v

    @model_validator(mode="after")
    def _no_self_reference(self) -> Solution:
        # Appendix B.7 rule 7: solution cannot list itself in dependencies
        for d in self.dependencies:
            if d.kind == DependencyKind.SOLUTION and d.ref == self.id:
                raise ValueError(f"solution {self.id}: self-reference in dependencies is forbidden")
        return self


class SolutionRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solutions: list[Solution] = Field(default_factory=list)

    @field_validator("solutions")
    @classmethod
    def _ids_unique(cls, v: list[Solution]) -> list[Solution]:
        seen: set[str] = set()
        for s in v:
            if s.id in seen:
                raise ValueError(f"duplicate solution id: {s.id}")
            seen.add(s.id)
        return v

    @model_validator(mode="after")
    def _at_most_one_chosen_per_outcome(self) -> SolutionRegistry:
        """Appendix B.7 rule 8: only one solution per outcome may be In-Progress or Complete."""
        by_outcome: dict[str, list[str]] = {}
        for s in self.solutions:
            if s.status in (SolutionStatus.IN_PROGRESS, SolutionStatus.COMPLETE):
                by_outcome.setdefault(s.outcome_id, []).append(s.id)
        for outcome_id, ids in by_outcome.items():
            if len(ids) > 1:
                raise ValueError(
                    f"outcome {outcome_id}: multiple solutions are In-Progress/Complete "
                    f"simultaneously ({', '.join(ids)})"
                )
        return self

    def get(self, solution_id: str) -> Solution | None:
        for s in self.solutions:
            if s.id == solution_id:
                return s
        return None

    def for_outcome(self, outcome_id: str) -> list[Solution]:
        return [s for s in self.solutions if s.outcome_id == outcome_id]


def load_solutions(path: Path) -> SolutionRegistry:
    """Load and validate `solutions.yaml`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SolutionRegistry.model_validate(raw)
