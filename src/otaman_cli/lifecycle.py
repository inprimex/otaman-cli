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
    delivery: str | None = None  # 'auto' → [auto-delivery] badge (D3/D4)


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


def _compact_ts(stem: str) -> str:
    """The leading ``YYYYMMDDThhmmss`` token of a bus-message filename stem, or "".

    This is the form that ``.openspec.yaml`` ``approved_by`` notes and
    ``dispositions.yaml`` ``approval`` stems cite an approval broadcast by."""
    m = re.match(r"^(\d{8}T\d{6})", stem)
    return m.group(1) if m else ""


def _approved_titles(bus_active_dir: Path) -> list[tuple[str, str, str]]:
    """(title, timestamp, ts_key) for each spec-change-approved broadcast.

    ``ts_key`` is the broadcast's compact filename timestamp — how archived
    changes' ``approved_by`` notes and the disposition ledger reference it.
    """
    if not bus_active_dir.is_dir():
        return []
    out: list[tuple[str, str, str]] = []
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
        out.append((title or f.stem, ts, _compact_ts(f.stem)))
    return out


def _approved_by_blob(changes_dir: Path) -> str:
    """Raw text of every ``.openspec.yaml`` (active AND archived). An approval
    broadcast whose compact ``YYYYMMDDThhmmss`` timestamp appears here already
    authored a change — even when the change's folder slug doesn't resemble the
    broadcast title (archive-aware match, IHC D8).

    Deliberately reads RAW text rather than parsing YAML: real ``.openspec.yaml``
    ``approved_by`` / ``archived`` notes carry free prose with mid-scalar ``: ``
    that breaks a strict loader, and the compact timestamp only ever appears in
    approval references, so a substring scan is both safe and robust."""
    if not changes_dir.is_dir():
        return ""
    dirs = [p for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"]
    archive = changes_dir / "archive"
    if archive.is_dir():
        dirs += [p for p in archive.iterdir() if p.is_dir()]
    parts: list[str] = []
    for d in dirs:
        oy = d / ".openspec.yaml"
        if not oy.is_file():
            continue
        try:
            parts.append(oy.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(parts)


def _dispositions(changes_dir: Path) -> tuple[set[str], set[str]]:
    """(approval-timestamp-tokens, title-slugs) from ``openspec/dispositions.yaml``.

    Approvals recorded there are absorbed/withdrawn — closed, not unauthored (the
    SLE ledger is authoritative for folderless approvals)."""
    tokens: set[str] = set()
    slugs: set[str] = set()
    ledger = changes_dir.parent / "dispositions.yaml"
    if not ledger.is_file():
        return tokens, slugs
    try:
        import yaml

        data = yaml.safe_load(ledger.read_text(encoding="utf-8")) or []
    except Exception:  # noqa: BLE001 - malformed ledger → nothing to exclude
        return tokens, slugs
    if not isinstance(data, list):
        return tokens, slugs
    for entry in data:
        if not isinstance(entry, dict):
            continue
        appr = entry.get("approval")
        if isinstance(appr, str):
            m = re.match(r"^(\d{8}T\d{0,6})", appr.strip())
            if m:
                tokens.add(m.group(1))
        title = entry.get("title")
        if isinstance(title, str):
            slugs.add(_slug(title))
    return tokens, slugs


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


def _completed_next_actor(data: dict, change_name: str) -> str:
    """Next actor for a complete-unarchived change — SHARED by both renderers
    (spec status via derive_lifecycle + the console table via derive_change_table),
    so the ratify-blocked→human rule can never drift between them (gate 3.1).

    A delta change that is done but lacks approval is ratify-blocked at the archive
    gate → the human with ``otaman ratify``. Approved / research → spec-agent."""
    from otaman_core.spec_lifecycle import has_approval, is_research

    data = data if isinstance(data, dict) else {}
    if not is_research(data) and not has_approval(data):
        return f"human (otaman ratify {change_name})"
    return "spec-agent (archive the change)"


def _delivery_of(change_dir: Path) -> str | None:
    """The change's ``delivery:`` mode (``auto``/``hitl``) from .openspec.yaml, or None."""
    from otaman_core.spec_lifecycle import read_openspec

    data = read_openspec(change_dir / ".openspec.yaml")
    v = data.get("delivery") if isinstance(data, dict) else None
    return v if isinstance(v, str) else None


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
    approved_by_blob = _approved_by_blob(changes_dir) if changes_dir else ""
    disp_tokens, disp_slugs = _dispositions(changes_dir) if changes_dir else (set(), set())

    # approved-unauthored: an approval that authored NO change anywhere. An
    # approval is considered authored/closed — not unauthored — when it (a) title-
    # matches a change folder (active or archived), (b) is cited by its compact
    # timestamp in some change's `approved_by` note (archived changes reference
    # the broadcast stem, not their own slug), or (c) is closed in the disposition
    # ledger (absorbed/withdrawn). Without (b)+(c) the lint false-positives on
    # every archived-but-renamed and every folderless approval.
    if bus_active_dir is not None:
        for title, ts, ts_key in _approved_titles(bus_active_dir):
            s = _slug(title)
            if s and any(s == e or s in e or e in s for e in existing):
                continue
            if ts_key and ts_key in approved_by_blob:
                continue
            if ts_key and ts_key in disp_tokens:
                continue
            if s and any(s == d or s in d or d in s for d in disp_slugs):
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
            from otaman_core.spec_lifecycle import read_openspec

            stage = _stage_of(d)
            oy_data = read_openspec(d / ".openspec.yaml")
            deliv = oy_data.get("delivery") if isinstance(oy_data, dict) else None
            deliv = deliv if isinstance(deliv, str) else None
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
                        delivery=deliv,
                    )
                )
            elif _TICKED.search(text):
                # gate 3.1 fix: ratify-blocked complete changes route to the human
                # in spec status too, not just the console table (shared helper).
                nxt = _completed_next_actor(oy_data, d.name)
                rows.append(
                    LifecycleRow(
                        change=d.name,
                        stage=stage,
                        state=COMPLETE_UNARCHIVED,
                        age=_human_age(secs),
                        age_days=days,
                        owner="spec-agent",
                        next_actor=nxt,
                        severity=_bucket_severity(days),
                        delivery=deliv,
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Lifecycle TABLE (IHC 1.5 / D10): one row per active change, all columns, with
# triage class read from .openspec.yaml + a one-key nudge.

#: Triage classes in display order (active first — the moving work separates from
#: the dormant tail at a glance). Unknown/absent sorts last.
TRIAGE_ORDER = ("active", "archive-candidate", "paused-decision", "absorbed", "dormant")


@dataclass(frozen=True)
class ChangeRow:
    """One change as the D10 lifecycle table renders it (values-free)."""

    name: str
    stage: str | None
    state: str
    tasks_done: int
    tasks_total: int
    age: str
    days_in_state: int
    next_actor: str
    triage: str | None
    triage_note: str
    last_touch: str  # last non-chore commit date (YYYY-MM-DD) or "?"
    last_nudged: str  # date of the most recent nudge for this change, or ""
    delivery: str | None = None  # 'auto' → [auto-delivery] badge (D3/D4)


def triage_rank(triage: str | None) -> int:
    return TRIAGE_ORDER.index(triage) if triage in TRIAGE_ORDER else len(TRIAGE_ORDER)


def _tasks_counts(text: str) -> tuple[int, int]:
    done = len(_TICKED.findall(text))
    todo = len(_UNTICKED.findall(text))
    return done, done + todo


def _last_real_touch(change_dir: Path) -> str:
    """Last NON-chore commit date (YYYY-MM-DD) touching the change dir, or '?'.

    Excludes ``chore(...)`` commits (the triage/tick passes) so the column shows
    real progress, not bookkeeping (D10)."""
    import subprocess

    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                str(change_dir),
                "log",
                "-1",
                "--format=%cs",
                "--invert-grep",
                "--grep=^chore",
                "--",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def _last_nudged(bus_active_dir: Path | None, change: str) -> str:
    """Date of the most recent nudge bus message for *change*, or '' (never)."""
    if bus_active_dir is None or not bus_active_dir.is_dir():
        return ""
    matches = sorted(bus_active_dir.glob(f"*-nudge-{change}.md"))
    if not matches:
        return ""
    m = re.match(r"(\d{4})(\d{2})(\d{2})T", matches[-1].name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def derive_change_table(
    *,
    changes_dir: Path | None,
    bus_active_dir: Path | None,
    now: datetime | None = None,
) -> list[ChangeRow]:
    """One ChangeRow per active (non-archived) change, sorted by triage then name."""
    from otaman_core.spec_lifecycle import read_openspec

    now = now or datetime.now(timezone.utc)
    rows: list[ChangeRow] = []
    if not changes_dir:
        return rows
    for d in sorted(p for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"):
        data = read_openspec(d / ".openspec.yaml")
        stage = data.get("stage") if isinstance(data, dict) else None
        triage = data.get("triage") if isinstance(data, dict) else None
        triage_note = str(data.get("triage_note") or "") if isinstance(data, dict) else ""
        done = total = 0
        state = "—"
        next_actor = "—"
        tasks = d / "tasks.md"
        if tasks.is_file():
            try:
                text = tasks.read_text(encoding="utf-8")
            except OSError:
                text = ""
            done, total = _tasks_counts(text)
            if _UNTICKED.search(text):
                state = IN_FLIGHT
                next_actor = _unticked_owners(text)
            elif _TICKED.search(text):
                state = COMPLETE_UNARCHIVED
                next_actor = _completed_next_actor(data, d.name)
        secs = _delta_secs(datetime.fromtimestamp(d.stat().st_mtime, timezone.utc).isoformat(), now)
        rows.append(
            ChangeRow(
                name=d.name,
                stage=stage if isinstance(stage, str) else None,
                state=state,
                tasks_done=done,
                tasks_total=total,
                age=_human_age(secs),
                days_in_state=_age_days(secs),
                next_actor=next_actor,
                triage=triage if isinstance(triage, str) else None,
                triage_note=triage_note,
                last_touch=_last_real_touch(d),
                last_nudged=_last_nudged(bus_active_dir, d.name),
                delivery=(data.get("delivery") if isinstance(data, dict) else None),
            )
        )
    rows.sort(key=lambda r: (triage_rank(r.triage), r.name))
    return rows


def nudge_target(next_actor: str, triage: str | None) -> str:
    """The agent a nudge is sent to — ``human`` when the next actor is the human
    (e.g. a ratify-blocked row), else the first ``*-agent`` in the next-actor
    string, else a triage-based fallback (paused → human, else spec-agent)."""
    na = next_actor or ""
    if re.search(r"\bhuman\b", na):  # ratify-blocked / human-next-actor rows
        return "human"
    m = re.search(r"[a-z0-9]+-agent", na)
    if m:
        return m.group(0)
    return "human" if triage == "paused-decision" else "spec-agent"


def send_nudge(program, row: ChangeRow, *, note: str = "") -> tuple[bool, str]:
    """One-key nudge: a bus ping to the row's next actor naming the change /
    state / age / expected next step (D10). Returns (ok, message)."""
    from otaman_cli.bus_write import write_message_exclusive

    active_dir, _ = program.bus_paths()
    target = nudge_target(row.next_actor, row.triage)
    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts = now.strftime("%Y%m%dT%H%M%S")
    stem = f"{ts}-human-to-{target}-nudge-{row.name}"[:120]
    note_section = f"\nNote: {note}\n" if note.strip() else ""
    content = (
        f"---\nid: {stem}\nfrom: human\nto: {target}\npriority: normal\ntype: info\n"
        f"timestamp: {iso}\nstatus: pending\n---\n\n"
        f"## Subject: Nudge: {row.name} ({row.state}, {row.age} in state)\n\n"
        f"Change **{row.name}** — stage {row.stage or '?'}, state {row.state}, "
        f"{row.age} in state, tasks {row.tasks_done}/{row.tasks_total}. "
        f"Expected next step: {row.next_actor}.{note_section}"
    )
    write_message_exclusive(active_dir / f"{stem}.md", content)
    return True, f"nudged {target} about {row.name}"


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "SEV_ERROR",
    "SEV_OK",
    "SEV_WARN",
    "TRIAGE_ORDER",
    "ChangeRow",
    "LifecycleRow",
    "derive_change_table",
    "derive_lifecycle",
    "nudge_target",
    "send_nudge",
    "triage_rank",
]
