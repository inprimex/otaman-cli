"""`otaman blocked` — migrated from main.py.

List, clear, or register blocked-task entries for the current agent.
Previously relied on main()'s shared flag loop to parse `--list`/
`--clear VALUE`/`--blocked-by NAME` before calling in with the parsed
values as keyword args; now parses its own flags from the raw argv
(F021/F022: one fewer command routed through the shared loop that
silently swallows unknown flags).
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import UI


def cmd_blocked(args: list[str]) -> int:
    """List, clear, or register blocked tasks for the current agent.

    `otaman blocked --list`              — list blocked entries (current agent)
    `otaman blocked --clear <slug>`      — remove a blocked entry (current agent)
    `otaman blocked clear <stem>`        — tombstone any matching entry across
                                            ALL agents' files by Proposal stem
                                            (auto-clear-blocked-entries 2.1)
    `otaman blocked <slug> [--blocked-by NAME]`  — register a new blocked entry
                                                    and set status (1.8)
    """
    list_mode = False
    clear_slug = ""
    blocked_by: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--list":
            list_mode = True
            i += 1
        elif args[i] == "--clear" and i + 1 < len(args):
            clear_slug = args[i + 1]
            i += 2
        elif args[i] == "--blocked-by" and i + 1 < len(args):
            blocked_by = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # auto-clear-blocked-entries task 2.1 — `otaman blocked clear <stem>`
    # subcommand: search all `.agents/blocked/*.md` for entries whose
    # `- **Proposal**: <stem>` line matches, and tombstone them with
    # reason `manually-cleared`.  Idempotent (no-match exits 0).
    if len(positional) >= 2 and positional[0] == "clear":
        return _cmd_blocked_clear_by_stem(root, positional[1])

    agent = resolve_agent_identity(root) or "unknown-agent"
    blocked_file = root / ".agents" / "blocked" / f"{agent}.md"

    if list_mode:
        if not blocked_file.is_file():
            print("No blocked tasks.")
            return 0
        text = blocked_file.read_text(encoding="utf-8")
        sections = re.findall(
            r"^## Blocked: (.+?)$(.*?)(?=^## Blocked:|\Z)",
            text, re.MULTILINE | re.DOTALL,
        )
        if not sections:
            print("No blocked tasks.")
            return 0
        for slug, body in sections:
            slug = slug.strip()
            since = ""
            m = re.search(r"\*\*Blocked since\*\*:\s*(.+)", body)
            if m:
                since = f"  (since {m.group(1).strip()})"
            print(f"{slug}{since}")
            proposal_m = re.search(r"\*\*Proposal\*\*:\s*(.+)", body)
            if proposal_m:
                UI.muted(f"  proposal: {proposal_m.group(1).strip()}")
        return 0

    if clear_slug:
        if not blocked_file.is_file():
            UI.muted(f"No blocked task found: {clear_slug}")
            return 0
        text = blocked_file.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^## Blocked: {re.escape(clear_slug)}\s*\n.*?(?=^## Blocked:|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        new_text = pattern.sub("", text).rstrip("\n")
        if new_text == text.rstrip("\n"):
            UI.muted(f"No blocked task found: {clear_slug}")
            return 0
        blocked_file.write_text(new_text + "\n" if new_text else "", encoding="utf-8")
        UI.ok(f"Cleared blocked task: {clear_slug}")
        return 0

    # agent-status-presence task 1.8 — register a new blocked entry +
    # set status. `otaman blocked <slug> [--blocked-by NAME]`.
    if positional:
        slug = positional[0].strip()
        if not slug:
            UI.error("Empty blocked slug")
            return 1
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        by = blocked_by or "human"
        entry = (
            f"## Blocked: {slug}\n"
            f"- **Blocked since**: {now_iso}\n"
            f"- **Blocked by**: {by}\n"
        )
        blocked_file.parent.mkdir(parents=True, exist_ok=True)
        existing = blocked_file.read_text(encoding="utf-8") if blocked_file.is_file() else ""
        if f"## Blocked: {slug}" in existing:
            UI.muted(f"Already blocked: {slug} (no change)")
        else:
            new_text = (existing.rstrip("\n") + "\n\n" + entry) if existing.strip() else entry
            blocked_file.write_text(new_text, encoding="utf-8")
            UI.ok(f"Registered blocked task: {slug}")
            UI.muted(f"  blocked_by: {by}")

        # Status hook — write blocked state
        _status_hook_after_blocked(root, agent, slug, by)
        return 0

    UI.error("Specify --list, --clear <slug>, or pass a slug to register")
    UI.muted("  otaman blocked --list")
    UI.muted("  otaman blocked --clear <slug>")
    UI.muted("  otaman blocked <slug> [--blocked-by NAME]")
    return 1


def _cmd_blocked_clear_by_stem(root: Path, stem: str) -> int:
    """auto-clear-blocked-entries task 2.1 — manual escape hatch.

    Scan every file under ``.agents/blocked/`` for entries whose
    ``- **Proposal**: <stem>`` line matches the given stem.  Tombstone each
    match by wrapping the entry block in an HTML comment with a
    ``cleared YYYY-MM-DD — manually-cleared`` trailer.  Idempotent: an
    already-commented entry is naturally skipped by the line-leading
    ``^## Blocked:`` regex.

    Returns 0 always — no-match is NOT an error (task 2.2: idempotent).
    """
    stem = (stem or "").strip()
    if not stem:
        UI.error("clear requires a proposal stem")
        UI.muted("  Usage: otaman blocked clear <proposal-stem>")
        return 1

    blocked_dir = root / ".agents" / "blocked"
    if not blocked_dir.is_dir():
        print(f"No blocked entry found for stem: {stem}")
        return 0

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Same regex shape as plugin-agent's `_auto_tombstone_blocked` in
    # bus_server.py, kept in sync deliberately so the tombstone format
    # is identical regardless of which agent / which trigger fired it.
    entry_re = re.compile(
        r"^(## Blocked: .+?)(?=\n## Blocked: |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    proposal_field_re = re.compile(
        r"^\s*-\s*\*\*Proposal\*\*:\s*(\S+)", re.MULTILINE,
    )
    title_re = re.compile(r"^## Blocked:\s*(.+)$", re.MULTILINE)

    tombstoned: list[tuple[str, str]] = []   # (agent, title)

    for blocked_file in sorted(blocked_dir.glob("*.md")):
        agent_name = blocked_file.stem
        try:
            text = blocked_file.read_text(encoding="utf-8")
        except OSError:
            continue

        modified = False
        new_parts: list[str] = []
        last_end = 0
        for m in entry_re.finditer(text):
            entry_block = m.group(1)
            new_parts.append(text[last_end:m.start()])

            prop_m = proposal_field_re.search(entry_block)
            if prop_m and prop_m.group(1) == stem:
                title_m = title_re.search(entry_block)
                title = title_m.group(1).strip() if title_m else "(untitled)"
                tombstoned.append((agent_name, title))
                trailer = f"\ncleared {today} — manually-cleared -->"
                new_parts.append("<!-- " + entry_block.rstrip() + trailer)
                modified = True
            else:
                new_parts.append(entry_block)
            last_end = m.end()

        new_parts.append(text[last_end:])

        if modified:
            try:
                blocked_file.write_text("".join(new_parts), encoding="utf-8")
            except OSError as exc:
                UI.warn(f"Failed to write {blocked_file}: {exc}")

    if not tombstoned:
        print(f"No blocked entry found for stem: {stem}")
        return 0

    for agent_name, title in tombstoned:
        UI.ok(f"Cleared: {agent_name} — {title}")
    return 0


def _status_hook_after_blocked(root: Path, agent: str, slug: str, by: str) -> None:
    """agent-status-presence task 1.8 — write `blocked` status after `otaman blocked <slug>`."""
    try:
        from otaman_cli.status import (
            AgentStatus, State, get_backend, is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    backend = get_backend(root)
    existing = backend.read(agent)
    since = existing.since if (existing and existing.state == State.BLOCKED) else now_iso
    # Preserve existing task/change so the operator sees what triggered the block
    task = existing.task if existing else slug
    change = existing.change if existing else None
    try:
        backend.write(AgentStatus(
            agent=agent, state=State.BLOCKED,
            task=task, change=change, blocked_by=by,
            since=since, updated_at=now_iso,
        ))
    except Exception:
        pass


register(CommandSpec(
    name="blocked",
    handler=cmd_blocked,
    help="List blocked tasks for the current agent",
))
