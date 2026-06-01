"""Outcome registry model + loader.

Implements the schema in Appendix A of
`outcome-and-solution-registries/design.md`.

Cross-file FK validation (e.g. `chosen-solution` -> solutions.yaml,
`persona` -> personas.yaml, `release` -> platform.yaml) is performed by
the higher-level cross-validator in this package; per-field validators
in this module check shape and intra-file invariants only.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_OUTCOME_ID_RE = re.compile(r"^JTBD-\d+-[a-z0-9-]+$")


class OutcomeStatus(str, Enum):
    DRAFTING = "Drafting"
    BACKLOG = "Backlog"
    APPROVED = "Approved"
    IN_PROGRESS = "In-Progress"
    DONE = "Done"
    RETIRED = "Retired"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Impact(str, Enum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


# Status transitions allowed by `promote` / `demote` per Appendix A.4.
_PROMOTE_FORWARD: dict[OutcomeStatus, OutcomeStatus] = {
    OutcomeStatus.DRAFTING: OutcomeStatus.BACKLOG,
    OutcomeStatus.BACKLOG: OutcomeStatus.APPROVED,
    OutcomeStatus.APPROVED: OutcomeStatus.IN_PROGRESS,
    OutcomeStatus.IN_PROGRESS: OutcomeStatus.DONE,
}
_DEMOTE_BACKWARD: dict[OutcomeStatus, OutcomeStatus] = {
    OutcomeStatus.IN_PROGRESS: OutcomeStatus.APPROVED,
    OutcomeStatus.APPROVED: OutcomeStatus.BACKLOG,
    OutcomeStatus.BACKLOG: OutcomeStatus.DRAFTING,
}


def promote_target(current: OutcomeStatus) -> OutcomeStatus | None:
    """Return the next forward state for `promote`, or None if terminal."""
    return _PROMOTE_FORWARD.get(current)


def demote_target(current: OutcomeStatus) -> OutcomeStatus | None:
    """Return the previous backward state for `demote`, or None if at start."""
    return _DEMOTE_BACKWARD.get(current)


# Valid transition.action values for outcomes (Appendix A.5 row 3).
OutcomeTransitionAction = Literal[
    "create",
    "promote",
    "demote",
    "request-estimate",
    "accept-cost",
    "reject-cost",
    "retire",
    "update-field",
]


class Statement(BaseModel):
    """JTBD statement sub-object: 4 required + 1 optional sub-fields (Appendix A.3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    as_a: str = Field(alias="as-a")
    i_want_to: str = Field(alias="i-want-to")
    incremental_outcome: str = Field(alias="incremental-outcome")
    so_i_can: str = Field(alias="so-i-can")
    ultimate_outcome: str = Field(default="", alias="ultimate-outcome")

    @field_validator("as_a", "i_want_to", "incremental_outcome", "so_i_can")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("statement sub-field must be a non-empty string")
        return v


class Transition(BaseModel):
    """Audit-trail entry (Appendix A.5)."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    by: str
    action: OutcomeTransitionAction
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    field: str | None = None
    old: Any | None = None
    new: Any | None = None
    note: str | None = None


class Outcome(BaseModel):
    """A single outcome entry in `outcomes.yaml`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    category: str = ""
    persona: str | None = None
    statement: Statement
    status: OutcomeStatus = OutcomeStatus.DRAFTING
    priority: Priority = Priority.P2
    impact: Impact | None = None
    estimate_requested: bool = Field(default=False, alias="estimate-requested")
    chosen_solution: str | None = Field(default=None, alias="chosen-solution")
    cost_accepted: bool | None = Field(default=None, alias="cost-accepted")
    release: str | None = None
    product_notes: str = Field(default="", alias="product-notes")

    @field_validator("product_notes", mode="before")
    @classmethod
    def _product_notes_null_to_empty(cls, v: Any) -> str:
        return "" if v is None else v
    created: date
    updated: date
    transitions: list[Transition] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _OUTCOME_ID_RE.match(v):
            raise ValueError(f"outcome id must match ^JTBD-\\d+-[a-z0-9-]+$; got {v!r}")
        return v

    @model_validator(mode="after")
    def _cost_accepted_requires_chosen_solution(self) -> Outcome:
        # Appendix A.6 rule 6: cost-accepted=true requires chosen-solution
        if self.cost_accepted is True and not self.chosen_solution:
            raise ValueError(
                f"outcome {self.id}: cost-accepted=true requires chosen-solution to be set"
            )
        return self

    @model_validator(mode="after")
    def _transitions_match_status_machine(self) -> Outcome:
        """Audit the transitions log: status changes must follow Appendix A.4."""
        current: OutcomeStatus | None = None
        for i, t in enumerate(self.transitions):
            if t.action == "create":
                if t.to is None:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] action=create requires `to`"
                    )
                current = OutcomeStatus(t.to)
                continue
            if t.action == "promote":
                if current is None or t.from_ is None or t.to is None:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] action=promote requires from+to"
                    )
                expected = promote_target(OutcomeStatus(t.from_))
                if expected is None or OutcomeStatus(t.to) != expected:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] illegal promote "
                        f"{t.from_}->{t.to}"
                    )
                current = OutcomeStatus(t.to)
            elif t.action == "demote":
                if t.from_ is None or t.to is None:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] action=demote requires from+to"
                    )
                expected = demote_target(OutcomeStatus(t.from_))
                if expected is None or OutcomeStatus(t.to) != expected:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] illegal demote "
                        f"{t.from_}->{t.to}"
                    )
                current = OutcomeStatus(t.to)
            elif t.action == "accept-cost":
                # accept-cost moves Backlog -> Approved (Appendix A.4)
                if t.from_ == OutcomeStatus.BACKLOG.value and t.to == OutcomeStatus.APPROVED.value:
                    current = OutcomeStatus.APPROVED
                # Allowed: accept-cost without status transition (just sets cost-accepted flag)
                elif t.from_ is None and t.to is None:
                    pass
                else:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] action=accept-cost has "
                        f"unexpected from/to: {t.from_!r}->{t.to!r}"
                    )
            elif t.action == "retire":
                if t.to is not None and OutcomeStatus(t.to) != OutcomeStatus.RETIRED:
                    raise ValueError(
                        f"outcome {self.id}: transition[{i}] retire must target Retired"
                    )
                current = OutcomeStatus.RETIRED
            # request-estimate, reject-cost, update-field do not alter status; skip
        # Final status check: terminal state in transitions must match outcome.status
        if current is not None and current != self.status:
            raise ValueError(
                f"outcome {self.id}: status={self.status.value} doesn't match the final "
                f"state from transitions ({current.value})"
            )
        return self


class OutcomeRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcomes: list[Outcome] = Field(default_factory=list)

    @field_validator("outcomes")
    @classmethod
    def _ids_unique(cls, v: list[Outcome]) -> list[Outcome]:
        seen: set[str] = set()
        for o in v:
            if o.id in seen:
                raise ValueError(f"duplicate outcome id: {o.id}")
            seen.add(o.id)
        return v

    def get(self, outcome_id: str) -> Outcome | None:
        for o in self.outcomes:
            if o.id == outcome_id:
                return o
        return None


def load_outcomes(path: Path) -> OutcomeRegistry:
    """Load and validate `outcomes.yaml`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return OutcomeRegistry.model_validate(raw)
