"""`otaman cleanup` — migrated from main.py.

--dry-run is shared with cmd_init (not yet migrated), so it stays in
main()'s shared loop and cmd_cleanup parses it independently here too --
temporary duplication until init migrates and the shared-loop copy can
be deleted (same pattern as scan/check/complete used while their
sharers were still unmigrated).
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.main import UI, run_script


def cmd_cleanup(args: list[str]) -> int:
    """Archive old bus messages and clean up."""
    dry_run = False
    positional: list[str] = []
    for a in args:
        if a == "--dry-run":
            dry_run = True
        else:
            positional.append(a)

    root = find_project_root()
    if not root:
        UI.error(not_in_project_message())
        return 1

    UI.header("Otaman Bus Cleanup")

    result = run_script(
        "cleanup-bus.py", str(root), *(["--dry-run"] if dry_run else []), capture=True
    )
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json

        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    if report.get("migrated"):
        UI.ok(f"Migrated: {report['migrated']} message(s) from flat bus/ to bus/active/")

    archived = report.get("archived", [])
    if archived:
        UI.ok(f"Archived: {len(archived)} message(s)")
        for name in archived[:10]:
            UI.muted(name)
        if len(archived) > 10:
            UI.muted(f"... and {len(archived) - 10} more")

    deleted = report.get("deleted", [])
    if deleted:
        UI.error(f"Deleted: {len(deleted)} archive(s)")
        for d in deleted:
            UI.muted(d)

    # identity-divergence 1.4 — name every reaped phantom; a status file with no
    # agents.yaml entry must never vanish silently any more than it should persist.
    orphans = report.get("status_orphans", [])
    if orphans:
        UI.error(f"Reaped: {len(orphans)} orphaned status file(s) (no agents.yaml entry)")
        for name in orphans:
            UI.muted(name)

    if not archived and not deleted and not orphans and not report.get("migrated"):
        UI.muted("Nothing to clean up.")

    UI.kv("Active", str(report.get("active_count", 0)))
    UI.kv("Archived", str(report.get("archive_count", 0)))

    if report.get("errors"):
        for e in report["errors"]:
            UI.error(e)

    if dry_run:
        UI.warn("(dry run — no changes made)")

    return 0


register(
    CommandSpec(name="cleanup", handler=cmd_cleanup, help="Archive old, fully-acked bus messages")
)
