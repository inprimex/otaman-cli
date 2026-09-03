"""Runner-free lifecycle-state derivation for the console (task 1.3 / D7).

Beyond the pending HITL queue, surface the catchable states where work stalls,
derived from the bus + the specs repo with NO runner:

- ``approved-unauthored`` — an approval broadcast exists on the bus but no
  matching change folder in the specs repo (the 2026-09-03 staleness incident:
  four SCRs approved, none authored, invisible for 8-10 days).
- ``in-flight`` — a change folder whose ``tasks.md`` still has unticked tasks.
- ``complete-unarchived`` — all tasks ticked, but the folder isn't archived.

Each row names the change, its state, the time it has been in that state, and
who holds the next action. This is the INTERIM derivation D7 defines; when
spec-lifecycle-enforcement's stage machine lands the view swaps to its stages
with no UI change. Textual-free so it stays unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otaman_cli.console.bus import Program

APPROVED_UNAUTHORED = "approved-unauthored"
IN_FLIGHT = "in-flight"
COMPLETE_UNARCHIVED = "complete-unarchived"

_ARCHIVE_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_UNTICKED = re.compile(r"^\s*-\s*\[\s*\]", re.MULTILINE)
_TICKED = re.compile(r"^\s*-\s*\[[xX]\]", re.MULTILINE)
_OWNER_TAG = re.compile(r"@otaman-[a-z0-9-]+")


@dataclass(frozen=True)
class LifecycleRow:
    """One catchable lifecycle state as the console displays it (values-free)."""

    change: str
    state: str
    age: str
    next_action: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _age(ts_iso: str, *, now: datetime | None = None) -> str:
    """A short "time in state" string from an ISO timestamp, or "?" if unparseable."""
    if not ts_iso:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (now or datetime.now(timezone.utc)) - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "0m"
    if secs >= 86400:
        return f"{secs // 86400}d"
    if secs >= 3600:
        return f"{secs // 3600}h"
    return f"{max(secs // 60, 0)}m"


def _specs_changes_dir(program: Program) -> Path | None:
    """The specs repo's ``openspec/changes`` dir (from platform.yaml specs.path)."""
    import yaml

    try:
        cfg = yaml.safe_load((program.root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    specs = cfg.get("specs") if isinstance(cfg, dict) else None
    path = specs.get("path") if isinstance(specs, dict) else None
    if not path:
        return None
    changes = (program.root / path / "openspec" / "changes").resolve()
    return changes if changes.is_dir() else None


def _existing_change_slugs(changes_dir: Path) -> set[str]:
    """Slugs of every change folder (active + archived, date-prefix stripped)."""
    slugs: set[str] = set()
    for d in changes_dir.iterdir():
        if d.is_dir() and d.name != "archive":
            slugs.add(d.name)
    archive = changes_dir / "archive"
    if archive.is_dir():
        for d in archive.iterdir():
            if d.is_dir():
                slugs.add(_ARCHIVE_DATE_PREFIX.sub("", d.name))
    return slugs


def _approved_titles(program: Program) -> list[tuple[str, str]]:
    """(title, timestamp) for each spec-change-approved broadcast on the bus."""
    active_dir, _ = program.bus_paths()
    if not active_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for f in sorted(active_dir.glob("*spec-change-approved*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        ts = ""
        for line in text.splitlines():
            if line.startswith("timestamp:"):
                ts = line.split(":", 1)[1].strip()
                break
        title = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("## Subject:"):
                title = s.replace("## Subject:", "").strip()
                title = re.sub(r"^Approved:\s*", "", title, flags=re.IGNORECASE)
                break
        out.append((title or f.stem, ts))
    return out


def _unticked_owners(tasks_text: str) -> str:
    """The @otaman-<repo> owners named on unticked task lines, or a fallback."""
    owners: list[str] = []
    for line in tasks_text.splitlines():
        if re.match(r"^\s*-\s*\[\s*\]", line):
            owners.extend(_OWNER_TAG.findall(line))
    uniq = sorted(set(owners))
    return (
        ", ".join(o.replace("@otaman-", "") + "-agent" for o in uniq) if uniq else "assigned agents"
    )


def list_lifecycle_states(program: Program, *, now: datetime | None = None) -> list[LifecycleRow]:
    """Catchable lifecycle states for *program*, derived from bus + specs repo."""
    rows: list[LifecycleRow] = []
    changes_dir = _specs_changes_dir(program)
    existing = _existing_change_slugs(changes_dir) if changes_dir else set()

    # approved-unauthored: an approval with no matching change folder anywhere.
    for title, ts in _approved_titles(program):
        s = _slug(title)
        if s and any(s == e or s in e or e in s for e in existing):
            continue
        rows.append(
            LifecycleRow(
                change=title,
                state=APPROVED_UNAUTHORED,
                age=_age(ts, now=now),
                next_action="spec-agent (author the change)",
            )
        )

    # in-flight / complete-unarchived from the active (non-archived) change folders.
    if changes_dir:
        for d in sorted(p for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"):
            tasks = d / "tasks.md"
            if not tasks.is_file():
                continue
            try:
                text = tasks.read_text(encoding="utf-8")
            except OSError:
                continue
            folder_age = _age(
                datetime.fromtimestamp(d.stat().st_mtime, timezone.utc).isoformat(), now=now
            )
            if _UNTICKED.search(text):
                rows.append(
                    LifecycleRow(
                        change=d.name,
                        state=IN_FLIGHT,
                        age=folder_age,
                        next_action=_unticked_owners(text),
                    )
                )
            elif _TICKED.search(text):
                rows.append(
                    LifecycleRow(
                        change=d.name,
                        state=COMPLETE_UNARCHIVED,
                        age=folder_age,
                        next_action="spec-agent (archive the change)",
                    )
                )
    return rows


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "LifecycleRow",
    "list_lifecycle_states",
]
