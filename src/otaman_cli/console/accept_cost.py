"""Console accept-cost action (team-mode 2.4a, Roman fast-track).

An outcome awaiting cost-acceptance gets a one-key ACCEPT-COST action in the
console tree, offered where the CEO/founder hat is the derived next actor. The
write goes through the EXISTING CLI verb (`otaman outcome accept-cost`) via
run_verb — one write path, one canon transition. The hat check is advisory at
Mode 1 (warn, never block); fail-closed arrives with 2.4b.
"""

from __future__ import annotations

import os

from otaman_cli.console.bus import Program
from otaman_cli.console.registry_detail import _find, _load_raw

#: The hats that own cost-acceptance (Appendix E — the CEO/founder decision).
ACCEPT_COST_HATS = ("ceo", "founder")


def accept_cost_candidate(program: Program, outcome_id: str) -> tuple[str | None, str]:
    """(solution_id, note) — the solution whose cost this outcome would accept,
    or (None, reason) when there's no single clear candidate.

    Offerable when the outcome is NOT already cost-accepted and has one clear
    solution: its ``chosen-solution`` if set, else its single estimated linked
    solution. Zero or several estimated solutions → not offerable (choose at the
    CLI first)."""
    data = _load_raw(program, "outcomes")
    if data is None:
        return None, "outcomes registry unavailable"
    o = _find(data.get("outcomes") or [], outcome_id)
    if not o:
        return None, "outcome not found"
    if o.get("cost-accepted") is True:
        return None, "cost already accepted"
    chosen = o.get("chosen-solution")
    if chosen:
        return chosen, f"accept cost for chosen solution {chosen}"
    sols = _load_raw(program, "solutions") or {}
    linked = [
        s
        for s in (sols.get("solutions") or [])
        if isinstance(s, dict) and s.get("outcome-id") == outcome_id
    ]
    estimated = [s for s in linked if s.get("effort-days") is not None]
    if len(estimated) == 1:
        return estimated[0].get("id"), f"accept cost for {estimated[0].get('id')}"
    if not estimated:
        return None, "no estimated solution to accept-cost yet"
    return None, (
        f"{len(estimated)} estimated solutions — open a solution node and press a to "
        "accept its cost"
    )


def is_accept_cost_offerable(program: Program, outcome_id: str) -> bool:
    """True when this outcome is awaiting cost-acceptance with one clear
    candidate — i.e. the CEO/founder is the derived next actor."""
    return accept_cost_candidate(program, outcome_id)[0] is not None


def solution_accept_candidate(program: Program, solution_id: str) -> tuple[str | None, str]:
    """(outcome_id, note) — the outcome whose cost this SOLUTION node would accept,
    or (None, reason). team-mode 2.4a follow-up (Roman live-blocked): pressing `a`
    on a solution node means "accept the cost OF THIS SOLUTION" — which is exactly
    the verb's `--solution` model, so a multi-candidate outcome needs no choose
    step. Offerable when the solution is estimated, not Discarded, and its outcome
    is not already cost-accepted."""
    sols = _load_raw(program, "solutions") or {}
    s = _find(sols.get("solutions") or [], solution_id)
    if not s:
        return None, "solution not found"
    if str(s.get("status", "")).lower() == "discarded":
        return None, "solution is discarded"
    if s.get("effort-days") is None:
        return None, "solution has no estimate yet"
    outcome_id = s.get("outcome-id")
    if not outcome_id:
        return None, "solution has no linked outcome"
    data = _load_raw(program, "outcomes")
    o = _find((data or {}).get("outcomes") or [], outcome_id) if data else None
    if o is not None and o.get("cost-accepted") is True:
        return None, "outcome cost already accepted"
    return outcome_id, f"accept cost of this solution for {outcome_id}"


def acting_hat_holds(program: Program, hats=ACCEPT_COST_HATS) -> tuple[bool, str | None]:
    """(holds, operator) — whether the resolved roster human holds any of *hats*.
    Advisory only (Mode 1): the caller warns when False but still proceeds."""
    try:
        from otaman_core.human_roster import load_human_roster, resolve_roster_human

        roster = load_human_roster(program.root / "platform.yaml")
        entry = resolve_roster_human(roster, os.environ.get("OTAMAN_HUMAN"))
        if entry is None:
            return False, None
        roles = {r.lower() for r in (getattr(entry, "roles", None) or [])}
        return (bool(roles & {h.lower() for h in hats}), getattr(entry, "name", None))
    except Exception:  # noqa: BLE001 - roster unavailable → advisory False, never crash
        return False, None


def run_accept_cost(program: Program, outcome_id: str, solution_id: str, *, runner=None):
    """Run `otaman outcome accept-cost <id> --solution <sol>` in the program root
    (the one canon write path). Returns a VerbResult (ok/output)."""
    from otaman_cli.console.setup import run_verb

    return run_verb(
        program,
        ["outcome", "accept-cost", outcome_id, "--solution", solution_id],
        runner=runner,
    )


__all__ = [
    "ACCEPT_COST_HATS",
    "accept_cost_candidate",
    "is_accept_cost_offerable",
    "acting_hat_holds",
    "run_accept_cost",
]
