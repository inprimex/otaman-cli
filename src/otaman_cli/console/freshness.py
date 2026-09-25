"""Runtime-freshness verdicts for the console (session-runtime-freshness 1.3).

The console renders the SAME verdicts as `otaman doctor`, from the same
computation: `otaman_plugin.runtime_freshness.assess()`. There is no second
checker here, deliberately — the whole change exists because a running session
can silently outlive the config it depends on, and two checkers that disagreed
about "stale" would reproduce that defect one level up.

Plugin owns checks 1-4 and the verdict vocabulary; cli owns the rendering.

`not-checked` is a REAL verdict, not an absence. When the checker cannot answer
— plugin too old to carry it, or not installed — this returns exactly that
rather than an empty list, because an empty list reads as "nothing is stale"
and that is the claim we are least entitled to make.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: plugin's vocabulary, mirrored for rendering only. A verdict cli invents
#: would be a second vocabulary; these exist so the renderer can order and
#: colour them without importing the type at module scope.
VERDICT_FRESH = "fresh"
VERDICT_STALE = "stale"
VERDICT_SKEWED = "skewed"
VERDICT_NOT_CHECKED = "not-checked"

#: Worst-first, so the row that should change behaviour is the row on screen.
VERDICT_ORDER = (VERDICT_STALE, VERDICT_SKEWED, VERDICT_NOT_CHECKED, VERDICT_FRESH)

_REQUIRED = ("assess", "Finding")


@dataclass(frozen=True)
class Row:
    """One rendered freshness row — plugin's Finding, flattened for display."""

    verdict: str
    check: str
    subject: str
    reason: str
    remedy: str = ""

    @property
    def rank(self) -> int:
        try:
            return VERDICT_ORDER.index(self.verdict)
        except ValueError:
            return len(VERDICT_ORDER)


def _checker() -> Any | None:
    """plugin's runtime_freshness module, or None when this install predates it."""
    try:
        from otaman_plugin import runtime_freshness as module
    except Exception:  # noqa: BLE001 - absent/broken plugin → not-checked, never silence
        return None
    return module if all(hasattr(module, n) for n in _REQUIRED) else None


def assess_rows(root: Path) -> list[Row]:
    """The freshness rows for *root*, worst verdict first.

    Never raises and never returns a bare empty list for a failure: a checker
    that could not run yields one `not-checked` row saying so, because silence
    and "everything is fresh" must not look identical on a screen whose whole
    job is telling you when they are not.
    """
    module = _checker()
    if module is None:
        return [
            Row(
                verdict=VERDICT_NOT_CHECKED,
                check="runtime-freshness",
                subject="this install",
                reason=(
                    "otaman-plugin does not carry the runtime-freshness checks "
                    "(session-runtime-freshness 1.1/1.2)"
                ),
                remedy="update the bundle — `otaman upgrade` — then reopen this view",
            )
        ]

    try:
        findings = module.assess(root)
    except Exception as exc:  # noqa: BLE001 - a broken check must not blank the view
        return [
            Row(
                verdict=VERDICT_NOT_CHECKED,
                check="runtime-freshness",
                subject="this install",
                reason=f"the freshness check failed: {type(exc).__name__}: {exc}",
                remedy="`otaman doctor` for the full output",
            )
        ]

    rows = [
        Row(
            verdict=str(getattr(f, "verdict", VERDICT_NOT_CHECKED)),
            check=str(getattr(f, "check", "") or "?"),
            subject=str(getattr(f, "subject", "") or "?"),
            reason=str(getattr(f, "reason", "") or ""),
            remedy=str(getattr(f, "remedy", "") or ""),
        )
        for f in findings or []
    ]
    if not rows:
        # assess() returns [] for a non-program root — that is "nothing to be
        # fresh RELATIVE TO", which is not the same as "all fresh".
        return [
            Row(
                verdict=VERDICT_NOT_CHECKED,
                check="runtime-freshness",
                subject=str(root),
                reason=(
                    "no program root here, so there is nothing for a runtime to be fresh against"
                ),
                remedy="",
            )
        ]
    return sorted(rows, key=lambda r: (r.rank, r.check, r.subject))


def summarize(rows: list[Row]) -> str:
    """One line naming what needs attention, or that nothing does."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    parts = [f"{counts[v]} {v}" for v in VERDICT_ORDER if counts.get(v)]
    return " · ".join(parts) if parts else "nothing checked"


__all__ = [
    "VERDICT_FRESH",
    "VERDICT_NOT_CHECKED",
    "VERDICT_ORDER",
    "VERDICT_SKEWED",
    "VERDICT_STALE",
    "Row",
    "assess_rows",
    "summarize",
]
