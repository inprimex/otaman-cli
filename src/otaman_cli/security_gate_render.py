"""security-gates-hook-c 1.7, cli half — Hook C's report, read at review.

Task 1.7 is one line with two owners: plugin's resolution function EMITS the
report (plugin #98), this renders it in the review flow. Core owns the record
itself (#93) — the dataclasses, the parse, and `is_blocked`.

Four positions, each of which the alternative gets wrong:

**The verdict comes from core's `is_blocked`, never from reading the layer
list.** Core states that rule as the single home precisely so the emitter and
the renderer cannot disagree, and plugin deleted their own copy of it for the
same reason. A second derivation here would recreate the drift both deletions
were for. This module never asks "did any layer fail" — it asks core.

**A payload that STATES a verdict is checked against the derivation, and a
mismatch is loud.** `report_to_dict` writes a derived `blocked` for readers that
do not recompute; `report_from_dict` ignores it. So an emitter that computed
blocking itself and got it wrong produces a payload whose stated verdict
disagrees with the truth, and nothing anywhere notices. Here it is named. This
is the one check that earns its place by catching the failure mode the
single-home rule exists to prevent.

**A layer that did not report is named, not omitted.** A report carrying one
passing layer must not read like a clean five-layer run. Absent is its own
verdict and it is rendered — the same position as 1.2's opt-out rendering, for
the same reason: a layer missing from the output is indistinguishable from a
layer that passed.

**Unreadable is not clean.** No payload, bad JSON, a shape core refuses, or a
bundle too old to carry the record — each one means the gate was not evaluated,
and each returns `CANNOT_RENDER` rather than a reassuring empty render. The
fail-open class, seven instances deep in this fleet, starts exactly here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Exit codes. A renderer that printed a blocking report and exited 0 would be
#: a silent success; one that exited 0 because it could not read the report
#: would be worse. `BLOCKED` is not this module's policy — it is core's
#: `is_blocked` reported faithfully.
CLEAR = 0
BLOCKED = 1
CANNOT_RENDER = 2

#: Rendered for a layer the report says nothing about. Deliberately distinct
#: from core's `not-run` — "the gate ran this and it did not execute" and "the
#: report does not mention it" are different facts about different things.
NOT_REPORTED = "not-reported"


@dataclass
class Loaded:
    """A parse attempt: the report, or why there is nothing to render.

    `stated_blocked` is the payload's own `blocked` field when it carried one —
    kept separately from the report so :func:`verdict_drift` can compare it
    against core's derivation, which is the whole point of keeping it.
    """

    report: Any | None = None
    error: str | None = None
    stated_blocked: bool | None = None
    unknown_fields: tuple[str, ...] = field(default_factory=tuple)


def _core() -> Any | None:
    """core's security-gate-report module, or None on a bundle that predates it."""
    try:
        from otaman_core import security_gate_report
    except Exception:  # noqa: BLE001 - absent → cannot render, never "clean"
        return None
    needed = ("report_from_dict", "is_blocked", "SecurityGateReport")
    return security_gate_report if all(hasattr(security_gate_report, n) for n in needed) else None


def _layers() -> tuple[str, ...]:
    """The canonical ladder order, from core; empty if this bundle has none."""
    try:
        from otaman_core.security_gates import LAYERS
    except Exception:  # noqa: BLE001 - render what the report has, in its own order
        return ()
    return tuple(LAYERS)


def load(raw: str) -> Loaded:
    """Parse a security-gate-report payload — plugin's `report_body`, serialized.

    Every failure path produces an `error`, never an empty report: the caller
    must not be able to mistake "could not read it" for "it found nothing".
    """
    core = _core()
    if core is None:
        return Loaded(
            error=(
                "otaman-core does not carry the security-gate-report record "
                "(security-gates-hook-c 1.6) — cannot render; update the bundle"
            )
        )
    text = raw.strip()
    if not text:
        return Loaded(error="empty payload — nothing was emitted, so nothing was checked")
    try:
        data = json.loads(text)
    except ValueError as exc:
        return Loaded(error=f"payload is not JSON: {exc}")
    if not isinstance(data, dict):
        return Loaded(error="payload is not a security-gate-report object")
    stated = data.get("blocked")
    try:
        report = core.report_from_dict(data)
    except Exception as exc:  # noqa: BLE001 - core raises its own error type
        return Loaded(error=f"not a valid security-gate-report: {exc}")
    return Loaded(
        report=report,
        stated_blocked=stated if isinstance(stated, bool) else None,
    )


def is_blocked(report: Any) -> bool:
    """Whether *report* blocks — core's derivation, asked rather than reimplemented."""
    core = _core()
    if core is None:  # pragma: no cover - load() refuses before any caller gets here
        raise RuntimeError("security-gate-report record unavailable")
    return bool(core.is_blocked(report))


def verdict_drift(loaded: Loaded) -> str | None:
    """The payload's stated verdict vs core's, when they disagree.

    A payload whose `blocked` was computed by its emitter rather than taken from
    `report_to_dict` can state the opposite of the truth, and `report_from_dict`
    discards it without a word. Naming the mismatch is the only place in the
    chain where that is visible.
    """
    if loaded.report is None or loaded.stated_blocked is None:
        return None
    derived = is_blocked(loaded.report)
    if derived == loaded.stated_blocked:
        return None
    return (
        f"payload states blocked={str(loaded.stated_blocked).lower()}, "
        f"core derives blocked={str(derived).lower()} — the emitter computed its own "
        "verdict and it disagrees with otaman_core.security_gate_report.is_blocked; "
        "core's stands"
    )


def _layer_lines(report: Any) -> list[str]:
    reported = {la.layer: la for la in report.layers}
    order = [la for la in _layers() if la in reported]
    order += [la for la in reported if la not in order]
    missing = [la for la in _layers() if la not in reported]
    lines = ["layers:"]
    for name in order:
        verdict = reported[name]
        lines.append(f"  - {name}: {verdict.verdict}")
        for finding in verdict.findings:
            lines.append(f"      {finding}")
    for name in missing:
        lines.append(f"  - {name}: {NOT_REPORTED}")
    if not order:
        lines.append("  (no layer reported a verdict — the ladder did not run)")
    return lines


def _suppression_lines(report: Any) -> list[str]:
    if not report.suppressions:
        return ["suppressions: none in the diff"]
    lines = ["suppressions:"]
    for sup in report.suppressions:
        if sup.justified:
            why = f" — {sup.justification}" if sup.justification else " — justified"
            lines.append(f"  - {sup.marker} at {sup.location}{why}")
        else:
            lines.append(f"  - {sup.marker} at {sup.location} — UNJUSTIFIED (fails the gate)")
    return lines


def _disagreement_lines(report: Any) -> list[str]:
    if not report.disagreements:
        return []
    lines = ["disagreements (human triage — the deterministic verdict stands):"]
    for dis in report.disagreements:
        where = f" [{dis.layer}]" if dis.layer else ""
        lines.append(f"  - {dis.finding}{where}")
        lines.append(f"      deterministic: {dis.deterministic}   observer: {dis.observer}")
    return lines


def render_lines(loaded: Loaded) -> list[str]:
    """How Hook C's result reads at review.

    The verdict line comes first and states where it came from, because a
    reviewer acting on "BLOCKED" is entitled to know that no surface in the
    chain decided it independently.
    """
    if loaded.report is None:
        return [f"security gate: NOT EVALUATED — {loaded.error}"]
    report = loaded.report
    blocked = is_blocked(report)
    where = f"{report.repo}{' ' + report.pr if report.pr else ''}"
    lines = [
        f"security gate: {'BLOCKED' if blocked else 'clear'}  ({where})",
        "  verdict from otaman_core.security_gate_report.is_blocked",
    ]
    drift = verdict_drift(loaded)
    if drift:
        lines.append(f"  !! {drift}")
    lines.append("")
    lines.extend(_layer_lines(report))
    dis = _disagreement_lines(report)
    if dis:
        lines.append("")
        lines.extend(dis)
    lines.append("")
    lines.extend(_suppression_lines(report))
    if blocked:
        reasons = [la.layer for la in report.layers if la.verdict == "fail"]
        unjustified = len(report.unjustified_suppressions)
        why = []
        if reasons:
            why.append("failing layer(s): " + ", ".join(reasons))
        if unjustified:
            why.append(f"{unjustified} unjustified suppression(s)")
        lines.append("")
        lines.append("blocked on " + "; ".join(why))
    return lines


def exit_code(loaded: Loaded) -> int:
    """`CLEAR` / `BLOCKED` / `CANNOT_RENDER` — never 0 for a gate that did not run."""
    if loaded.report is None:
        return CANNOT_RENDER
    return BLOCKED if is_blocked(loaded.report) else CLEAR


__all__ = [
    "BLOCKED",
    "CANNOT_RENDER",
    "CLEAR",
    "NOT_REPORTED",
    "Loaded",
    "exit_code",
    "is_blocked",
    "load",
    "render_lines",
    "verdict_drift",
]
