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


def _unparseable_filings(root: Path, change: str, config: dict[str, Any], recognised) -> list[str]:
    """Stems of task-complete filings for *change* that named nothing readable.

    Detected from what core recognised rather than by re-parsing: a message with
    a `**Completed**:` line, none of whose recognised ids appear in it, told the
    reader nothing. Under-reports if an unreadable spec happens to contain
    another filing's id verbatim — stated, because a silent miss here is the
    failure this surfacing exists to prevent.
    """
    from otaman_cli.bus_message_types import messages_of_type

    bus_rel = (config.get("communication") or {}).get("bus_path", ".agents/bus")
    stems: list[str] = []
    for sub in ("active", "archive"):
        directory = root / bus_rel / sub
        for path in messages_of_type(directory, "task-complete"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if not re.search(rf"^change:\s*{re.escape(change)}\s*$", text, re.MULTILINE):
                continue
            specs = _COMPLETED_LINE.findall(text)
            if not specs:
                continue  # no Completed line at all — not a tick claim, not a defect
            if any(any(rid in spec for rid in recognised) for spec in specs):
                continue
            stems.append(path.stem)
    return stems


def plan(root: Path, changes_dir: Path, change: str, config: dict[str, Any]) -> ChangeOutcome:
    """What *change* is owed, without writing anything."""
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

    filed = core.filed_complete_at(root, change, config)
    recognised = {k for k in filed if k != core.COMPLETED_ALL}

    # Surfaced BEFORE the empty-filed shortcut. When nothing parses, `filed` is
    # empty — and returning early there would drop precisely the filings that
    # need surfacing most: a change whose every filing was unreadable would
    # report "nothing owed", which is the silent drop this exists to prevent.
    outcome.unparseable = _unparseable_filings(root, change, config, recognised)
    if not filed:
        return outcome
    all_filed = core.COMPLETED_ALL in filed

    lines = _task_lines(tasks_path)

    # Two task lines can collapse to one id: core's `task_id_of` stops at a word
    # boundary, so `1.7-bis` reads as `1.7`. A tick aimed at either would land on
    # both, which is a wrong write, not a display quirk — so those ids are
    # reported and NEVER applied. Reported to core (2026-09-29); if core learns
    # the suffix, these simply stop appearing.
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
