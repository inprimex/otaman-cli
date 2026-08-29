"""Shared approver-eligibility resolution (hitl-default-approver 2.1/2.2).

HITL confirmation (`otaman hitl take`) and console spec approval (`otaman -i`)
resolve the acting human from ``OTAMAN_HUMAN`` and require the roster
``approver`` role through THIS one helper — there is exactly one notion of
"may work with proposals", so the two paths cannot diverge (hitl-confirmation
delta). The eligibility primitives live in ``otaman_core.human_roster``
(hitl-default-approver step 1); this module is the single cli-side adapter both
call paths share.

Three outcomes (matching the spec refusal semantics):
- **unresolved** — ``OTAMAN_HUMAN`` unset or matches no roster entry → keep
  today's unverified/refusal behavior unchanged.
- **refused** — resolves to an entry that lacks ``approver`` → refuse, naming
  the entry and the missing role.
- **approved** — resolves to an entry holding ``approver`` → proceed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Eligibility:
    """The shared verdict for an acting human working with proposals."""

    resolved: bool  # OTAMAN_HUMAN matched a roster entry
    approved: bool  # ... and that entry holds the approver role
    entry_name: str | None  # the matched entry's name (for a named refusal)
    platform_yaml: Path | None = None  # where the roster lives (for the fix hint)

    @property
    def refused(self) -> bool:
        """Resolved to a real human who lacks the approver role."""
        return self.resolved and not self.approved


def resolve_eligibility(platform_yaml: Path, otaman_human: str | None = None) -> Eligibility:
    """Resolve the acting human's approver eligibility against *platform_yaml*.

    *otaman_human* defaults to the ``OTAMAN_HUMAN`` env value. A missing or
    unmatched identity is ``unresolved`` (behavior unchanged); a matched entry
    without ``approver`` is ``refused``; a matched approver is ``approved``.
    Never raises — a missing/broken roster resolves to ``unresolved``.
    """
    who = otaman_human if otaman_human is not None else os.environ.get("OTAMAN_HUMAN", "")
    who = (who or "").strip()
    if not who:
        return Eligibility(False, False, None, platform_yaml)

    from otaman_core.human_roster import is_approver, load_human_roster, resolve_roster_human

    try:
        roster = load_human_roster(platform_yaml)
    except Exception:  # noqa: BLE001 - no/broken roster → unresolved, never crash the caller
        return Eligibility(False, False, None, platform_yaml)

    entry = resolve_roster_human(roster, who)
    if entry is None:
        return Eligibility(False, False, None, platform_yaml)
    return Eligibility(True, is_approver(entry), entry.name, platform_yaml)


def refusal_message(eligibility: Eligibility) -> str:
    """The named, ACTIONABLE refusal for a resolved-but-non-approver identity.

    Names the entry AND the remedy — mgmt-agent's rollout-gap report
    (20260828T201645): the old message named the missing role but not how to
    fix it, so Roman (roster `founder`, no `approver`) hit a fail-closed
    refusal with no next step. The hint points at the exact roster entry + file
    to edit.
    """
    from otaman_core.human_roster import APPROVER_ROLE

    name = eligibility.entry_name or "?"
    where = (
        f"{eligibility.platform_yaml} human-roster"
        if eligibility.platform_yaml is not None
        else "platform.yaml human-roster"
    )
    return (
        f"{name!r} is in the roster but lacks the required '{APPROVER_ROLE}' role — "
        f"add '{APPROVER_ROLE}' to `roles` for {name!r} in {where}"
    )


__all__ = ["Eligibility", "refusal_message", "resolve_eligibility"]
