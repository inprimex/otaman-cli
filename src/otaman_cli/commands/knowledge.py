"""`otaman knowledge add|list|show` — the verb over `.agents/knowledge/`.

The directory existed for months and stayed empty, because a convention with no
verb and no entry shape is not a place anyone puts anything. core supplies the
shape and the IO (shared-agent-memory 1.1); this is the verb.

Everything typed lives in `otaman_core.knowledge` — the schema, the anchor rule,
the past-due comparison, the parser and the writer. Nothing here re-derives any
of it. What belongs at this layer is the operator surface: resolving where the
pile lives, defaulting the fields a human should not have to retype, and saying
clearly why an entry was refused.

The anchor rule is the one worth reading twice: an entry with no evidence
anchor is REFUSED, and the refusal names the rule rather than reporting a field
error. A memory tier that accumulates unprovenanced claims is worse than an
empty one — it looks like knowledge and cannot be checked.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.main import UI, C

#: Default review horizon. Long enough not to be busywork, short enough that a
#: fact nobody re-confirmed in a quarter gets looked at rather than trusted
#: forever (the risk-register SLA pattern core's docstring cites).
DEFAULT_REVIEW_DAYS = 90


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _core():
    """core's knowledge module, or None when this install predates it."""
    try:
        from otaman_core import knowledge as module
    except Exception:  # noqa: BLE001 - absent/broken core → caller refuses cleanly
        return None
    required = (
        "KnowledgeEntry",
        "validate_entry",
        "load_entries",
        "write_entry",
        "is_past_due",
        # knowledge-v2 (core #81). Probed together so a core carrying only the v1
        # surface reads as absent rather than failing partway through `amend`.
        "amend_entry",
        "is_amended",
        "superseded_by",
        "index_line",
        "mark_accessed",
        "load_entry_by_stem",
        "in_partition",
        "active_entries",
        "KnowledgeError",
    )
    return module if all(hasattr(module, n) for n in required) else None


def _knowledge_dir(root: Path):
    from otaman_core.knowledge import KNOWLEDGE_DIRNAME

    return root / ".agents" / KNOWLEDGE_DIRNAME


def _today() -> str:
    return datetime.date.today().isoformat()


def _resolve_function(root: Path, core, explicit: str | None) -> tuple[str, str]:
    """``(function, note)`` — which knowledge partition this agent writes to.

    DERIVED, not typed per-entry. D4 says one-writer-per-partition "reuses fleet
    law, not new machinery": a partition is owned the way a repo is owned, so the
    writer's partition comes from `program.processes.knowledge.partitions` —
    the same shape as the ownership map — rather than from a flag the operator
    has to remember correctly every time.

    Until plugin's 3.1 ships that map there is nothing to derive from, so this
    falls back to `development` and RETURNS A NOTE saying so. Announced rather
    than silent, because a wrong partition is a misfiling that looks like a
    filing: it puts an entry in another agent's index and out of its owner's.
    """
    if explicit:
        return explicit, ""
    agent = ""
    try:
        from otaman_cli.identity import resolve_agent_identity

        agent = resolve_agent_identity(root) or ""
    except Exception:  # noqa: BLE001 - unresolved identity → fall through to the note
        agent = ""

    try:
        import yaml

        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
        partitions = (
            ((cfg.get("program") or {}).get("processes") or {}).get("knowledge") or {}
        ).get("partitions") or {}
    except Exception:  # noqa: BLE001 - unreadable platform.yaml → the note path
        partitions = {}

    if isinstance(partitions, dict) and partitions:
        for function, owner in partitions.items():
            if owner == agent and function in core.FUNCTIONS:
                return function, ""
        return (
            core.FUNCTION_DEVELOPMENT,
            f"{agent or 'this agent'} owns no declared knowledge partition — "
            f"filed under {core.FUNCTION_DEVELOPMENT}",
        )
    return (
        core.FUNCTION_DEVELOPMENT,
        f"no knowledge partitions declared in platform.yaml — filed under "
        f"{core.FUNCTION_DEVELOPMENT}; declare "
        f"program.processes.knowledge.partitions to scope this properly",
    )


def _default_review_by(created: str) -> str:
    """`created` + the review horizon, so neither `add` nor `amend` retypes it."""
    try:
        base = datetime.date.fromisoformat(created)
    except ValueError:
        base = datetime.date.today()
    return (base + datetime.timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()


def _add(argv: list[str], root: Path, core) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="otaman knowledge add", add_help=True)
    parser.add_argument("--type", dest="kind", default=core.KIND_LESSON, choices=list(core.KINDS))
    parser.add_argument("--title", required=True)
    parser.add_argument("--anchor", default="", help="file:line, bus stem, or a measured number")
    parser.add_argument("--body", default="", help="the fact itself; '-' reads stdin")
    parser.add_argument("--author", default=None, help="defaults to the resolved agent identity")
    parser.add_argument("--review-by", dest="review_by", default=None, help="ISO date")
    parser.add_argument("--created", default=None, help="ISO date (defaults to today)")
    parser.add_argument(
        "--function", default=None, help="knowledge partition (derived from the map by default)"
    )
    parser.add_argument(
        "--domain", default="", help="industry vocabulary term (validated against the registry)"
    )
    args = parser.parse_args(argv)

    body = args.body
    if body == "-":
        import sys

        body = sys.stdin.read()

    author = args.author
    if not author:
        from otaman_cli.identity import resolve_agent_identity

        author = resolve_agent_identity(root) or ""

    created = args.created or _today()
    review_by = args.review_by or _default_review_by(created)

    # `domain:` is the PROGRAM's vocabulary, so it is validated here and not by
    # core (kv2 2.3). Refusal names the registry; acceptance without a registry
    # says so rather than implying the term was checked.
    from otaman_cli import vocabulary

    domain_ok, domain_note = vocabulary.check_domain(root, getattr(args, "domain", ""))
    if not domain_ok:
        UI.error("Refusing to record this entry:")
        for line in domain_note.splitlines():
            UI.muted(f"  {line}" if line.startswith("  ") else f"  - {line}")
        return 2

    function, fn_note = _resolve_function(root, core, getattr(args, "function", None))
    entry = core.KnowledgeEntry(
        type=args.kind,
        author=author,
        created=created,
        review_by=review_by,
        anchor=args.anchor,
        title=args.title,
        body=body,
        function=function,
        domain=(getattr(args, "domain", "") or "").strip(),
    )

    errors = core.validate_entry(entry)
    if errors:
        UI.error("Refusing to record this entry:")
        for err in errors:
            UI.muted(f"  - {err}")
        # The anchor rule gets its own sentence: it is the standard this tier
        # exists to hold, not one validation among several.
        if any("anchor" in e for e in errors):
            UI.muted("")
            UI.muted("  An entry needs evidence someone can check later — the file and line")
            UI.muted("  it was observed at, the bus message that reported it, or the number")
            UI.muted("  that was measured. Pass it with --anchor.")
        return 2

    if args.anchor and not core.anchor_is_recognized(args.anchor):
        # Advisory, not a refusal: the hard rule is presence.
        UI.warn(f"anchor {args.anchor!r} is not a recognised shape — recording it anyway.")

    path = core.write_entry(_knowledge_dir(root), entry)

    # Round-trip check: does the entry read back as what we asked to record?
    #
    # core's renderer writes frontmatter values UNQUOTED, so a value carrying a
    # YAML-significant character is silently transformed. A title like
    # "worktree sessions need core #73" loses everything from the `#` — YAML
    # reads it as a comment — and the file still parses cleanly, so the entry
    # looks recorded and is quietly wrong. Durable memory that mangles what it
    # was told is worse than memory that refuses.
    #
    # This compares rather than re-renders: writing a second renderer here would
    # be the duplicate single-home forbids, and it would only cover the escapes I
    # happened to think of. A comparison catches any of them, including the next
    # one. Reported upstream; this stays as the guard at the door.
    written = core.parse_entry(path.read_text(encoding="utf-8"))
    mangled = [
        field
        for field in ("type", "author", "created", "review_by", "anchor", "title")
        if getattr(written, field, None) != getattr(entry, field)
    ]
    if mangled:
        path.unlink(missing_ok=True)
        UI.error("Refusing to record this entry — it does not survive being written.")
        for field in mangled:
            UI.muted(f"  {field}: asked for {getattr(entry, field)!r}")
            UI.muted(f"  {' ' * len(field)}  read back {getattr(written, field, None)!r}")
        UI.muted("")
        UI.muted("  A frontmatter value carrying ':' or '#' is reinterpreted by YAML.")
        UI.muted("  Rephrase it (write 'PR 73' rather than '#73'), or quote it.")
        return 2

    UI.ok(f"Recorded: {path.relative_to(root)}")
    UI.muted(
        f"  {entry.type} · {entry.function} · review by {entry.review_by} · anchor {entry.anchor}"
    )
    if fn_note:
        UI.warn(f"  {fn_note}")
    if domain_note:
        UI.warn(f"  {domain_note}")
    return 0


def _list(argv: list[str], root: Path, core) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="otaman knowledge list", add_help=True)
    parser.add_argument("--type", dest="kind", default=None, choices=list(core.KINDS))
    parser.add_argument("--past-due", action="store_true", help="only entries needing review")
    parser.add_argument(
        "--function", default=None, choices=list(core.FUNCTIONS), help="one partition only"
    )
    parser.add_argument(
        "--state", default=None, choices=list(core.STATES), help="one lifecycle state only"
    )
    parser.add_argument(
        "--all-states",
        action="store_true",
        help="include dormant and retired (default: active only)",
    )
    parser.add_argument(
        "--budget", type=int, default=None, help="cap the rows shown (index token budget)"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    all_entries = core.load_entries(_knowledge_dir(root))
    entries = all_entries
    # What the reader was filtering BY, so an empty result can say which filter
    # matched nothing rather than claiming the pile is empty.
    applied: list[str] = []

    # ACTIVE-only by default: the index preloads active entries, never dormant
    # or retired ones — that is the token-scoping the change exists for. The
    # default is stated in the footer so a missing entry is explained rather
    # than mysterious.
    if args.state:
        entries = [e for e in entries if e.state == args.state]
        applied.append(f"state {args.state}")
    elif not args.all_states:
        entries = core.active_entries(entries)
        # The DEFAULT is a filter, so it has to name itself when it is the
        # reason nothing matched — otherwise the empty message reads
        # "No entries match  — 1 recorded in total", which is the blank-filter
        # bug plugin-agent reported one release ago, reintroduced by a default.
        applied.append(f"state {core.STATE_ACTIVE} (default)")
    if args.function:
        entries = core.in_partition(entries, args.function)
        applied.append(f"partition {args.function}")
    if args.kind:
        entries = [e for e in entries if e.type == args.kind]
        applied.append(f"type {args.kind}")
    today = _today()
    if args.past_due:
        entries = [e for e in entries if core.is_past_due(e, today)]
        applied.append("past review")

    if args.json:
        import json

        print(
            json.dumps(
                [
                    {
                        "type": e.type,
                        "title": e.title,
                        "author": e.author,
                        "created": e.created,
                        "review_by": e.review_by,
                        "anchor": e.anchor,
                        "state": e.state,
                        "function": e.function,
                        "domain": e.domain,
                        "supersedes": e.supersedes,
                        "amended": core.is_amended(e, all_entries),
                        "past_due": core.is_past_due(e, today),
                    }
                    for e in entries
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not entries:
        # An empty FILTER is not an empty SURFACE (plugin-agent 20260923T191515).
        # Reporting "nothing recorded" when five entries exist asserts something
        # false about the pile and sends the reader to fix a problem they do not
        # have. Which of the two is true decides the message.
        if all_entries:
            UI.ok(
                f"No entries match {' and '.join(applied)} — {len(all_entries)} recorded in total."
            )
            UI.muted("  otaman knowledge list        (all of them)")
            return 0
        UI.ok("No knowledge entries recorded.")
        UI.muted("  otaman knowledge add --title '…' --anchor <file:line|stem|number>")
        return 0

    shown = entries
    clipped = 0
    if args.budget is not None and args.budget >= 0 and len(shown) > args.budget:
        clipped = len(shown) - args.budget
        shown = shown[: args.budget]

    overdue = 0
    amended = 0
    print()
    UI.header(f"Knowledge ({len(entries)})")
    for e in shown:
        past_due = core.is_past_due(e, today)
        overdue += 1 if past_due else 0
        # `is_amended` is DERIVED by scanning who supersedes this entry, so the
        # flag is computed against the WHOLE pile, not the filtered view — an
        # entry corrected by a dormant or other-partition entry is still amended.
        is_amended = core.is_amended(e, all_entries)
        amended += 1 if is_amended else 0
        # core's index_line is the one format every surface renders (D5).
        print(f"  {core.index_line(e, amended=is_amended)}")
        flag = f"{C.YELLOW}review overdue{C.RESET}  " if past_due else ""
        print(f"      {flag}{e.created} → {e.review_by} · {e.author} · {e.anchor}")
    print()
    if clipped:
        # Never a silent truncation: a budget that hides rows without saying so
        # reads as "that is all there is".
        UI.muted(f"  {clipped} more not shown (--budget {args.budget}); raise it or filter.")
    if overdue:
        UI.muted(f"  {overdue} entry(ies) past review — correct or retire, never silently trust.")
    if amended:
        UI.muted(
            f"  {amended} entry(ies) carry {core.AMENDED_FLAG} — a correction supersedes them."
        )
    if not (args.state or args.all_states):
        UI.muted("  active only — --all-states to include dormant and retired.")
    return 0


def _show(argv: list[str], root: Path, core) -> int:
    if not argv:
        return _bail("Usage: otaman knowledge show <title-or-stem-substring>", code=2)
    needle = " ".join(argv).strip().lower()
    entries = core.load_entries(_knowledge_dir(root))
    hits = [e for e in entries if needle in e.title.lower() or needle in e.stem.lower()]
    if not hits:
        return _bail(f"No knowledge entry matches {needle!r}.")
    if len(hits) > 1:
        UI.error(f"{len(hits)} entries match {needle!r}:")
        for e in hits[:8]:
            UI.muted(f"  - {e.title}")
        return 1
    entry = hits[0]
    print()
    UI.header(entry.title)
    UI.kv("  Type", entry.type)
    UI.kv("  State", entry.state)
    UI.kv("  Partition", entry.function or "(none)")
    if entry.domain:
        UI.kv("  Domain", entry.domain)
    UI.kv("  Author", entry.author)
    UI.kv("  Created", entry.created)
    UI.kv(
        "  Review by",
        entry.review_by + ("  (OVERDUE)" if core.is_past_due(entry, _today()) else ""),
    )
    UI.kv("  Anchor", entry.anchor)

    # BOTH directions of the edge (2.1). A reader who lands on the stale entry
    # first is the exact failure this change exists to fix — plugin-agent's
    # week-one lesson went stale in four hours and `list` showed the correction
    # as an unrelated row. So the superseded entry must point FORWARD to its
    # correction, not only the correction backward.
    correcting = core.superseded_by(entries, entry.stem)
    if correcting:
        print()
        UI.warn(f"  {core.AMENDED_FLAG} superseded by:")
        for stem in correcting:
            later = core.load_entry_by_stem(_knowledge_dir(root), stem)
            UI.muted(f"    {stem}" + (f"  —  {later.title}" if later else ""))
        UI.muted("    read that before acting on this one.")
    if entry.supersedes:
        print()
        UI.muted(f"  corrects: {entry.supersedes}")
        earlier = core.load_entry_by_stem(_knowledge_dir(root), entry.supersedes)
        if earlier:
            UI.muted(f"    {earlier.title}")

    print()
    print(entry.body.strip() or "(no body)")

    # Reinforcement: reading an entry is the signal that keeps it out of the
    # decay sweep. Best-effort — a read-only knowledge dir must still SHOW.
    try:
        core.mark_accessed(_knowledge_dir(root), entry.stem, _today())
    except Exception:  # noqa: BLE001 - failing to record a read must not fail the read
        pass
    return 0


def _amend(argv: list[str], root: Path, core) -> int:
    """`otaman knowledge amend <stem>` — record a correction as an EDGE (2.1).

    The superseded entry is never rewritten or deleted. Its bytes are the
    reader-level record of what was believed at the time, and a knowledge base
    whose past can be edited cannot be trusted about what it said last week —
    the same argument that made console-undo write an inverse entry rather than
    erase one.

    So this writes ONE new entry carrying a `supersedes:` pointer, and the
    `[amended]` flag on the old one is DERIVED by scanning for who supersedes it.
    One file means the edge cannot be half-written.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="otaman knowledge amend", add_help=True)
    parser.add_argument("stem", help="the entry this correction supersedes")
    parser.add_argument("--title", required=True)
    parser.add_argument("--anchor", default="")
    parser.add_argument("--body", default="", help="the correction; '-' reads stdin")
    parser.add_argument("--type", dest="kind", default=None, choices=list(core.KINDS))
    parser.add_argument("--author", default=None)
    parser.add_argument("--review-by", dest="review_by", default=None)
    args = parser.parse_args(argv)

    directory = _knowledge_dir(root)
    superseded = core.load_entry_by_stem(directory, args.stem)
    if superseded is None:
        # Named, and it lists what IS there — a stem typo is the common case and
        # "no such entry" alone leaves the operator guessing which.
        return _bail(
            f"No knowledge entry {args.stem!r} to amend.\n"
            "  otaman knowledge list        (the stems are the filenames without .md)",
            code=2,
        )

    body = args.body
    if body == "-":
        import sys

        body = sys.stdin.read()

    author = args.author
    if not author:
        from otaman_cli.identity import resolve_agent_identity

        author = resolve_agent_identity(root) or ""

    created = _today()
    review_by = args.review_by or _default_review_by(created)
    correcting = core.KnowledgeEntry(
        # The correction inherits the superseded entry's TYPE unless told
        # otherwise: a correction to a lesson is a lesson, and making the
        # operator restate it invites a silent reclassification.
        type=args.kind or superseded.type,
        author=author,
        created=created,
        review_by=review_by,
        anchor=args.anchor,
        title=args.title,
        body=body,
        # Inherit the superseded entry's partition when it HAS one; a pre-v2
        # entry carries `function=""`, which validation rejects — so inheriting
        # blindly would make every correction to a legacy entry impossible.
        function=superseded.function or _resolve_function(root, core, None)[0],
        domain=superseded.domain,
    )

    errors = core.validate_entry(correcting)
    if errors:
        UI.error("Refusing to record this correction:")
        for err in errors:
            UI.muted(f"  - {err}")
        if any("anchor" in e for e in errors):
            UI.muted("")
            UI.muted("  A correction needs its own evidence — what changed your mind.")
        return 2

    try:
        path = core.amend_entry(directory, args.stem, correcting)
    except core.KnowledgeError as exc:
        # core raises when the edge would dangle. Surfaced, never a traceback.
        return _bail(str(exc), code=2)

    written = core.parse_entry(path.read_text(encoding="utf-8"))
    if written is None or written.supersedes != args.stem:
        path.unlink(missing_ok=True)
        return _bail(
            "Refusing to record this correction — the edge did not survive being "
            f"written (supersedes read back as {getattr(written, 'supersedes', None)!r}).",
            code=2,
        )

    UI.ok(f"Recorded correction: {path.relative_to(root)}")
    UI.muted(f"  supersedes {args.stem}")
    UI.muted(f"  the superseded entry is unchanged and now renders {core.AMENDED_FLAG}")
    return 0


def _partitions(root: Path) -> dict[str, str] | None:
    """`{function: owner}` from platform.yaml, or None when unset.

    None and `{}` mean the same thing here and both must stay distinguishable
    from "checked, none found" downstream — see `ownership_violations`.
    """
    try:
        import yaml

        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
        node = ((cfg.get("program") or {}).get("processes") or {}).get("knowledge") or {}
        raw = node.get("partitions") or {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001 - unreadable config → not-checked, never "clean"
        return None


def _sweep(argv: list[str], root: Path, core) -> int:
    """`otaman knowledge sweep [--apply] [--restore <stem>]` (knowledge-v2 2.2).

    Reports by default and mutates only under `--apply`, because a verb that
    retires things as a side effect of being asked a question is one nobody can
    afford to run to find out.
    """
    from otaman_cli import knowledge_health as health

    apply = "--apply" in argv
    rest = [a for a in argv if a != "--apply"]
    restore = ""
    if "--restore" in rest:
        i = rest.index("--restore")
        if i + 1 >= len(rest):
            return _bail("Usage: otaman knowledge sweep --restore <stem>", code=2)
        restore = rest[i + 1]

    directory = _knowledge_dir(root)

    if restore:
        if core.load_entry_by_stem(directory, restore) is None:
            return _bail(f"No knowledge entry with stem {restore!r} to restore.")
        core.set_state(directory, restore, core.STATE_ACTIVE)
        UI.ok(f"Restored {restore} to active.")
        return 0

    entries = core.load_entries(directory)
    decayed = health.decay_candidates(entries, _today())
    drifted = health.out_of_band_edits(directory, core)
    owned = health.ownership_violations(entries, _partitions(root))

    print()
    UI.header("Knowledge sweep")

    if not decayed:
        UI.muted("  Decay: no active entry is past review-by and unread since.")
    else:
        UI.warn(f"  Decay: {len(decayed)} entr{'y' if len(decayed) == 1 else 'ies'} unreinforced")
        for f in decayed:
            UI.muted(f"    - {f.stem}")
            UI.muted(f"      {f.detail}")

    if drifted:
        UI.warn(f"  Out-of-band: {len(drifted)} entr{'y' if len(drifted) == 1 else 'ies'} edited")
        for f in drifted:
            UI.muted(f"    - {f.stem}: {f.detail}")

    for f in owned:
        if f.kind == health.NOT_CHECKED:
            UI.warn(f"  Ownership: NOT CHECKED — {f.detail}")
            UI.muted(f"    {f.remedy}")
        else:
            UI.warn(f"  Ownership: {f.stem} — {f.detail}")

    if not apply:
        if decayed:
            print()
            UI.muted("  Nothing was changed. `otaman knowledge sweep --apply` moves the")
            UI.muted("  decayed entries to dormant; `otaman knowledge show <stem>` keeps one")
            UI.muted("  active by reinforcing it.")
        return 0

    moved = 0
    for f in decayed:
        if core.set_state(directory, f.stem, core.STATE_DORMANT) is not None:
            moved += 1
            UI.muted(f"    dormant: {f.stem} — {f.detail}")
    print()
    if moved:
        UI.ok(f"Moved {moved} entr{'y' if moved == 1 else 'ies'} to dormant (reversible:")
        UI.muted("  `otaman knowledge sweep --restore <stem>`). Only `state:` changed.")
    else:
        UI.muted("  Nothing to move.")
    return 0


def cmd_knowledge(args: list[str]) -> int:
    if not args or args[0] in {"-h", "--help", "help"}:
        UI.info("otaman knowledge <subcommand>")
        UI.muted("  add --title T --anchor A [--type ...] [--body -]   Record a durable fact")
        UI.muted("  list [--type T] [--past-due] [--json]              What we know")
        UI.muted("  show <title-substring>                             One entry in full")
        UI.muted("  amend <stem> --title T --anchor A                  Correct an entry (an edge)")
        return 0 if args else 1

    core = _core()
    if core is None:
        return _bail(
            "otaman knowledge needs a newer otaman-core (it hosts the knowledge "
            "schema and IO as of shared-agent-memory). Update the bundle — "
            "`otaman upgrade` — then retry.",
            code=2,
        )

    root = find_project_root()
    if root is None:
        return _bail(not_in_project_message())

    sub, rest = args[0], args[1:]
    if sub == "add":
        return _add(rest, root, core)
    if sub == "list":
        return _list(rest, root, core)
    if sub == "show":
        return _show(rest, root, core)
    if sub == "amend":
        return _amend(rest, root, core)
    if sub == "sweep":
        return _sweep(rest, root, core)
    return _bail(f"unknown `otaman knowledge` subcommand: {sub!r}")


register(
    CommandSpec(
        name="knowledge",
        handler=cmd_knowledge,
        help="Durable agent knowledge: add | list | show | amend | sweep",
    )
)

__all__ = ["cmd_knowledge"]
