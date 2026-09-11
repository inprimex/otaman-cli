"""Console CHOOSE / DISCARD actions on solution nodes (team-mode 2.4b).

Roman's live tree usage: "no option to confirm/reject any SOLs via treeview
mode." The decision layer adds one-key CTO actions on solution nodes — CHOOSE
(mark this solution the outcome's chosen one) and DISCARD (with a required
reason) — both writing through the EXISTING CLI verbs (`otaman outcome choose`,
`otaman solution discard`): one write path, canon transitions, B1 attribution.
Hat check advisory at Mode 1 (fail-closed lands with the Mode-2 permissions pack,
inert today).
"""

from __future__ import annotations

from otaman_cli.console.accept_cost import acting_hat_holds
from otaman_cli.console.bus import Program
from otaman_cli.console.registry_detail import _find, _load_raw

#: CHOOSE/DISCARD are CTO decisions; founder-mode sees every key (canon).
DECISION_HATS = ("cto", "founder")


def _solution(program: Program, solution_id: str) -> dict | None:
    sols = _load_raw(program, "solutions") or {}
    return _find(sols.get("solutions") or [], solution_id)


def choose_candidate(program: Program, solution_id: str) -> tuple[str | None, str]:
    """(outcome_id, note) — the outcome this solution would become the chosen one
    of, or (None, reason). Offerable when the solution isn't Discarded, has a
    linked outcome, and isn't already that outcome's chosen-solution."""
    s = _solution(program, solution_id)
    if not s:
        return None, "solution not found"
    if str(s.get("status", "")).lower() == "discarded":
        return None, "solution is discarded"
    outcome_id = s.get("outcome-id")
    if not outcome_id:
        return None, "solution has no linked outcome"
    data = _load_raw(program, "outcomes")
    o = _find((data or {}).get("outcomes") or [], outcome_id) if data else None
    if o is not None and o.get("chosen-solution") == solution_id:
        return None, "already the chosen solution"
    return outcome_id, f"choose this solution for {outcome_id}"


def discard_candidate(program: Program, solution_id: str) -> tuple[bool, str]:
    """(offerable, note) — a solution can be discarded unless it's already
    Discarded."""
    s = _solution(program, solution_id)
    if not s:
        return False, "solution not found"
    if str(s.get("status", "")).lower() == "discarded":
        return False, "already discarded"
    return True, "discard this solution (a reason is required)"


def acting_decision_hat(program: Program) -> tuple[bool, str | None]:
    """(holds, operator) for the CHOOSE/DISCARD hats (cto/founder). Advisory."""
    return acting_hat_holds(program, DECISION_HATS)


def run_choose(program: Program, outcome_id: str, solution_id: str, *, runner=None):
    from otaman_cli.console.setup import run_verb

    return run_verb(
        program, ["outcome", "choose", outcome_id, "--solution", solution_id], runner=runner
    )


def run_discard(program: Program, solution_id: str, reason: str, *, runner=None):
    from otaman_cli.console.setup import run_verb

    return run_verb(
        program, ["solution", "discard", solution_id, "--reason", reason], runner=runner
    )


__all__ = [
    "DECISION_HATS",
    "choose_candidate",
    "discard_candidate",
    "acting_decision_hat",
    "run_choose",
    "run_discard",
]
