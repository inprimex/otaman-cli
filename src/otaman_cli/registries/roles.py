"""Operating-actor + role resolution + advisory authorization (Appendix E).

v1 is **advisory-only** — unauthorized operations log a warning to stderr
but proceed. Mode 2+ will replace ``proceed anyway`` with ``exit 1``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

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
    # Solution lifecycle authz moved to the acting human's roster HAT
    # (team-mode Phase A 1.2 — hat_advisory); the per-verb solution.* role rows
    # were removed. "roles are hats, repos are homes."
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
TRANSITION_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "chosen-solution",
        "cost-accepted",
        "estimate-requested",
        "created",
        "id",
        "transitions",
    }
)


def resolve_operating_actor(cwd: Path | None = None) -> str:
    """Resolve the "who is acting now" identity (Appendix E.2).

    Delegates to the ONE canonical resolver, ``identity.resolve_agent_identity``
    (identity-divergence D1) — so ``OTAMAN_AGENT`` is cross-checked against the
    cwd-resolved repo owner (a leaked/stale env can't impersonate another agent)
    rather than trusted raw here. ``"human"`` remains the Mode-1 fallback when no
    agent identity resolves.
    """
    from otaman_cli.identity import find_project_root, resolve_agent_identity

    cwd = cwd or Path.cwd()
    root = find_project_root(cwd)
    return resolve_agent_identity(root, cwd=cwd) or "human"


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


def hat_advisory(
    operation: str,
    required_hats: Iterable[str],
    root: Path,
    *,
    stderr=sys.stderr,
) -> bool:
    """Advisory authorization by the acting human's roster HAT (team-mode 1.2).

    Replaces the per-verb role table for solution verbs: authorization derives
    from the acting human's ``human-roster`` role (resolved from ``OTAMAN_HUMAN``)
    — "roles are hats". Advisory in Mode 1 (WARN on a missing hat and proceed,
    like :func:`authz_advisory`); Mode 2+ flips to fail-closed. Always True.
    """
    required = tuple(required_hats)
    if not required:
        return True
    hats: set[str] = set()
    who = os.environ.get("OTAMAN_HUMAN", "").strip() or "unresolved"
    try:
        from otaman_core.human_roster import load_human_roster, resolve_roster_human

        roster = load_human_roster(root / "platform.yaml")
        entry = resolve_roster_human(roster, os.environ.get("OTAMAN_HUMAN"))
        hats = set(entry.roles) if entry else set()
    except Exception:  # noqa: BLE001 - advisory: an absent/broken roster just WARNs
        hats = set()
    if hats & set(required):
        return True
    print(
        f"WARN: operation '{operation}' expects hat {list(required)}; "
        f"acting human '{who}' has {sorted(hats) or 'no roster hat'}",
        file=stderr,
    )
    return True  # Mode 1: proceed anyway


# ---------------------------------------------------------------------------
# The two hats of a funding decision, and the mode that follows from them
# (registry-access-contract D2)


#: Who may CHOOSE a solution — technical judgment. `founder` stands in, the same
#: way core's `resolve_spec_approver` lets the default approver stand in when no cto
#: exists (D5/Q8); the console has used this pair since team-mode 2.4b.
CHOOSE_HATS: tuple[str, ...] = ("cto", "founder")

#: Who may FUND it — budget authority. The console's accept-cost key has used this
#: pair since team-mode 2.4a.
FUND_HATS: tuple[str, ...] = ("ceo", "founder")


def acting_hats(root: Path) -> tuple[frozenset[str], str | None]:
    """``(hats, name)`` for the human `OTAMAN_HUMAN` resolves to, lowercased.

    `(frozenset(), None)` when nothing resolves — an absent or unreadable roster
    yields no hats rather than an exception, because every caller here is either
    advisory or refuses on its own terms.
    """
    try:
        from otaman_core.human_roster import load_human_roster, resolve_roster_human

        entry = resolve_roster_human(
            load_human_roster(root / "platform.yaml"), os.environ.get("OTAMAN_HUMAN")
        )
    except Exception:  # noqa: BLE001 - no roster → no hats, never a crash
        return frozenset(), None
    if entry is None:
        return frozenset(), None
    roles = frozenset(str(r).lower() for r in (getattr(entry, "roles", None) or []))
    return roles, getattr(entry, "name", None)


def held_hat(hats: frozenset[str], required: Iterable[str]) -> str | None:
    """The FIRST of *required* the acting human actually holds, or None.

    Returns which hat, not merely whether one was held: core's approval record
    carries `via: hat` but not WHICH, and D2 requires the log to show both hats on a
    founder-mode choose+fund. Ordered by *required* so the specific hat wins over the
    `founder` stand-in — a log saying `hat: cto` for someone who holds cto is more
    useful than one saying `founder`.
    """
    for hat in required:
        if hat.lower() in hats:
            return hat
    return None


def operating_mode(root: Path) -> str:
    """``"founder"`` when ONE human holds both hats, else ``"team"`` (D2).

    Not a config switch, deliberately: the mode is a property of the acting human's
    hats. One person holding both the choose and the fund hat IS founder-mode, and
    that is precisely what makes the combined keystroke safe to offer them — there is
    no second party whose decision it would collapse. A program whose hats are split
    across people is team-mode for the same reason, with no setting to get wrong.
    """
    hats, _ = acting_hats(root)
    both = held_hat(hats, CHOOSE_HATS) and held_hat(hats, FUND_HATS)
    return "founder" if both else "team"


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
    "hat_advisory",
    "CHOOSE_HATS",
    "FUND_HATS",
    "acting_hats",
    "held_hat",
    "operating_mode",
    "is_transition_only_field",
]
