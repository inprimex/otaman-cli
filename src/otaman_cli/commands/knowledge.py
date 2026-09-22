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
    required = ("KnowledgeEntry", "validate_entry", "load_entries", "write_entry", "is_past_due")
    return module if all(hasattr(module, n) for n in required) else None


def _knowledge_dir(root: Path):
    from otaman_core.knowledge import KNOWLEDGE_DIRNAME

    return root / ".agents" / KNOWLEDGE_DIRNAME


def _today() -> str:
    return datetime.date.today().isoformat()


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
    review_by = args.review_by
    if not review_by:
        try:
            base = datetime.date.fromisoformat(created)
        except ValueError:
            base = datetime.date.today()
        review_by = (base + datetime.timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()

    entry = core.KnowledgeEntry(
        type=args.kind,
        author=author,
        created=created,
        review_by=review_by,
        anchor=args.anchor,
        title=args.title,
        body=body,
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
    UI.muted(f"  {entry.type} · review by {entry.review_by} · anchor {entry.anchor}")
    return 0


def _list(argv: list[str], root: Path, core) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="otaman knowledge list", add_help=True)
    parser.add_argument("--type", dest="kind", default=None, choices=list(core.KINDS))
    parser.add_argument("--past-due", action="store_true", help="only entries needing review")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    entries = core.load_entries(_knowledge_dir(root))
    if args.kind:
        entries = [e for e in entries if e.type == args.kind]
    today = _today()
    if args.past_due:
        entries = [e for e in entries if core.is_past_due(e, today)]

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
        UI.ok("No knowledge entries recorded.")
        UI.muted("  otaman knowledge add --title '…' --anchor <file:line|stem|number>")
        return 0

    overdue = 0
    print()
    UI.header(f"Knowledge ({len(entries)})")
    for e in entries:
        past_due = core.is_past_due(e, today)
        overdue += 1 if past_due else 0
        flag = f"{C.YELLOW}review overdue{C.RESET}  " if past_due else ""
        print(f"  {C.GREEN}{e.type:<9}{C.RESET} {e.title}")
        print(f"      {flag}{e.created} → {e.review_by} · {e.author} · {e.anchor}")
    if overdue:
        print()
        UI.muted(f"  {overdue} entry(ies) past review — correct or retire, never silently trust.")
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
    UI.kv("  Author", entry.author)
    UI.kv("  Created", entry.created)
    UI.kv(
        "  Review by",
        entry.review_by + ("  (OVERDUE)" if core.is_past_due(entry, _today()) else ""),
    )
    UI.kv("  Anchor", entry.anchor)
    print()
    print(entry.body.strip() or "(no body)")
    return 0


def cmd_knowledge(args: list[str]) -> int:
    if not args or args[0] in {"-h", "--help", "help"}:
        UI.info("otaman knowledge <subcommand>")
        UI.muted("  add --title T --anchor A [--type ...] [--body -]   Record a durable fact")
        UI.muted("  list [--type T] [--past-due] [--json]              What we know")
        UI.muted("  show <title-substring>                             One entry in full")
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
    return _bail(f"unknown `otaman knowledge` subcommand: {sub!r}")


register(
    CommandSpec(
        name="knowledge",
        handler=cmd_knowledge,
        help="Durable agent knowledge: add | list | show",
    )
)

__all__ = ["cmd_knowledge"]
