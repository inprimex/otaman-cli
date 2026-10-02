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
from otaman_cli.scripts import run_script
from otaman_cli.ui import UI

#: Exit code for "this run could not examine most of its input". Distinct from 1 so a
#: caller can tell it from an ordinary failure, and from 0 because a run that read a
#: minority of the bus has not done the job its name claims.
EXIT_MOSTLY_UNEXAMINED = 3


def _render_backup_stake(report: dict) -> None:
    """Say how much of what the purge destroys exists NOWHERE ELSE.

    This replaces "Unrecoverable unless this directory is version-controlled" — true,
    and a condition the operator cannot evaluate at the moment of deciding while the
    run can. On the tenant that lost 591 messages the condition was FALSE (nothing
    committed for six weeks) and no line said so; the day's forensics then measured
    2,627 of 5,317 active messages and 100% of the archive untracked.

    Three states, never collapsed: all committed (restorable), some/all absent from
    HEAD (gone for good), and git could not be consulted — which reads as
    unrecoverable, because "I could not check" has the same consequence as "no
    backup" and the opposite one from "all committed".
    """
    detail = report.get("deleted_detail") or []
    unknown = [d["month"] for d in detail if d.get("unbacked") is None]
    unbacked = report.get("deleted_unbacked", 0)
    if unbacked:
        UI.error(
            f"  {unbacked} of those message(s) are NOT in git HEAD — deleting them is "
            "permanent, with nothing to restore them from."
        )
    if unknown:
        UI.warn(
            "  git could not be consulted for "
            + ", ".join(unknown)
            + " — treat those as unrecoverable."
        )
    if not unbacked and not unknown and detail:
        UI.muted("  All of them are committed to git HEAD and restorable from it.")


def _examined_remainder(report: dict) -> int:
    """Messages that were read and simply did not qualify — young, or already archived.

    Derived rather than reported, because the report counts OUTCOMES and this needs the
    denominator: everything with a readable timestamp that the archive pass looked at
    and left alone. `active_count` is post-run, so the archived ones are no longer in
    it; the skipped ones still are, and are subtracted.
    """
    active = report.get("active_count", 0)
    skipped = report.get("skipped_unparseable", 0) + report.get("skipped_no_frontmatter", 0)
    held = report.get("held_unacked", 0)
    return max(0, active - skipped - held)


def cmd_cleanup(args: list[str]) -> int:
    """Archive old bus messages and clean up."""
    dry_run = False
    purge = False
    positional: list[str] = []
    for a in args:
        if a == "--dry-run":
            dry_run = True
        elif a == "--purge":
            purge = True
        else:
            positional.append(a)

    root = find_project_root()
    if not root:
        UI.error(not_in_project_message())
        return 1

    UI.header("Otaman Bus Cleanup")

    result = run_script(
        "cleanup-bus.py",
        str(root),
        *(["--dry-run"] if dry_run else []),
        *(["--purge"] if purge else []),
        capture=True,
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

    # MOVED and DESTROYED are reported as different operations, in that order, with the
    # destruction last so it is the thing left on screen.
    #
    # The old output said `Deleted: 2 archive(s)` for 591 destroyed messages, under a
    # truncated `Archived:` block, in the past tense during a dry run. deploy-agent read
    # it as two directories being tidied, told Roman the operation was recoverable, and
    # was wrong (20261001T220034). The count was of DIRECTORIES; the number that matters
    # is messages, and the word that was missing is "permanently".
    verb = "Would archive" if dry_run else "Archived"
    archived = report.get("archived", [])
    if archived:
        UI.ok(f"{verb}: {len(archived)} message(s) (moved, recoverable)")
        for name in archived[:10]:
            UI.muted(name)
        if len(archived) > 10:
            UI.muted(f"... and {len(archived) - 10} more")

    fresh = report.get("purge_skipped_fresh", [])
    if fresh:
        UI.muted(
            f"Kept {len(fresh)} month(s) this run archived into — their retention "
            "starts now: " + ", ".join(fresh)
        )

    months = report.get("deleted_months", [])
    n_msgs = report.get("deleted_message_count", 0)
    if months:
        where = ", ".join(report.get("deleted", [])) or ", ".join(months)
        if report.get("purge_withheld"):
            UI.warn(
                f"WITHHELD: {n_msgs} message(s) in {len(months)} month(s) are past "
                f"--delete-days and were NOT deleted: {where}"
            )
            UI.muted("  Pass --purge to delete them. This is irreversible.")
        else:
            destroyed = "Would DELETE PERMANENTLY" if dry_run else "DELETED PERMANENTLY"
            UI.error(f"{destroyed}: {n_msgs} message(s) in {len(months)} month(s): {where}")
        _render_backup_stake(report)

    # identity-divergence 1.4 — name every reaped phantom; a status file with no
    # agents.yaml entry must never vanish silently any more than it should persist.
    orphans = report.get("status_orphans", [])
    if orphans:
        UI.error(f"Reaped: {len(orphans)} orphaned status file(s) (no agents.yaml entry)")
        for name in orphans:
            UI.muted(name)

    # no-silent-success: what the run DECLINED TO EXAMINE is part of its result.
    # `otaman cleanup` printed "Nothing to clean up." over 6,980 active messages
    # for four and a half months, because a broken timestamp parse skipped 98.4%
    # of them with a bare `continue` (deploy-agent root-cause 20261001T142130).
    # The sentence was true. It was true of 113 messages out of 7,006, and
    # nothing said so.
    skipped = report.get("skipped_unparseable", 0)
    no_fm = report.get("skipped_no_frontmatter", 0)
    if skipped or no_fm:
        UI.warn(
            f"NOT EXAMINED: {skipped} message(s) with an unparseable timestamp"
            + (f", {no_fm} with no frontmatter" if no_fm else "")
        )
        for sample in report.get("skipped_samples", []):
            UI.muted(f"{sample.get('timestamp', '')!r}  in {sample.get('file', '')}")
        UI.muted("These reached neither the age check nor the ack check.")

    held = report.get("held_unacked", 0)
    if held:
        # The question that arrives right after the parse fix lands.
        UI.muted(f"Old enough to archive but waiting on an ack: {held}")

    if not archived and not months and not orphans and not report.get("migrated"):
        if skipped or no_fm:
            # Never the bare sentence when input was discarded: "nothing to do"
            # and "could not look at most of it" are opposite facts.
            UI.muted("Nothing archivable among the messages that COULD be examined.")
        else:
            UI.muted("Nothing to clean up.")

    UI.kv("Active", str(report.get("active_count", 0)))
    UI.kv("Archived", str(report.get("archive_count", 0)))

    if report.get("errors"):
        for e in report["errors"]:
            UI.error(e)

    if dry_run:
        UI.warn("(dry run — no changes made)")

    # deploy-agent's second ask (20261001T192057), which I declined in #234 and was
    # wrong to: exit non-zero when the run examined a MINORITY of its input.
    #
    # Not a percentage threshold — those are arbitrary and argued about. The rule is
    # "I looked at less than half of what I was given", which separates a data
    # condition from a tool failure. With core refusing a valueless timestamp at write
    # (core #102), the only way to reach that state is a parser/producer mismatch:
    # exactly the four-month silence this closes, where 6,893 of 7,006 were discarded
    # and the command still exited 0. On this bus today it is 7 of 7,084, which is
    # data, and stays exit 0.
    examined = (
        len(report.get("archived", []))
        + report.get("held_unacked", 0)
        + _examined_remainder(report)
    )
    if skipped + no_fm > examined:
        UI.error(
            f"Examined a MINORITY of the bus: {examined} message(s) read, "
            f"{skipped + no_fm} skipped. A parser that cannot read what the writer "
            "emits reports 'nothing to clean up' over a bus that is never cleaned."
        )
        return EXIT_MOSTLY_UNEXAMINED

    return 0


register(
    CommandSpec(
        name="cleanup",
        handler=cmd_cleanup,
        help="Archive old, fully-acked bus messages (--purge also DELETES old archive months)",
    )
)
