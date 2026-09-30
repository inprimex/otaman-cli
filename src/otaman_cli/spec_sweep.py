"""task-complete-reconciler 1.2 — the reconciler behind `otaman spec sweep`.

A filed `task-complete` is a promise that `tasks.md` will catch up. Until this
existed nothing kept it: the tick applied only when the specs owner's agent
happened to sweep by hand. On the pmeets tenant that ran ~2 weeks — the lens
under-counted 5/11 against a real 11/11, finished changes were never archived,
and nothing warned, because every filer had been told it was handled.

Three things this module deliberately does NOT own:

* **Reading filings.** `otaman_core.task_complete` is the single home — it
  matches on `type: task-complete` frontmatter (never filename), scans active
  AND archive, and carries the retraction rule. A second reader here would be
  the duplication the change exists to remove.
* **Writing ticks.** `otaman_plugin.actualize_tasks` already owns the tasks.md
  write and is what `otaman complete` uses on the owner path. The sweep drives
  it; it does not re-implement it.
* **Parsing a `**Completed**:` spec.** Core's parser is private, so rather than
  copy it, an unparseable filing is detected from what core RECOGNISED — a
  message carrying a Completed line that contributed no id core could read.

What it does own: deciding which ids are still owed a tick, honouring
retraction, and reporting counted outcomes with zero work stated out loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A filing whose `**Completed**:` line names nothing core could parse. Surfaced
#: rather than dropped: a filer who wrote "live-test" or prose believes the work
#: is recorded, and silence would leave the task un-ticked with nobody looking.
_COMPLETED_LINE = re.compile(r"^\*\*Completed\*\*:\s*(.+)$", re.MULTILINE)


@dataclass
class ChangeOutcome:
    """What the sweep found, and did, for one change."""

    change: str
    owed: list[str] = field(default_factory=list)  # ids effectively complete but un-ticked
    applied: int = 0
    already: int = 0
    not_found: list[str] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)  # filed, but a later un-tick wins
    unparseable: list[str] = field(default_factory=list)  # message stems
    ambiguous: list[str] = field(default_factory=list)  # one id, several task lines
    error: str = ""

    @property
    def did_work(self) -> bool:
        return bool(
            self.applied or self.owed or self.unparseable or self.retracted or self.ambiguous
        )


def _core() -> Any | None:
    """core's filed-completes reader, or None when this install predates it."""
    try:
        from otaman_core import task_complete
    except Exception:  # noqa: BLE001 - absent reader → not-checked, never "clean"
        return None
    needed = ("filed_complete_at", "is_effectively_complete", "task_id_of", "COMPLETED_ALL")
    return task_complete if all(hasattr(task_complete, n) for n in needed) else None


def _batch_reader(core: Any):
    """core's one-pass reader, or None on an install that predates it (core #89).

    Probed, never version-pinned: the same core version exists with and without
    it. Absent, the per-change path still works — slower, and the count says so
    rather than lying about it.
    """
    return getattr(core, "filed_complete_by_change", None)


def _task_lines(tasks_path: Path) -> list[tuple[str, bool]]:
    """`(task_id, is_ticked)` for every task line, via core's id parser."""
    core = _core()
    if core is None or not tasks_path.is_file():
        return []
    out: list[tuple[str, bool]] = []
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        ticked = stripped[:5].lower().startswith("- [x")
        body = stripped[5:].strip() if len(stripped) > 5 else ""
        tid = core.task_id_of(body)
        if tid:
            out.append((tid, ticked))
    return out


def _all_filings(root: Path, config: dict[str, Any]) -> list[tuple[str, str]]:
    """Every task-complete message once, as `(stem, text)`.

    The per-change path re-globbed AND re-read the bus for each change — the
    same O(changes x messages) shape core #89's batch reader removes on its
    side, and pointless to leave here. Reading the text once matters as much as
    globbing once: it is the same files either way.
    """
    from otaman_cli.bus_message_types import messages_of_type

    bus_rel = (config.get("communication") or {}).get("bus_path", ".agents/bus")
    out: list[tuple[str, str]] = []
    for sub in ("active", "archive"):
        for path in messages_of_type(root / bus_rel / sub, "task-complete"):
            try:
                out.append((path.stem, path.read_text(encoding="utf-8")))
            except OSError:
                continue
    return out


def _unparseable_filings(
    root: Path,
    change: str,
    config: dict[str, Any],
    recognised,
    *,
    filings: list[Any] | None = None,
) -> list[str]:
    """Stems of task-complete filings for *change* that named nothing readable.

    Detected from what core recognised rather than by re-parsing: a message with
    a `**Completed**:` line, none of whose recognised ids appear in it, told the
    reader nothing. Under-reports if an unreadable spec happens to contain
    another filing's id verbatim — stated, because a silent miss here is the
    failure this surfacing exists to prevent.
    """
    stems: list[str] = []
    for stem, text in filings if filings is not None else _all_filings(root, config):
        if not re.search(rf"^change:\s*{re.escape(change)}\s*$", text, re.MULTILINE):
            continue
        specs = _COMPLETED_LINE.findall(text)
        if not specs:
            continue  # no Completed line at all — not a tick claim, not a defect
        if any(any(rid in spec for rid in recognised) for spec in specs):
            continue
        stems.append(stem)
    return stems


def plan(
    root: Path,
    changes_dir: Path,
    change: str,
    config: dict[str, Any],
    *,
    filed: dict[str, Any] | None = None,
    filings: list[Any] | None = None,
) -> ChangeOutcome:
    """What *change* is owed, without writing anything.

    *filed* and *filings* let a fleet-wide caller hand in what it already read.
    Core's reader is per-change, so asking about N changes cost N whole-bus
    scans — 61 changes over 6657 messages measured 23s, which is why the
    awaiting-tick count had a budget it could not meet. core #89's batch reader
    collapses that to one pass; these parameters are how it reaches here.
    """
    outcome = ChangeOutcome(change=change)
    core = _core()
    if core is None:
        outcome.error = (
            "otaman-core does not carry the filed-completes reader "
            "(task-complete-reconciler 1.1) — cannot check"
        )
        return outcome

    tasks_path = changes_dir / change / "tasks.md"
    if not tasks_path.is_file():
        return outcome

    if filed is None:
        filed = core.filed_complete_at(root, change, config)
    recognised = {k for k in filed if k != core.COMPLETED_ALL}

    # Surfaced BEFORE the empty-filed shortcut. When nothing parses, `filed` is
    # empty — and returning early there would drop precisely the filings that
    # need surfacing most: a change whose every filing was unreadable would
    # report "nothing owed", which is the silent drop this exists to prevent.
    outcome.unparseable = _unparseable_filings(root, change, config, recognised, filings=filings)
    if not filed:
        return outcome
    all_filed = core.COMPLETED_ALL in filed

    lines = _task_lines(tasks_path)

    # Two task lines can still carry the SAME id — a duplicated line in a
    # hand-edited tasks.md. A tick aimed at that id lands on both, which is a
    # wrong write, not a display quirk, so it is reported and never applied.
    #
    # `1.7-bis` reading as `1.7` used to be the live instance of this; core #88
    # taught `task_id_of` the suffix (from this sweep's report, 2026-09-29), so
    # that case is gone. The guard stays because a literal duplicate is still
    # possible and still writes to the wrong line.
    seen: dict[str, int] = {}
    for tid, _ in lines:
        seen[tid] = seen.get(tid, 0) + 1
    collided = {tid for tid, n in seen.items() if n > 1}
    outcome.ambiguous = sorted(collided)

    for tid, ticked in lines:
        if tid in collided:
            continue
        if not (all_filed or tid in filed):
            continue
        if not core.is_effectively_complete(tid, filed, tasks_path):
            # A filing older than this task's most recent un-tick is withdrawn
            # evidence — it must neither re-tick nor block re-dispatch.
            if not ticked:
                outcome.retracted.append(tid)
            continue
        if ticked:
            outcome.already += 1
        else:
            outcome.owed.append(tid)

    return outcome


def changes_with_filings(changes_dir: Path) -> list[str]:
    """Every active change directory carrying a tasks.md, sorted."""
    if not changes_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in changes_dir.iterdir()
        if d.is_dir() and d.name != "archive" and (d / "tasks.md").is_file()
    )


#: How long the awaiting-tick COUNT may spend before it gives up and says so.
#: `otaman check` is the most-run command in the fleet; a count that makes it
#: pause is a count nobody keeps. Exceeding this renders not-checked — never a
#: partial number, which would read as authoritative while being short.
#:
#: Deliberately small. On an ordinary tenant the whole count finishes well
#: inside it and the real figure renders; on a fleet this size it cannot finish
#: at any budget worth paying on every `check`, so a bigger one would only buy a
#: slower way to print the same "NOT CHECKED". The fix for that is a batch
#: reader in core (one bus scan for all changes instead of one per change —
#: measured 61 scans over 6657 messages, 23s), not a longer wait here.
COUNT_BUDGET_SECONDS = 1.0


def awaiting_tick(
    root: Path, changes_dir: Path, config: dict[str, Any], budget: float = COUNT_BUDGET_SECONDS
) -> tuple[int | None, str]:
    """``(count, note)`` — task ids filed but not yet ticked, fleet-wide.

    ``None`` means NOT CHECKED, with *note* saying why; it is never 0, because a
    zero that means "did not look" is the failure this whole change exists to
    end (pmeets: ~2 silent weeks, the lens reading 5/11 against a real 11/11).

    Bounded on purpose, and the bound now bites for a DIFFERENT reason than it
    did. Core #89's batch reader collapsed the bus side from 61 whole-bus scans
    (23s) to one pass — measured here at 666ms for the reader plus 370ms for the
    filings, about a second.

    What remains is the retraction rule: `is_effectively_complete` calls
    `last_untick_at`, which runs `git log` plus a `git show` per commit, PER
    TASK. Core batched the bus deliberately and left git alone — the right call,
    since that is the path whose exactness I tested hardest and asked them not to
    touch. With 115 filed tasks on this fleet the full count measures 51s.

    So the count still renders NOT CHECKED here, and says which half is
    expensive. It is NOT computed without retraction and shown as an
    approximation: an over-count rendered confidently is the failure this whole
    change exists to remove, and 115-that-might-be-113 is exactly that.
    """
    import time

    core = _core()
    if core is None:
        return None, "otaman-core does not carry the filed-completes reader (kv2/tcr 1.1)"

    started = time.monotonic()
    total = 0

    # ONE pass over the bus, then dict lookups per change (core #89). The
    # per-change reader cost 61 whole-bus scans over 6657 messages — 23s — which
    # is why this count used to render NOT CHECKED on the largest tenant it was
    # built for. Probed, so an older core still works by the slower path.
    batch = _batch_reader(core)
    try:
        by_change = batch(root, config) if batch is not None else None
        filings = _all_filings(root, config)
    except Exception as exc:  # noqa: BLE001 - an unreadable bus is not zero
        return None, f"the reader failed: {type(exc).__name__}"

    for name in changes_with_filings(changes_dir):
        if time.monotonic() - started > budget:
            return None, (
                f"the count exceeded its {budget:g}s budget (the per-task retraction "
                "scan reads git) — run `otaman spec sweep` for the exact figure"
            )
        try:
            owed = plan(
                root,
                changes_dir,
                name,
                config,
                filed=(by_change or {}).get(name, {}) if by_change is not None else None,
                filings=filings,
            ).owed
            total += len(owed)
        except Exception as exc:  # noqa: BLE001 - one bad change must not blank the count
            return None, f"the reader failed on {name}: {type(exc).__name__}"
    return total, ""
