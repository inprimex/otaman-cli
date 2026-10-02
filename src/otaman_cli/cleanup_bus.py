#!/usr/bin/env python3
"""Archive and clean up old bus messages.

Usage:
    python cleanup-bus.py <project-root> [--dry-run] [--archive-days N] [--delete-days N]

Archives messages that are fully acked and older than archive-days (default: from
platform.yaml communication.max_age_days, or 30).

With --purge, ALSO deletes whole archive months older than delete-days (default: 90).
Irreversible, and opt-in since 20261001: without the flag those months are reported
and kept. A month this run archived into is never purged in the same invocation —
`delete_days` measures time spent AS an archived message.

Outputs JSON report of actions taken.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from otaman_core.frontmatter import parse_bus_timestamp

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


#: Ack states that END a message's life for its recipient. `read` is deliberately
#: absent: `otaman ack --read` exists to keep a message VISIBLE while the recipient
#: finishes something else, and this repo's own CLAUDE.md prescribes exactly that
#: pattern ("ack as read, add to queue, finish current task first").
TERMINAL_ACK_STATES = ("resolved", "approved", "rejected")

#: The non-terminal state, named rather than inferred from "not in the set above": a
#: reader of this module should be able to see the one word that keeps a message live.
LIVE_ACK_STATE = "read"


def ack_is_terminal(acks_dir: Path, msg_id: str, agent: str) -> bool:
    """Whether *agent* has FINISHED with this message, not merely seen it.

    The archive predicate used to ask `ack_file.exists()`, which answers "has anyone
    acked this?" when the question is "is anyone still working on it?". Those differ:
    measured by deploy-agent on the live bus (20261001T201348), **428 of the 2,494
    archivable messages are acked `read`** — deliberately kept visible — and would have
    been archived out of `otaman check` on the first successful cleanup in four months.
    The distinction `--read` exists to express would have been silently discarded by
    the command that is supposed to respect it.

    An ack whose state cannot be read, or is a word this does not recognise, is NOT
    terminal. Unreadable is not finished — the same rule this module already applies to
    an unparseable timestamp, and the safe direction when the action is irreversible
    from the operator's point of view even though the file is only moved.
    """
    ack_file = acks_dir / f"{msg_id}.{agent}.ack"
    try:
        state = ack_file.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    if not state:
        return False
    # Substring, not equality: one live ack reads "approved — already actioned: ..."
    # with the reason appended, and a state word followed by prose is still that state.
    return any(word in state for word in TERMINAL_ACK_STATES)


def head_tracked_names(directory: Path) -> set[str] | None:
    """Filenames in *directory* that git HEAD carries, or None when git cannot say.

    HEAD, not the index: what makes a deleted file recoverable is a commit. The bus
    of the program that lost messages had not been committed in six weeks, so a purge
    there was permanent in a way the operator had no way to see — the warning said
    "unrecoverable unless this directory is version-controlled" and left them to
    guess which it was.

    None (not an empty set) when the directory is outside a repo, git is absent, or
    the call fails: the caller must render that as unrecoverable, because "I could not
    check" and "there is no backup" have the same consequence and the opposite
    consequence from "all committed".
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(directory), "ls-tree", "--name-only", "HEAD", "--", "."],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def unbacked_count(month_dir: Path) -> int | None:
    """How many `*.md` files in *month_dir* git HEAD does NOT carry (None = unknown)."""
    if not month_dir.is_dir():
        return None
    tracked = head_tracked_names(month_dir)
    if tracked is None:
        return None
    return sum(1 for f in month_dir.glob("*.md") if f.name not in tracked)


def is_fully_acked(msg_path: Path, acks_dir: Path, agents: list[str], fm: dict[str, Any]) -> bool:
    """Whether every recipient has reached a TERMINAL ack state for this message."""
    to = fm.get("to", "")
    msg_id = get_msg_id(msg_path)

    if to == "all":
        # Need all developer agents to have finished with it.
        if not agents:
            return False
        return all(ack_is_terminal(acks_dir, msg_id, agent) for agent in agents)
    # Single-agent message: that agent has to have finished with it.
    return ack_is_terminal(acks_dir, msg_id, str(to))


def parse_timestamp(fm: dict[str, Any]) -> datetime | None:
    """The bus timestamp as an AWARE datetime, or None if it is genuinely malformed.

    Delegates to `otaman_core.frontmatter.parse_bus_timestamp` (core #102, homed in
    response to cli #234's single-home note). The local three-`strptime` parser this
    replaces is gone: it rejected the fractional seconds the producer's
    `datetime.now(UTC).isoformat()` emits, so `otaman cleanup` aged 98.4% of the live
    bus as unparseable and archived nothing for four and a half months.

    The wrapper survives for ONE reason, measured rather than assumed: core's parser
    returns a value with whatever tzinfo the input carried, so a naive
    `2026-08-11 20:53:12` comes back naive — and the caller compares the result
    against an aware cutoff, which raises `TypeError: can't compare offset-naive and
    offset-aware datetimes`. The live bus has no naive timestamps today (measured:
    7,076 aware, 0 naive), so this is a guard against a shape that is accepted at
    write and would crash the archive pass, not a workaround for current data. Raised
    with core, whose docstring promises "aware".
    """
    parsed = parse_bus_timestamp(fm.get("timestamp"))
    if parsed is None:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


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
    purge: bool = False,
) -> dict[str, Any]:
    """Run cleanup on the bus. Returns a report dict.

    *purge* opts into Step 3, the IRREVERSIBLE deletion of whole archive months.
    Default False, and the default is the fix: `cleanup` reads as hygiene, and on
    2026-10-01 it destroyed 591 messages on its first successful run in four months
    (deploy-agent 20261001T220034) — recovered only because that bus happened to be a
    git repo with the deletions uncommitted. The capability is unchanged; what changed
    is that it no longer happens because somebody ran the tidy-up command.
    """
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
        # Step 3 (purge) accounting. `deleted` keeps its old shape for callers that
        # read it; these are what make the destruction legible:
        #   deleted_months        — the month directories involved
        #   deleted_message_count — MESSAGES, the number that matters. The old report
        #                           counted DIRECTORIES, so 591 destroyed messages
        #                           rendered as "Deleted: 2 archive(s)" and was read as
        #                           two directories being tidied (20261001T220034).
        #   purge_withheld        — identified and NOT performed, because purging now
        #                           requires an explicit opt-in.
        #   purge_skipped_fresh   — months THIS run archived into, which it refuses to
        #                           purge in the same invocation.
        #   deleted_unbacked      — of those messages, how many git HEAD does NOT
        #                           carry, i.e. how many exist NOWHERE ELSE. The line
        #                           this replaces said "unrecoverable unless this
        #                           directory is version-controlled" — a condition the
        #                           operator cannot evaluate and the run can. On the
        #                           tenant that lost 591 messages the condition was
        #                           FALSE and nothing said so (deploy/plugin/cli
        #                           forensics 2026-10-02: 2,627 of 5,317 active
        #                           messages untracked, and the archive 0% tracked).
        #                           None means git could not answer; that reads as
        #                           unrecoverable, never as safe.
        "deleted_months": [],
        "deleted_message_count": 0,
        "deleted_unbacked": 0,
        "deleted_detail": [],
        "purge_withheld": False,
        "purge_skipped_fresh": [],
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

    # Months this invocation archives into, and how many messages it puts in each.
    # Step 3 will not purge them — see there. A COUNT rather than a set, because a dry
    # run has to report the size of a month it has not created.
    archived_into: dict[str, int] = {}

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
        # Recorded whether or not this is a dry run: Step 3 must refuse the months
        # THIS invocation archived into, and a dry run has to predict that refusal or
        # its preview is of a different operation than the real one.
        archived_into[month_dir.name] = archived_into.get(month_dir.name, 0) + 1
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

    # Step 3: PURGE old archive months — irreversible, and opt-in since 20261001.
    #
    # Two defects, one invocation, 591 messages destroyed (deploy-agent
    # 20261001T220034):
    #
    # (a) Step 3's implicit premise is that an archived month has already had
    #     `delete_days` of life AS AN ARCHIVED MONTH. That is false the first time the
    #     archiver runs after an outage: on a bus that had not archived in four months,
    #     Step 2 created 2026-05 and 2026-06 and Step 3 purged them seconds later. So
    #     a month this run archived into is skipped and SAID, not silently kept.
    #
    # (b) It happened at all because `cleanup` deletes by default. The command reads as
    #     hygiene; destroying 591 messages is not what an operator expects from it. Now
    #     it requires `purge=True`, and without it the months are reported as withheld.
    # Candidates are the months on disk UNION the months Step 2 would create. The union
    # matters only in a dry run — there, Step 2 writes nothing, so a month it would
    # create is not on disk and Step 3 could not see it. Without the union the preview
    # describes a different operation than the real run, which is how this incident was
    # read as safe in the first place.
    existing = {d.name for d in archive_dir.iterdir() if d.is_dir()}
    for month_name in sorted(existing | set(archived_into)):
        month_subdir = archive_dir / month_name
        # Parse month from dirname (YYYY-MM)
        try:
            month_dt = datetime.strptime(month_name, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        # If the entire month is older than delete cutoff, it is a purge candidate.
        if month_dt + timedelta(days=31) >= delete_cutoff:
            continue

        on_disk = len(list(month_subdir.glob("*.md"))) if month_subdir.is_dir() else 0
        # In a real run Step 2 has already moved the files, so they are counted on disk;
        # in a dry run they are not there yet and the archived count supplies them.
        count = on_disk if not dry_run else on_disk + archived_into.get(month_name, 0)

        if month_name in archived_into:
            # (a) — this run put messages here. Their retention window starts now.
            report["purge_skipped_fresh"].append(f"{month_name} ({count} messages)")
            continue

        # The stake, measured before anything is removed — and in a dry run too, so
        # the preview describes the same operation the real run would perform.
        unbacked = unbacked_count(month_subdir)
        report["deleted_months"].append(month_name)
        report["deleted_message_count"] += count
        if unbacked is None:
            stake = "git could not be consulted — treat as unrecoverable"
        elif unbacked == 0:
            stake = "all committed to git"
        else:
            stake = f"{unbacked} with NO git backup"
            report["deleted_unbacked"] += unbacked
        report["deleted_detail"].append(
            {"month": month_name, "messages": count, "unbacked": unbacked}
        )
        report["deleted"].append(f"{month_name} ({count} messages, {stake})")

        if not purge:
            # (b) — identified, not performed. The caller is told, and nothing is lost.
            report["purge_withheld"] = True
            continue
        if not dry_run:
            shutil.rmtree(str(month_subdir))

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
                "Usage: cleanup-bus.py [project-root] [--dry-run] [--purge] "
                "[--archive-days N] [--delete-days N]",
                file=sys.stderr,
            )
            return 2
    else:
        project_root = Path(sys.argv[1]).resolve()
    dry_run = "--dry-run" in sys.argv
    purge = "--purge" in sys.argv
    archive_days = 30
    delete_days = 90

    for i, arg in enumerate(sys.argv):
        if arg == "--archive-days" and i + 1 < len(sys.argv):
            archive_days = int(sys.argv[i + 1])
        if arg == "--delete-days" and i + 1 < len(sys.argv):
            delete_days = int(sys.argv[i + 1])

    report = cleanup(project_root, archive_days, delete_days, dry_run, purge=purge)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
