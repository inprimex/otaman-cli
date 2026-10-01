#!/usr/bin/env python3
"""Archive and clean up old bus messages.

Usage:
    python cleanup-bus.py <project-root> [--dry-run] [--archive-days N] [--delete-days N]

Archives messages that are fully acked and older than archive-days (default: from
platform.yaml communication.max_age_days, or 30).
Deletes archived messages older than delete-days (default: 90).

Outputs JSON report of actions taken.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def parse_frontmatter(filepath: Path) -> dict[str, Any] | None:
    """Frontmatter of *filepath*, or None when it is not a bus message.

    The parse itself is core's (shared-logic 1.2). ``None`` is preserved for
    "not a message" because every caller here skips on it; core returns ``{}``
    for that case, and a caller checking ``is None`` would stop skipping.
    """
    from otaman_cli.frontmatter_gate import frontmatter

    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, _body = frontmatter().parse(content)
    return fm or None


def get_agents(project_root: Path) -> list[str]:
    """Get list of all agent names from agents.yaml."""
    agents_file = project_root / ".agents" / "agents.yaml"
    if not agents_file.exists():
        return []
    try:
        with open(agents_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return [a["name"] for a in data.get("agents", []) if a.get("role") == "developer"]
    except (OSError, yaml.YAMLError):
        return []


def get_msg_id(filepath: Path) -> str:
    """Extract message ID from filename (timestamp prefix or sequence number)."""
    name = filepath.stem
    # Timestamp-based: 20260306T120000-...
    # Sequence-based: 004-...
    return name


def is_fully_acked(msg_path: Path, acks_dir: Path, agents: list[str], fm: dict[str, Any]) -> bool:
    """Check if a broadcast message has been acked by all relevant agents."""
    to = fm.get("to", "")
    msg_id = get_msg_id(msg_path)

    if to == "all":
        # Need all developer agents to ack
        if not agents:
            return False
        for agent in agents:
            ack_file = acks_dir / f"{msg_id}.{agent}.ack"
            if not ack_file.exists():
                return False
        return True
    else:
        # Single-agent message: check if that agent acked
        ack_file = acks_dir / f"{msg_id}.{to}.ack"
        return ack_file.exists()


#: Legacy hand-listed formats, kept as a FALLBACK only. Each one is a shape
#: `fromisoformat` already accepts, so nothing reaches them in practice — they
#: stay so that no timestamp which parsed before this fix stops parsing now.
_LEGACY_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S")


def _normalize_fraction(ts: str) -> str:
    """Pad or trim fractional seconds to 6 digits.

    Python 3.10's ``fromisoformat`` accepts ONLY 3 or 6 fractional digits (3.11
    widened it). The floor for this package is 3.10 and CI runs 3.11, so without
    this a 2-digit fraction parses on the runner and fails on a tenant — a
    version-dependent parse, which is worse than a format list because it is
    invisible until someone else's machine disagrees.
    """
    head, sep, tail = ts.partition(".")
    if not sep:
        return ts
    digits = ""
    for ch in tail:
        if not ch.isdigit():
            break
        digits += ch
    if not digits:
        return ts
    rest = tail[len(digits) :]
    return f"{head}.{digits[:6].ljust(6, '0')}{rest}"


def parse_timestamp(fm: dict[str, Any]) -> datetime | None:
    """The bus timestamp as an aware datetime, or None if it is genuinely malformed.

    This rejected **98.4% of the live bus** (deploy-agent root-cause
    20261001T142130: 6,893 of 7,006 messages). It tried three hand-listed
    `strptime` formats, none of which accepts fractional seconds — while the
    producer writes `datetime.now(UTC).isoformat()`, which emits them. A second
    family failed too: `2026-05-24 21:29:29+00:00`, space-separated WITH an
    offset, which the listed `%Y-%m-%d %H:%M:%S` cannot take.

    Every rejected message was then skipped by a bare `continue`, so it never
    reached the age or ack check. `otaman cleanup` reported "Nothing to clean up"
    over 6,980 active messages and one archived, for four and a half months —
    technically true, and true only because 98.4% of the input was discarded
    before any criterion was applied.

    `fromisoformat` is the right parser because the producer uses `isoformat`:
    one function that accepts exactly what the other emits, instead of a format
    list this side maintains alone. ``Z`` is trimmed because 3.10 does not take
    it, and fractional digits are normalized because 3.10 wants 3 or 6.

    NOTE (single-home): the canonical bus-timestamp parse now has two consumers —
    `otaman_core.task_complete` already does `fromisoformat(...replace("Z", ...))`
    inline, and this. Asked core-agent to home it; this repoints when it lands.
    """
    ts = fm.get("timestamp", "")
    if not ts:
        return None
    ts_str = str(ts).strip()
    if not ts_str:
        return None
    candidate = ts_str[:-1] + "+00:00" if ts_str.endswith(("Z", "z")) else ts_str
    try:
        dt = datetime.fromisoformat(_normalize_fraction(candidate))
    except ValueError:
        dt = None
    if dt is None:
        for fmt in _LEGACY_FORMATS:
            try:
                dt = datetime.strptime(ts_str, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def migrate_flat_to_active(bus_dir: Path) -> int:
    """Migrate messages from flat bus/ to bus/active/ structure.

    Returns count of migrated files.
    """
    active_dir = bus_dir / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "acks").mkdir(exist_ok=True)

    migrated = 0
    for f in bus_dir.glob("*.md"):
        # Only move .md files in the root bus dir (not in subdirs)
        if f.parent == bus_dir:
            dest = active_dir / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                migrated += 1
    return migrated


def reap_orphan_status_files(project_root: Path, *, dry_run: bool = False) -> list[str]:
    """Remove `.agents/status/*.yaml` files whose agent has no agents.yaml entry.

    Returns the file names reaped (or that WOULD be reaped under *dry_run*) so
    the caller can name each — a phantom agent must never disappear silently
    either. Orphan detection is shared with `otaman doctor` (which reports the
    same files) so the two can't disagree about what is an orphan.
    """
    try:
        from otaman_core.validate_message import load_known_agents

        from otaman_cli.doctor import find_orphan_status_files

        orphans = find_orphan_status_files(project_root, load_known_agents(project_root))
    except Exception:  # noqa: BLE001 - no registry/status dir → nothing to reap
        return []

    reaped: list[str] = []
    for path in orphans:
        if not dry_run:
            try:
                path.unlink()
            except OSError:
                continue
        reaped.append(path.name)
    return reaped


def cleanup(
    project_root: Path,
    archive_days: int = 30,
    delete_days: int = 90,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run cleanup on the bus. Returns a report dict."""
    report: dict[str, Any] = {
        "migrated": 0,
        "archived": [],
        "deleted": [],
        "status_orphans": [],
        "active_count": 0,
        "archive_count": 0,
        "errors": [],
        # no-silent-success: what was NOT examined, and why. The parse bug cost
        # four months because the skip was invisible — had the run said "skipped
        # 6,893 messages with unparseable timestamps", it would have been a
        # one-minute diagnosis (deploy-agent, 20261001T142130). A count of what a
        # verb declined to look at is part of its result, not a debug detail.
        "skipped_unparseable": 0,
        "skipped_no_frontmatter": 0,
        "skipped_samples": [],
        # Old enough to archive and held back only by a missing ack. Reported so
        # that the question after this fix ("why are these still here?") arrives
        # answered instead of becoming the next four-month mystery.
        "held_unacked": 0,
    }

    # identity-divergence 1.4: reap status files with no agents.yaml entry. Runs
    # BEFORE the bus-dir guard below — a phantom agent's status file must be
    # reapable even on a program whose bus dir is absent, and it is precisely the
    # surface that kept the pmeets phantom visible for 17 hours.
    report["status_orphans"] = reap_orphan_status_files(project_root, dry_run=dry_run)

    # Load config for bus path
    config_path = project_root / "platform.yaml"
    bus_rel = ".agents/bus"
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            bus_rel = config.get("communication", {}).get("bus_path", ".agents/bus")
            archive_days = config.get("communication", {}).get("max_age_days", archive_days)
        except (OSError, yaml.YAMLError):
            pass

    bus_dir = project_root / bus_rel
    if not bus_dir.is_dir():
        report["errors"].append("Bus directory not found")
        return report

    # Step 1: Migrate flat structure to active/ if needed
    active_dir = bus_dir / "active"
    if not active_dir.exists():
        migrated = migrate_flat_to_active(bus_dir)
        report["migrated"] = migrated
    else:
        active_dir.mkdir(parents=True, exist_ok=True)

    acks_dir = active_dir / "acks"
    acks_dir.mkdir(exist_ok=True)

    archive_dir = bus_dir / "archive"
    archive_dir.mkdir(exist_ok=True)

    agents = get_agents(project_root)
    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=archive_days)
    delete_cutoff = now - timedelta(days=delete_days)

    # Step 2: Archive old, fully-acked messages from active/
    for msg_file in sorted(active_dir.glob("*.md")):
        fm = parse_frontmatter(msg_file)
        if not fm:
            report["skipped_no_frontmatter"] += 1
            continue

        ts = parse_timestamp(fm)
        if not ts:
            report["skipped_unparseable"] += 1
            # A bounded sample, because the count alone does not tell you WHICH
            # shape to fix and 6,893 filenames are not a report.
            if len(report["skipped_samples"]) < 5:
                report["skipped_samples"].append(
                    {"file": msg_file.name, "timestamp": str(fm.get("timestamp", ""))}
                )
            continue

        if ts >= archive_cutoff:
            continue
        if not is_fully_acked(msg_file, acks_dir, agents, fm):
            report["held_unacked"] += 1
            continue

        month_dir = archive_dir / ts.strftime("%Y-%m")
        if not dry_run:
            month_dir.mkdir(parents=True, exist_ok=True)
            dest = month_dir / msg_file.name
            shutil.move(str(msg_file), str(dest))
            # Move associated ack files too
            msg_id = get_msg_id(msg_file)
            for ack_file in acks_dir.glob(f"{msg_id}.*.ack"):
                ack_dest = month_dir / "acks"
                ack_dest.mkdir(exist_ok=True)
                shutil.move(str(ack_file), str(ack_dest / ack_file.name))
        report["archived"].append(msg_file.name)

    # Step 3: Delete old archives
    for month_subdir in sorted(archive_dir.iterdir()):
        if not month_subdir.is_dir():
            continue
        # Parse month from dirname (YYYY-MM)
        try:
            month_dt = datetime.strptime(month_subdir.name, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        # If the entire month is older than delete cutoff, remove it
        if month_dt + timedelta(days=31) < delete_cutoff:
            count = len(list(month_subdir.glob("*.md")))
            if not dry_run:
                shutil.rmtree(str(month_subdir))
            report["deleted"].append(f"{month_subdir.name} ({count} messages)")

    # Counts
    report["active_count"] = len(list(active_dir.glob("*.md")))
    report["archive_count"] = sum(
        len(list(d.glob("*.md"))) for d in archive_dir.iterdir() if d.is_dir()
    )

    return report


def main() -> int:
    if len(sys.argv) < 2:
        from otaman_core._resolve import find_maestro_root

        project_root = find_maestro_root()
        if not project_root:
            print(
                "Usage: cleanup-bus.py [project-root] [--dry-run] "
                "[--archive-days N] [--delete-days N]",
                file=sys.stderr,
            )
            return 2
    else:
        project_root = Path(sys.argv[1]).resolve()
    dry_run = "--dry-run" in sys.argv
    archive_days = 30
    delete_days = 90

    for i, arg in enumerate(sys.argv):
        if arg == "--archive-days" and i + 1 < len(sys.argv):
            archive_days = int(sys.argv[i + 1])
        if arg == "--delete-days" and i + 1 < len(sys.argv):
            delete_days = int(sys.argv[i + 1])

    report = cleanup(project_root, archive_days, delete_days, dry_run)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
