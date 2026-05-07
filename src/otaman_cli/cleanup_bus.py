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
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def parse_frontmatter(filepath: Path) -> dict[str, Any] | None:
    """Parse YAML frontmatter from a markdown file."""
    try:
        content = filepath.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
        if not match:
            return None
        fm = yaml.safe_load(match.group(1))
        return fm if isinstance(fm, dict) else None
    except (OSError, yaml.YAMLError):
        return None


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


def parse_timestamp(fm: dict[str, Any]) -> datetime | None:
    """Parse timestamp from frontmatter."""
    ts = fm.get("timestamp", "")
    if not ts:
        return None
    ts_str = str(ts)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


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
        "active_count": 0,
        "archive_count": 0,
        "errors": [],
    }

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
            continue

        ts = parse_timestamp(fm)
        if not ts:
            continue

        if ts < archive_cutoff and is_fully_acked(msg_file, acks_dir, agents, fm):
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
        len(list(d.glob("*.md")))
        for d in archive_dir.iterdir()
        if d.is_dir()
    )

    return report


def main() -> int:
    if len(sys.argv) < 2:
        from otaman_core._resolve import find_maestro_root
        project_root = find_maestro_root()
        if not project_root:
            print("Usage: cleanup-bus.py [project-root] [--dry-run] [--archive-days N] [--delete-days N]",
                  file=sys.stderr)
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
