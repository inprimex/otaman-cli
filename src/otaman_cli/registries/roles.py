"""Operating-actor + role resolution + advisory authorization (Appendix E).

v1 is **advisory-only** — unauthorized operations log a warning to stderr
but proceed. Mode 2+ will replace ``proceed anyway`` with ``exit 1``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from otaman_cli.identity import _read_otaman_agent_field
from otaman_cli.registries.platform_ext import ProgramExtensions


# Operation → required role(s) table (Appendix E.4).
OPERATION_ROLES: dict[str, tuple[str, ...]] = {
    # Outcome lifecycle
    "outcome.add": ("cpo",),
    "outcome.promote": ("cpo",),
    "outcome.demote": ("cpo",),
    "outcome.request-estimate": ("cpo",),
    "outcome.accept-cost": ("ceo",),
    "outcome.reject-cost": ("ceo",),
    "outcome.retire": ("cpo", "ceo"),
    "outcome.update-field": ("cpo",),
    # Solution lifecycle
    "solution.add": ("cto",),
    "solution.propose": ("cto",),
    "solution.promote-to-complete": ("cto",),
    "solution.discard": ("cto",),
    "solution.update-field": ("cto",),
    # Persona lifecycle
    "persona.add": ("cpo",),
    "persona.retire": ("cpo",),
    # Read-only ops — any actor
    "outcome.list": (),
    "outcome.show": (),
    "outcome.history": (),
    "solution.list": (),
    "solution.show": (),
    "solution.history": (),
    "persona.list": (),
    "persona.show": (),
}


# Fields that may not be edited via a generic ``update-field`` command;
# only their named transition command may change them (Appendix E.5).
TRANSITION_ONLY_FIELDS: frozenset[str] = frozenset({
    "status",
    "chosen-solution",
    "cost-accepted",
    "estimate-requested",
    "created",
    "id",
    "transitions",
})


def resolve_operating_actor(cwd: Path | None = None) -> str:
    """Resolve the "who is acting now" identity (Appendix E.2).

    Chain:
        1. ``OTAMAN_AGENT`` env var
        2. ``.otaman`` ``agent:`` field via CWD ancestry walk
        3. ``"human"`` fallback (Mode 1 dev-mode assumption)
    """
    env_actor = os.environ.get("OTAMAN_AGENT", "").strip()
    if env_actor:
        return env_actor

    walk_actor = _read_otaman_agent_field(cwd or Path.cwd())
    if walk_actor:
        return walk_actor

    return "human"


def resolve_roles(actor: str, platform: ProgramExtensions) -> list[str]:
    """Return all role-ids assigned to *actor* in ``platform.role-assignments``.

    Mode 1 commonly has a single human holding multiple roles; this returns
    them all so authorization checks can consider any one as a match.
    """
    roles: list[str] = []
    for role_id, assigned in platform.role_assignments.items():
        if assigned == actor:
            roles.append(role_id)
    return roles


def required_roles_for(operation: str) -> tuple[str, ...]:
    """Return the required-role tuple for *operation*. Empty tuple = any actor."""
    return OPERATION_ROLES.get(operation, ())


def authz_advisory(
    operation: str,
    actor: str,
    actor_roles: Iterable[str],
    *,
    stderr=sys.stderr,
) -> bool:
    """Check whether *actor* (holding *actor_roles*) may run *operation*.

    Always returns True (v1 advisory-only). When unauthorized, emits a
    warning to *stderr* in the spec-required format (Appendix E.6) and
    proceeds anyway. Caller should call this BEFORE performing the
    mutation so the warning fires before any side-effect.
    """
    required = required_roles_for(operation)
    if not required:
        return True  # any-actor operation

    actor_role_set = set(actor_roles)
    if actor_role_set & set(required):
        return True

    actual_role = next(iter(actor_role_set)) if actor_role_set else "none"
    print(
        f"WARN: operation '{operation}' requires role {list(required)}; "
        f"acting as '{actor}' (role: '{actual_role}')",
        file=stderr,
    )
    return True  # Mode 1: proceed anyway


def is_transition_only_field(field: str) -> bool:
    """Return True if *field* must be mutated via a named transition command,
    not via a generic ``update-field`` command (Appendix E.5).
    """
    return field in TRANSITION_ONLY_FIELDS


__all__ = [
    "OPERATION_ROLES",
    "TRANSITION_ONLY_FIELDS",
    "resolve_operating_actor",
    "resolve_roles",
    "required_roles_for",
    "authz_advisory",
    "is_transition_only_field",
]
