"""Auto-triage scorer (Appendix G, task 7.1).

Pure-Python deterministic ranking — no LLM, no network. Used after
``otaman solution add`` to surface a recommendation to the CEO/CPO.

Formula (Appendix G.1):
    Stage 1: score = impact_weight[outcome.impact] / solution.effort_days
    Stage 2: priority_rank (P0=4 ... P3=1) tiebreaker — higher wins
    Stage 3: lower SOL-<seq> wins (deterministic; final tiebreaker)

Edge cases (Appendix G.3):
    - outcome.impact is None         → triage skipped; recommended = None
    - solution.effort_days is None   → solution excluded from ranking
    - All solutions Discarded         → triage skipped; recommended = None
    - Single non-Discarded solution   → recommended (score still computed)
    - Tie after both stages           → lowest SOL-<seq> wins
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Priority rank — higher value wins ties at stage 2.
PRIORITY_RANK: dict[str, int] = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}

# Default impact weights (Appendix G.4); overridable via platform.yaml.
DEFAULT_IMPACT_WEIGHTS: dict[str, float] = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}

_SOL_SEQ_RE = re.compile(r"^SOL-(\d+)-")


@dataclass(frozen=True)
class TriageResult:
    """One ranked solution from triage."""

    solution_id: str
    score: float
    impact_weight: float
    effort_days: float
    priority_rank: int
    sol_seq: int  # for deterministic tie-break (Appendix G.3)


def _sol_seq(solution_id: str) -> int:
    """Extract the numeric sequence from ``SOL-N-slug``. Returns 999999 for
    malformed ids so they sort last (validators reject these at write time)."""
    m = _SOL_SEQ_RE.match(solution_id or "")
    return int(m.group(1)) if m else 999999


def compute_triage_score(
    outcome: dict[str, Any],
    solution: dict[str, Any],
    impact_weights: dict[str, float] | None = None,
) -> float | None:
    """Compute the value-rate score for a single (outcome, solution) pair.

    Returns ``None`` when the score is undefined per Appendix G.3:
        - outcome.impact is None / unknown tier
        - solution.effort_days is None / non-positive
    """
    weights = impact_weights if impact_weights is not None else DEFAULT_IMPACT_WEIGHTS

    impact = outcome.get("impact")
    if impact is None:
        return None
    if impact not in weights:
        return None  # unknown impact tier — skip rather than guess
    impact_w = weights[impact]

    effort = solution.get("effort-days")
    if effort is None:
        return None
    try:
        effort_f = float(effort)
    except (TypeError, ValueError):
        return None
    if effort_f <= 0:
        return None

    return impact_w / effort_f


def rank_solutions(
    outcome: dict[str, Any],
    solutions: list[dict[str, Any]],
    impact_weights: dict[str, float] | None = None,
) -> list[TriageResult]:
    """Return solutions ranked best-first per Appendix G.

    Discarded solutions are excluded. Solutions without an effort-days
    value are also excluded (their score is undefined).

    Sort key (best first):
        1. score DESC (value-rate)
        2. priority_rank DESC (P0 > P1 > P2 > P3)
        3. sol_seq ASC (deterministic — lower seq wins)
    """
    weights = impact_weights if impact_weights is not None else DEFAULT_IMPACT_WEIGHTS
    priority = outcome.get("priority", "P3")
    p_rank = PRIORITY_RANK.get(priority, 0)

    results: list[TriageResult] = []
    for s in solutions:
        if s.get("status") == "Discarded":
            continue
        score = compute_triage_score(outcome, s, weights)
        if score is None:
            continue
        results.append(
            TriageResult(
                solution_id=s["id"],
                score=score,
                impact_weight=weights[outcome["impact"]],
                effort_days=float(s["effort-days"]),
                priority_rank=p_rank,
                sol_seq=_sol_seq(s["id"]),
            )
        )

    # Sort: higher score first, higher priority_rank first, lower seq first
    # Use 4-decimal rounding for score tie detection per Appendix G.1.
    results.sort(key=lambda r: (-round(r.score, 4), -r.priority_rank, r.sol_seq))
    return results


def recommend(
    outcome: dict[str, Any],
    solutions: list[dict[str, Any]],
    impact_weights: dict[str, float] | None = None,
) -> TriageResult | None:
    """Return the recommended solution per Appendix G, or None if undefined.

    Cases returning None:
        - outcome.impact is None (triage skipped)
        - All sibling solutions are Discarded or have no effort-days
    """
    if outcome.get("impact") is None:
        return None
    ranked = rank_solutions(outcome, solutions, impact_weights)
    return ranked[0] if ranked else None


def build_rationale(
    chosen: TriageResult,
    alternatives: list[TriageResult],
    outcome: dict[str, Any],
) -> str:
    """Build a one-line human-readable rationale for the recommendation."""
    impact = outcome.get("impact", "?")
    rationale = (
        f"impact={impact}({chosen.impact_weight:g}) "
        f"/ effort={chosen.effort_days:g}d = {chosen.score:.2f}"
    )
    if not alternatives:
        rationale += "; only viable candidate"
    elif len(alternatives) == 1:
        alt = alternatives[0]
        rationale += f"; beats {alt.solution_id} ({alt.score:.2f})"
    else:
        rationale += f"; top of {len(alternatives) + 1} candidates"
    return rationale


__all__ = [
    "PRIORITY_RANK",
    "DEFAULT_IMPACT_WEIGHTS",
    "TriageResult",
    "compute_triage_score",
    "rank_solutions",
    "recommend",
    "build_rationale",
]
