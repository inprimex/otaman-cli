"""Shared, archive-aware change-lifecycle derivation (SLE 2.1 / D3).

One derivation, two renderers: `otaman spec status` + the doctor section, and the
`otaman -i` console lifecycle view (IHC 1.3) both consume this. Per change it
yields the stage (from `.openspec.yaml` — the repo is truth, D1), the catchable
state, days-in-state, who owns it and who acts next, and a severity bucket
(WARN at day 1, ERROR at day 3 for the states where work stalls).

States (interim, until every change carries a stage the machine drives):
- ``approved-unauthored`` — an approval broadcast on the bus with no change
  folder in either active OR archived changes (archive-aware, IHC D8).
- ``in-flight`` — an active change folder whose ``tasks.md`` has unticked tasks.
- ``complete-unarchived`` — all tasks ticked, folder not archived.

Textual-free so both renderers and the unit tests share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

APPROVED_UNAUTHORED = "approved-unauthored"
IN_FLIGHT = "in-flight"
COMPLETE_UNARCHIVED = "complete-unarchived"

#: Severity buckets (D3): the stalled states escalate with age.
SEV_OK = "ok"
SEV_WARN = "warn"
SEV_ERROR = "error"

_ARCHIVE_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_UNTICKED = re.compile(r"^\s*-\s*\[\s*\]", re.MULTILINE)
_TICKED = re.compile(r"^\s*-\s*\[[xX]\]", re.MULTILINE)
_OWNER_TAG = re.compile(r"@otaman-[a-z0-9-]+")


@dataclass(frozen=True)
class LifecycleRow:
    """One change's catchable lifecycle state (values-free, renderer-agnostic)."""

    change: str
    stage: str | None
    state: str
    age: str
    age_days: int
    owner: str
    next_actor: str
    severity: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _delta_secs(ts_iso: str, now: datetime) -> int | None:
    if not ts_iso:
        return None
    try:
        ts = datetime.fromisoformat(ts_iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int((now - ts).total_seconds())


def _human_age(secs: int | None) -> str:
    if secs is None:
        return "?"
    secs = max(secs, 0)
    if secs >= 86400:
        return f"{secs // 86400}d"
    if secs >= 3600:
        return f"{secs // 3600}h"
    return f"{secs // 60}m"


def _age_days(secs: int | None) -> int:
    return max(secs, 0) // 86400 if secs is not None else 0


def _bucket_severity(age_days: int) -> str:
    """WARN at day 1, ERROR at day 3 (D3) for the stalled states."""
    if age_days >= 3:
        return SEV_ERROR
    if age_days >= 1:
        return SEV_WARN
    return SEV_OK


def _existing_change_slugs(changes_dir: Path) -> set[str]:
    """Slugs of every change folder — active AND archived (date-prefix stripped)."""
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


def _approved_titles(bus_active_dir: Path) -> list[tuple[str, str]]:
    """(title, timestamp) for each spec-change-approved broadcast on the bus."""
    if not bus_active_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for f in sorted(bus_active_dir.glob("*spec-change-approved*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        ts = ""
        title = ""
        for line in text.splitlines():
            if not ts and line.startswith("timestamp:"):
                ts = line.split(":", 1)[1].strip()
            s = line.strip()
            if not title and s.startswith("## Subject:"):
                title = re.sub(
                    r"^Approved:\s*", "", s.replace("## Subject:", "").strip(), flags=re.IGNORECASE
                )
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


def _stage_of(change_dir: Path) -> str | None:
    from otaman_core.spec_lifecycle import read_stage

    return read_stage(change_dir / ".openspec.yaml")


def derive_lifecycle(
    *,
    changes_dir: Path | None,
    bus_active_dir: Path | None,
    now: datetime | None = None,
) -> list[LifecycleRow]:
    """Catchable lifecycle rows from the specs repo + bus. Both inputs optional."""
    now = now or datetime.now(timezone.utc)
    rows: list[LifecycleRow] = []
    existing = _existing_change_slugs(changes_dir) if changes_dir else set()

    # approved-unauthored: an approval with no matching change folder anywhere.
    if bus_active_dir is not None:
        for title, ts in _approved_titles(bus_active_dir):
            s = _slug(title)
            if s and any(s == e or s in e or e in s for e in existing):
                continue
            secs = _delta_secs(ts, now)
            days = _age_days(secs)
            rows.append(
                LifecycleRow(
                    change=title,
                    stage=None,
                    state=APPROVED_UNAUTHORED,
                    age=_human_age(secs),
                    age_days=days,
                    owner="spec-agent",
                    next_actor="spec-agent (author the change)",
                    severity=_bucket_severity(days),
                )
            )

    # in-flight / complete-unarchived from the active (non-archived) folders.
    if changes_dir:
        for d in sorted(p for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"):
            tasks = d / "tasks.md"
            if not tasks.is_file():
                continue
            try:
                text = tasks.read_text(encoding="utf-8")
            except OSError:
                continue
            secs = _delta_secs(
                datetime.fromtimestamp(d.stat().st_mtime, timezone.utc).isoformat(), now
            )
            days = _age_days(secs)
            stage = _stage_of(d)
            if _UNTICKED.search(text):
                rows.append(
                    LifecycleRow(
                        change=d.name,
                        stage=stage,
                        state=IN_FLIGHT,
                        age=_human_age(secs),
                        age_days=days,
                        owner=_unticked_owners(text),
                        next_actor=_unticked_owners(text),
                        severity=SEV_OK,  # active work is not an alarm
                    )
                )
            elif _TICKED.search(text):
                rows.append(
                    LifecycleRow(
                        change=d.name,
                        stage=stage,
                        state=COMPLETE_UNARCHIVED,
                        age=_human_age(secs),
                        age_days=days,
                        owner="spec-agent",
                        next_actor="spec-agent (archive the change)",
                        severity=_bucket_severity(days),
                    )
                )
    return rows


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "SEV_ERROR",
    "SEV_OK",
    "SEV_WARN",
    "LifecycleRow",
    "derive_lifecycle",
]
