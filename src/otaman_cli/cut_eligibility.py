"""release-completeness-gate 1.3 — the pre-cut view deploy consults.

Core owns the verdict (`otaman_core.release_gate.cut_eligibility`, core #107): a change
is cut-eligible when every IMPLEMENTATION task is filed complete — read from the
single-home filed-completes reader, bus filings rather than tasks.md ticks — AND its
verification gate has passed. This renders that verdict in `otaman spec status`. It
computes nothing about eligibility itself; a second opinion about whether a cut may
proceed is the last thing this fleet needs.

Three positions, two of them forced by measurement:

**Implementation tasks and the gate are read from the HEADINGS, not from task numbering.**
Core's verdict takes implementation `task_ids` and a separate `gate_passed`, so the split
has to come from somewhere. `## 2. Verification gate` is what the files actually say;
inferring it from an id starting with `2.` would be a convention no document states.

**A change whose gate state cannot be determined is NOT CHECKED, never eligible and never
gate-unpassed.** Core has three statuses and all three are claims. "I could not tell" is a
fourth, and it is mine to render rather than core's to invent — passing `gate_passed=False`
for an undeterminable gate would print `gate-unpassed`, which asserts a failed check that
never ran.

**The fleet view costs one bus pass with core's batch seam and 68 without it, so it PROBES
for the seam instead of pinning a version.** Measured on this bus: `filed_complete_at` is
377ms per change and `filed_complete_by_change` is 384ms for all 112, so the 68 changes in
`otaman spec status` are 25.7s of re-reading the same directory — on a command that takes
1.87s today. Core's `cut_eligibility` reads the filings itself and has no `filed`
parameter yet (asked for; it is one kwarg on a function that is otherwise a pure
combinator). Until it lands, the fleet-wide render is OPT-IN and says so, because a
budget-truncated listing would show eligibility for whichever handful the clock reached —
an arbitrary subset that changes between runs. `_accepts_filed` means the day core adds
the kwarg this file gets the fast path with no edit here, and the budget below goes back
to being a backstop.
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Seconds the fleet-wide render may spend when it has to fall back to core's per-change
#: bus reads. A backstop, not a plan: the honest path is `batch_seam_present()` being
#: True, and `verdicts_for` COUNTS what the budget cut rather than quietly shortening the
#: list (`spec_sweep.COUNT_BUDGET_SECONDS` is the same dialect).
BUDGET_SECONDS = 60.0

#: Rendered where the gate's state could not be established. Distinct from core's
#: `gate-unpassed`, which means the gate ran and did not pass.
NOT_CHECKED = "not-checked"

#: The heading that opens the verification-gate section. Matched on the words rather than
#: the number, because the number is a convention and the words are the document.
_GATE_HEADING = re.compile(r"^##\s*\d*\.?\s*verification gate", re.IGNORECASE)
_SECTION_HEADING = re.compile(r"^##\s+")


@dataclass
class Verdict:
    """One change's cut-eligibility, as the surface renders it."""

    change: str
    status: str = NOT_CHECKED
    total_tasks: int = 0
    complete_tasks: int = 0
    outstanding: tuple[str, ...] = ()
    gate_passed: bool = False
    reason: str = ""
    #: Tasks (gate included) with no filing of their own, counted complete only because
    #: SOME filing for the change said `--all`. See `_blanket_count`.
    via_all: int = 0

    @property
    def checked(self) -> bool:
        return self.status != NOT_CHECKED

    @property
    def eligible(self) -> bool:
        """True only on a verdict that was actually computed."""
        return self.checked and self.status == "eligible"

    @property
    def label(self) -> str:
        if not self.checked:
            return f"{NOT_CHECKED} — {self.reason}" if self.reason else NOT_CHECKED
        counts = f"{self.complete_tasks}/{self.total_tasks} filed"
        if self.via_all:
            counts += f"; {self.via_all} via --all"
        if self.outstanding:
            return f"{self.status} ({counts}; outstanding {', '.join(self.outstanding)})"
        return f"{self.status} ({counts})"


def _core() -> Any | None:
    """core's release-gate module, or None on a bundle that predates it."""
    try:
        from otaman_core import release_gate
    except Exception:  # noqa: BLE001 - absent → not-checked, never "eligible"
        return None
    return release_gate if hasattr(release_gate, "cut_eligibility") else None


def _accepts_filed(fn: Any) -> bool:
    """Does core's verdict take pre-read filings, or does it read the bus per change?

    Probed, never version-pinned — the adoption rule that cost nothing when core renamed
    `HarnessBackend`. A True here is the difference between one bus pass and one per
    change (384ms vs 25.7s across this fleet).
    """
    try:
        return "filed" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins / unintrospectable
        return False


def batch_seam_present() -> bool:
    """True when a whole-fleet render is one bus pass rather than one read per change.

    The caller decides what to render with this: the fleet-wide view is opt-in while it
    costs 377ms a change, and automatic once it does not.
    """
    core = _core()
    return core is not None and _accepts_filed(core.cut_eligibility)


def split_tasks(tasks_path: Path) -> tuple[list[str], list[str], str]:
    """``(implementation_ids, gate_ids, error)`` from a change's tasks.md.

    Split on the `## ... Verification gate` heading. A file with no such heading yields
    an empty gate list and an error, because a change whose gate cannot be located has an
    undeterminable gate — not a passing one.
    """
    if not tasks_path.exists():
        # Distinguished from an unreadable file: an approved-unauthored change has no
        # tasks.md yet, which is a lifecycle stage, not a fault to report as one.
        return [], [], "not authored yet (no tasks.md)"
    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [], f"tasks.md could not be read ({type(exc).__name__})"

    try:
        from otaman_core.task_complete import task_id_of
    except Exception:  # noqa: BLE001
        return [], [], "otaman-core does not carry task_id_of"

    implementation: list[str] = []
    gate: list[str] = []
    in_gate = False
    for line in text.splitlines():
        if _SECTION_HEADING.match(line):
            in_gate = bool(_GATE_HEADING.match(line))
            continue
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        body = stripped[5:].strip() if len(stripped) > 5 else ""
        tid = task_id_of(body)
        if not tid:
            continue
        (gate if in_gate else implementation).append(tid)

    if not gate:
        return implementation, [], "no `## Verification gate` section — gate state unknown"
    return implementation, gate, ""


def _blanket_count(
    filed: dict[str, Any], task_ids: list[str] | tuple[str, ...], completed_all: str
) -> int:
    """How many of *task_ids* are complete ONLY because a filing said `--all`.

    Found on this fleet while building the surface: one `--all` filing by plugin-agent
    (20260911T175237) makes every task of `team-mode-registers-and-sessions` read as
    filed — including `2.3 @otaman-plugin B3 privacy enforcement`, still `- [ ]`, and the
    MANDATORY `3.1` verification gate — so the change renders cut-eligible. The sentinel
    is core's documented contract for dispatch (`COMPLETED_ALL in filed` means "all
    filed") and the retraction rule cannot catch it, because a task never ticked was never
    un-ticked.

    Reported to core-agent for the ruling, because the verdict is theirs — and the
    ruling came back the same day: core #109 passes `honor_all=False` for a cut, so a
    blanket claim no longer closes another agent's task. The count stays, for two
    reasons. The STATUS now moves on its own (such a change reads
    `tasks-outstanding`), and the reader still needs to know WHY a change that looks
    finished is not: three tasks filed only by somebody's `--all`. And on a bundle
    that predates #109 the old behaviour is still live, where the annotation is the
    only sign at all.
    """
    if completed_all not in filed:
        return 0
    return sum(1 for tid in dict.fromkeys(task_ids) if tid not in filed)


def verdict_for(
    root: Path,
    change: str,
    changes_dir: Path,
    config: dict[str, Any],
    *,
    filed: dict[str, Any] | None = None,
) -> Verdict:
    """Core's cut-eligibility verdict for *change*, or why there is none.

    *filed* is this change's `{task_id: filed_at}` map, when a caller has already read the
    whole bus once via `filed_complete_by_change`. Without it this reads the bus for this
    change alone — fine for one named change, and the reason the fleet-wide path hands it
    in. It is forwarded to core only when core accepts it (`_accepts_filed`); otherwise it
    still saves this function's own read and core makes its own.
    """
    core = _core()
    if core is None:
        return Verdict(
            change=change,
            reason="otaman-core does not carry release_gate (release-completeness-gate 1.1)",
        )

    tasks_path = changes_dir / change / "tasks.md"
    implementation, gate, error = split_tasks(tasks_path)
    if error:
        return Verdict(change=change, reason=error)

    try:
        from otaman_core.task_complete import (
            COMPLETED_ALL,
            filed_complete_at,
            is_effectively_complete,
        )

        if filed is None:
            filed = filed_complete_at(root, change, config)
        # The gate arm is mine to establish — core's verdict takes it as a parameter —
        # and it uses core's own RETRACTION-AWARE predicate, so a retracted gate filing
        # does not read as a passed gate.
        #
        # `honor_all=False` when core offers it (core #109, from this surface's own
        # report): a blanket `--all` filing must not pass a MANDATORY verification
        # gate any more than it may close another agent's task. team-mode's 3.1
        # test-tenant gate read as passed on one such filing. Probed rather than
        # pinned, so this works either side of core's merge.
        gate_kwargs = {}
        if "honor_all" in inspect.signature(is_effectively_complete).parameters:
            gate_kwargs["honor_all"] = False
        gate_passed = bool(gate) and all(
            is_effectively_complete(tid, filed, tasks_path, **gate_kwargs) for tid in gate
        )
        via_all = _blanket_count(filed, [*implementation, *gate], COMPLETED_ALL)
    except Exception as exc:  # noqa: BLE001 - unreadable filings → not-checked
        return Verdict(change=change, reason=f"filings unreadable ({type(exc).__name__})")

    extra: dict[str, Any] = {}
    if _accepts_filed(core.cut_eligibility):
        extra["filed"] = filed
    try:
        result = core.cut_eligibility(
            root, change, implementation, config, tasks_path, gate_passed=gate_passed, **extra
        )
    except Exception as exc:  # noqa: BLE001 - core raised → not-checked, never eligible
        return Verdict(change=change, reason=f"verdict failed ({type(exc).__name__})")

    return Verdict(
        change=change,
        status=str(result.status),
        total_tasks=int(result.total_tasks),
        complete_tasks=int(result.complete_tasks),
        outstanding=tuple(str(t) for t in result.outstanding),
        gate_passed=bool(result.gate_passed),
        via_all=via_all,
    )


def verdicts_for(
    root: Path,
    changes: list[str],
    changes_dir: Path,
    config: dict[str, Any],
    *,
    budget: float = BUDGET_SECONDS,
) -> tuple[dict[str, Verdict], int]:
    """``({change: Verdict}, skipped)`` for *changes*, under a time budget.

    One bus pass for every change via the batch reader core added for cli #224's
    identical problem — found already there while drafting the ask for it. Whether that
    reaches core's verdict too depends on `batch_seam_present()`; when it does not, the
    budget caps the per-change re-reads and *skipped* is how many changes were never
    looked at, which the caller must state rather than let a short list read as a
    complete one.
    """
    out: dict[str, Verdict] = {}
    started = time.perf_counter()
    skipped = 0

    by_change: dict[str, Any] | None = None
    try:
        from otaman_core.task_complete import filed_complete_by_change

        by_change = filed_complete_by_change(root, config)
    except Exception:  # noqa: BLE001 - fall back to per-change reads; the budget caps it
        by_change = None

    for change in changes:
        if time.perf_counter() - started > budget:
            skipped += 1
            continue
        filed = by_change.get(change, {}) if by_change is not None else None
        out[change] = verdict_for(root, change, changes_dir, config, filed=filed)
    return out, skipped


def summarize(verdicts: dict[str, Verdict], skipped: int = 0) -> str:
    """One line a human can act on: how many may ship, and what the rest are waiting for.

    Counts `not-checked` and *skipped* separately and always names them. A summary that
    said "2 eligible of 68" while 40 gate states were undeterminable would read as a
    finished check over a fleet it never finished checking.
    """
    total = len(verdicts) + skipped
    eligible = sum(1 for v in verdicts.values() if v.eligible)
    parts = [f"{eligible} of {total} cut-eligible"]
    for status in ("tasks-outstanding", "gate-unpassed"):
        n = sum(1 for v in verdicts.values() if v.status == status)
        if n:
            parts.append(f"{n} {status}")
    unchecked = sum(1 for v in verdicts.values() if not v.checked)
    if unchecked:
        parts.append(f"{unchecked} {NOT_CHECKED}")
    if skipped:
        parts.append(f"{skipped} not reached (time budget)")
    blanket = sum(1 for v in verdicts.values() if v.via_all)
    if blanket:
        parts.append(f"{blanket} resting on an --all filing")
    return ", ".join(parts)


__all__ = [
    "BUDGET_SECONDS",
    "NOT_CHECKED",
    "Verdict",
    "batch_seam_present",
    "split_tasks",
    "summarize",
    "verdict_for",
    "verdicts_for",
]
