"""Persona registry model + loader.

Implements the schema in Appendix C of
`outcome-and-solution-registries/design.md`.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_ID_RE = re.compile(r"^persona-[a-z0-9-]+$")


class PersonaKind(str, Enum):
    END_USER = "end-user"
    ADMIN = "admin"
    TEAM_MEMBER = "team-member"
    INTERNAL_STAKEHOLDER = "internal-stakeholder"
    EXTERNAL_STAKEHOLDER = "external-stakeholder"
    SYSTEM = "system"


class Persona(BaseModel):
    """A single persona entry from `personas.yaml`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    name: str
    description: str
    kind: PersonaKind
    domain_prefill_source: str | None = Field(default=None, alias="domain-prefill-source")
    # Soft-delete marker added by `otaman persona retire` (task 4.1 spec note).
    status: Literal["active", "retired"] = "active"

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"persona id must match ^persona-[a-z0-9-]+$; got {v!r}")
        return v

    @field_validator("name", "description")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v


class PersonaRegistry(BaseModel):
    """Top-level container for `personas.yaml`."""

    model_config = ConfigDict(extra="forbid")

    personas: list[Persona] = Field(default_factory=list)

    @field_validator("personas")
    @classmethod
    def _ids_unique(cls, v: list[Persona]) -> list[Persona]:
        seen: set[str] = set()
        for p in v:
            if p.id in seen:
                raise ValueError(f"duplicate persona id: {p.id}")
            seen.add(p.id)
        return v

    def get(self, persona_id: str) -> Persona | None:
        for p in self.personas:
            if p.id == persona_id:
                return p
        return None


def load_personas(path: Path) -> PersonaRegistry:
    """Load and validate `personas.yaml`. Returns a `PersonaRegistry`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PersonaRegistry.model_validate(raw)
