"""`otaman check` — migrated from main.py.

Previously relied on main()'s shared flag loop to pre-parse
--hide-broadcast-older-than into a keyword arg before calling in. Folded
that parsing into cmd_check itself (F021/F022: it was check-exclusive, so
the flag-loop branch and variable are removed entirely, not just
duplicated).
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.commands.bus_messaging import _file_is_for_agent
from otaman_cli.commands.status_cluster import cmd_fleet_status
from otaman_cli.identity import find_project_root, not_in_project_message, resolve_agent_identity
from otaman_cli.main import UI, C, _get_agent_ack_status, _resolve_bus_paths


def cmd_check(args: list[str]) -> int:
    """Check messages for an agent."""
    hide_broadcast_hours: int | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--hide-broadcast-older-than" and i + 1 < len(args):
            try:
                hide_broadcast_hours = int(args[i + 1])
            except ValueError:
                UI.warn(
                    f"--hide-broadcast-older-than expects an integer (hours); "
                    f"ignoring '{args[i + 1]}'"
                )
            i += 2
        else:
            positional.append(args[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error(not_in_project_message())
        return 1

    # Determine agent: explicit arg → CWD→repo→owner → .agents/current-agent
    agent = resolve_agent_identity(root, explicit=positional[0] if positional else None)
    if not agent:
        UI.error("No agent specified and identity could not be resolved.")
        UI.muted(
            "  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), "
            ".agents/current-agent"
        )
        UI.muted(
            "  Fix: set OTAMAN_AGENT env var, or run 'otaman init --update' "
            "to write per-repo .otaman files"
        )
        UI.muted("Usage: otaman check <agent-name>")
        return 1

    try:
        import yaml
    except ImportError:
        UI.error("PyYAML required. Install with: pip install pyyaml")
        return 2

    active_dir, acks_dir = _resolve_bus_paths(root)

    if not active_dir.is_dir():
        print("No messages - bus directory doesn't exist yet.")
        return 0

    UI.header(f"Messages for: {agent}")

    # Parse messages
    messages = []
    total = {"pending": 0, "read": 0, "resolved": 0}
    # cofounder-agent bug report 20260811T202643: a file check cannot parse
    # used to be skipped SILENTLY — a trust-critical delivery gap (a pending
    # message no one knows exists). Collect and warn instead.
    unparseable: list[str] = []

    # Resolved once, outside the loop: `otaman check` iterates the whole active
    # dir, and the gate's probe is not worth repeating per message.
    from otaman_cli.frontmatter_gate import frontmatter

    _fm_api = frontmatter()

    for f in sorted(active_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                unparseable.append(f.name)
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                unparseable.append(f.name)
                continue

            # Per-file recipient designation lives in the FILENAME, not the
            # frontmatter: CC fan-out copies all carry the full `cc:` list,
            # so an `agent in cc` test surfaces every OTHER recipient's copy
            # too — permanently-pending noise this agent can never ack
            # (`otaman ack` correctly refuses non-own copies). Use the same
            # predicate ack uses so check and ack agree on ownership; it
            # keeps the comma-tolerant `to:` fallback for legacy
            # multi-recipient files (notify-change-fanout) and `to: all`
            # broadcasts.
            if not _file_is_for_agent(f.stem, fm, agent):
                continue

            # Per-agent status from ack files
            status = _get_agent_ack_status(f.stem, agent, acks_dir)
            total[status] = total.get(status, 0) + 1

            # Extract subject
            subject = ""
            body_start = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            for line in body_start.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break

            # task-sequencing-contract 1.2 — advisory waiting-state for
            # sequenced assignments with unsatisfied depends-on.
            seq_waiting = None
            if fm.get("type") == "task-assignment" and fm.get("depends-on"):
                from otaman_cli.sequencing import waiting_annotation

                seq_waiting = waiting_annotation(fm, body_start)

            messages.append(
                {
                    "id": fm.get("id", "?"),
                    "from": fm.get("from", "?"),
                    "to": str(fm.get("to", "")),
                    "seq_waiting": seq_waiting,
                    "priority": fm.get("priority", "normal"),
                    "type": fm.get("type", "?"),
                    "status": status,
                    "timestamp": str(fm.get("timestamp", "")),
                    "subject": subject,
                    "file": f.name,
                    "stem": f.stem,
                    # inter-agent-request-response-contract (tasks 2.1, 2.2)
                    "expects_response": bool(fm.get("expects-response")),
                    "response_effort": fm.get("response-effort"),
                    "response_deadline": fm.get("response-deadline"),
                    "reply_to": fm.get("reply-to"),
                    # bus-cc-routing task 2.2 — `x-cc: true` marks a CC copy
                    "is_cc": _fm_api.is_cc_copy(fm),
                }
            )
        except (OSError, yaml.YAMLError):
            unparseable.append(f.name)
            continue

    # bus-cc-routing task 2.2 — partition CC copies into their own bucket so
    # the primary-messages section stays focused. CC copies still respect the
    # status filter (pending vs read/resolved).
    primary_messages = [m for m in messages if not m.get("is_cc")]
    cc_messages = [m for m in messages if m.get("is_cc")]

    # Display pending first, then others (primary only — CC has its own section)
    pending = [m for m in primary_messages if m["status"] == "pending"]
    other = [m for m in primary_messages if m["status"] != "pending"]
    cc_pending = [m for m in cc_messages if m["status"] == "pending"]
    cc_other = [m for m in cc_messages if m["status"] != "pending"]

    # Apply --hide-broadcast-older-than filter (D4)
    if hide_broadcast_hours is not None and hide_broadcast_hours > 0:
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hide_broadcast_hours)

        def _is_old_broadcast(m: dict) -> bool:
            if m.get("to") != "all":
                return False
            ts_str = m.get("timestamp", "")
            if not ts_str:
                return False
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                return ts < cutoff
            except ValueError:
                return False

        pending = [m for m in pending if not _is_old_broadcast(m)]

    # Task 2.1: tiebreaker sort within priority band — expects-response,
    # response-effort, timestamp. See response_contract.make_sort_key.
    from otaman_cli.response_contract import (
        deadline_is_imminent as _deadline_imminent,
    )
    from otaman_cli.response_contract import (
        make_sort_key as _sort_key,
    )

    pending.sort(key=_sort_key)

    if pending:
        for m in pending:
            broadcast_label = " (broadcast)" if m.get("to") == "all" else ""
            # Task 2.2: surface [DEADLINE] indicator for imminent response-deadline
            deadline_label = ""
            if _deadline_imminent(m.get("response_deadline")):
                deadline_label = f" {C.RED}[DEADLINE {m['response_deadline']}]{C.RESET}"
            waiting_label = ""
            if m.get("seq_waiting"):
                waiting_label = f" {C.YELLOW}[{m['seq_waiting']}]{C.RESET}"
            UI.bullet(
                f"{m['id']} from {UI.agent(m['from'])} "
                f"[{UI.priority(m['priority'])}]{broadcast_label}{deadline_label}{waiting_label}"
            )
            print(f"    {m['subject']}")
            UI.muted(f"{m['type']} | {m['timestamp']} | {m['stem']}")
            print()
    else:
        UI.muted("No pending messages.")
        print()

    if other:
        read_count = sum(1 for m in other if m["status"] == "read")
        resolved_count = sum(1 for m in other if m["status"] == "resolved")
        UI.muted(f"Also: {read_count} read, {resolved_count} resolved")

    # bus-cc-routing task 2.2 — CC (copies) section, ONLY when present.
    # Visually lighter than primary: `·` bullet instead of `*`, includes
    # the `to` field so the reader sees who the primary recipient was.
    if cc_pending or cc_other:
        print()
        UI.muted("CC (copies):")
        for m in cc_pending:
            broadcast_label = " (broadcast)" if m.get("to") == "all" else ""
            print(
                f"  · {m['id']} to {UI.agent(m['to'])} from "
                f"{UI.agent(m['from'])} [{UI.priority(m['priority'])}]{broadcast_label}"
            )
            if m.get("subject"):
                print(f"      {m['subject']}")
            UI.muted(f"    {m['type']} | {m['timestamp']} | {m['stem']}")
        if cc_other:
            cc_read = sum(1 for m in cc_other if m["status"] == "read")
            cc_resolved = sum(1 for m in cc_other if m["status"] == "resolved")
            UI.muted(f"  Also (CC): {cc_read} read, {cc_resolved} resolved")

    if unparseable:
        print()
        UI.warn(
            f"{len(unparseable)} file(s) in the active bus could not be parsed "
            "and are NOT listed above:"
        )
        for name in unparseable[:5]:
            UI.muted(f"  - {name}")
        if len(unparseable) > 5:
            UI.muted(f"  ... and {len(unparseable) - 5} more")
        UI.muted("  One of them may be a message addressed to you. Inspect the file directly.")

    # spec-gate-hardening 1.4 — the current agent's OWN changes awaiting human
    # approval/ratification: the spec-approval-pending items it enqueued at
    # propose time (they're addressed `to: human`, so they never appear in the
    # agent's normal pending list — surface them here by `from == agent`).
    awaiting: list[tuple[str, str]] = []
    if active_dir.is_dir():
        for f in sorted(active_dir.glob("*spec-approval-pending*.md")):
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            try:
                fm = yaml.safe_load(fm_match.group(1))
            except yaml.YAMLError:
                continue
            if not isinstance(fm, dict) or fm.get("from") != agent:
                continue
            subject = ""
            body_start = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            for line in body_start.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break
            awaiting.append((subject or f.stem, f.stem))
    # F3: a spec-approval-pending item exists only for propose-flow SCRs — an
    # AUTHORED change awaiting the human's ratification (the original pmeets ask,
    # and every spec-agent change) has no such item. Also derive those from the
    # specs repo: not-research, not-yet-approved changes this agent owns
    # (spec_owner == agent, or requested_by names it).
    try:
        from otaman_cli.commands.spec import _specs_changes_dir

        changes_dir = _specs_changes_dir(root)
    except Exception:  # noqa: BLE001 - specs stack unavailable → skip this source
        changes_dir = None
    if changes_dir is not None:
        from otaman_core.spec_lifecycle import has_approval, is_research, read_openspec

        for d in sorted(p for p in changes_dir.iterdir() if p.is_dir() and p.name != "archive"):
            data = read_openspec(d / ".openspec.yaml")
            if not data or is_research(data) or has_approval(data):
                continue  # research / already-approved → not awaiting ratification
            owner = data.get("spec_owner")
            requested_by = str(data.get("requested_by") or "")
            if owner == agent or (agent and agent in requested_by):
                stage = data.get("stage") or "—"
                awaiting.append((f"{d.name} — awaiting ratification (stage={stage})", d.name))
    if awaiting:
        print()
        UI.header("Your changes awaiting approval/ratification")
        for subject, stem in awaiting:
            UI.bullet(f"{subject}  ({stem})")

    # Show blocked tasks (blocked-entry-lifecycle 1.4)
    blocked_file = root / ".agents" / "blocked" / f"{agent}.md"
    if blocked_file.exists():
        from otaman_cli.blocked_gate import blocked_entries

        mod = blocked_entries()
        if mod is None:
            return  # `check` must still run; the blocked section is omitted
        parse_entries, stale_reason = mod.parse_entries, mod.stale_reason

        # Parsing (and tombstone recognition) via the ONE parser — this used to
        # be a bespoke `<!--.*?-->` strip plus a `\n## Blocked: ` split, one of
        # the five divergent implementations that let the surface drift.
        entries = parse_entries(blocked_file.read_text(encoding="utf-8"))
        if entries:
            known = _known_refs(root)
            marked = [(e, stale_reason(e, known_refs=known)) for e in entries]
            live = [e for e, reason in marked if not reason]
            stale = [(e, reason) for e, reason in marked if reason]

            print()
            # The banner distinguishes live from stale: a list that is mostly
            # stale trains agents to ignore the one surface meant to tell them
            # they are genuinely blocked, so the count has to say which is which.
            headline = f"BLOCKED TASKS: {len(live)} live"
            if stale:
                headline += f", {len(stale)} stale"
            UI.blocked(headline)

            for entry in live:
                stem = entry.proposal
                has_approval = any(
                    m["type"] == "spec-change-approved" and stem and stem in m.get("subject", "")
                    for m in messages
                )
                has_spec_change = any(m["type"] == "spec-change" for m in messages)
                has_rejection = any(
                    m["type"] == "spec-change-rejected" and stem and stem in m.get("subject", "")
                    for m in messages
                )
                if has_approval and has_spec_change:
                    UI.ok(f"READY TO RESUME: {entry.display_title}")
                    UI.ok("Specs updated — read them and continue implementation")
                elif has_approval:
                    UI.bullet(f"{entry.display_title} — approved, waiting for spec commit...")
                elif has_rejection:
                    UI.error(f"REJECTED: {entry.display_title} — read rejection reason and adapt")
                else:
                    UI.bullet(f"{entry.display_title} — waiting for human approval")
                if stem:
                    UI.muted(f"Proposal: {stem}")

            for entry, reason in stale:
                UI.bullet(f"[stale] {entry.display_title}")
                UI.muted(f"  {reason}")
            if stale:
                UI.muted("  Stale entries are reported, never auto-removed — clear with")
                UI.muted("  `otaman blocked clear <stem>` once you've confirmed they're done.")

    UI.kv(
        "Summary",
        f"{total.get('pending', 0)} pending | {total.get('read', 0)} read | "
        f"{total.get('resolved', 0)} resolved",
    )
    if pending:
        UI.muted("Use `otaman read <msg-stem>` to read a message")
        UI.muted("Use `otaman ack <msg-stem>` to acknowledge a message")

    # agent-status-presence task 1.10 — fleet section.
    _check_render_fleet(root)

    return 0


def _known_refs(root: Path) -> set[str]:
    """Every ref a blocked entry could legitimately point at (1.4).

    Two sources, both searched including their archives so a completed item is
    "resolved", not "vanished":
      * bus message stems — what an approval wait's ``**Proposal**:`` names;
      * change directory names — what a dependency wait's ``**Change**:`` names.

    Best-effort by design: a source that cannot be read contributes nothing, and
    an entry is only called stale when NOTHING can account for it. Under-reading
    here would mislabel live blocks as stale, which is the more damaging error —
    so every failure degrades toward "live".
    """
    refs: set[str] = set()
    try:
        active_dir, _ = _resolve_bus_paths(root)
        bus_dir = active_dir.parent if active_dir.name == "active" else active_dir
        for path in bus_dir.rglob("*.md"):
            refs.add(path.stem)
    except Exception:  # noqa: BLE001 - unreadable bus → contributes nothing
        pass
    try:
        from otaman_cli.commands.spec import _specs_changes_dir

        changes_dir = _specs_changes_dir(root)
        if changes_dir is not None:
            for d in changes_dir.iterdir():
                if d.is_dir() and d.name != "archive":
                    refs.add(d.name)
            archive = changes_dir / "archive"
            if archive.is_dir():
                for d in archive.iterdir():
                    if d.is_dir():
                        refs.add(d.name)
                        # archived dirs are date-prefixed (2026-09-09-<slug>);
                        # a dependency wait names the bare slug.
                        parts = d.name.split("-", 3)
                        if len(parts) == 4:
                            refs.add(parts[3])
    except Exception:  # noqa: BLE001 - specs stack unavailable → contributes nothing
        pass
    return refs


def _check_render_fleet(root: Path) -> None:
    """Append fleet summary to `otaman check` output.

    Per design Q4:
      - Omit section entirely when all agents idle OR agent_presence is false
      - One-line compact summary when any agent is non-idle but none blocked
      - Full table when any agent is blocked
    """
    try:
        from otaman_cli.status import State, get_backend, is_agent_presence_enabled
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return
    try:
        records = get_backend(root).read_all()
    except NotImplementedError:
        return
    except Exception:
        return
    non_idle = [r for r in records if r.state != State.IDLE]
    if not non_idle:
        return

    has_blocked = any(r.state == State.BLOCKED for r in records)
    if has_blocked:
        # Full table — reuse the fleet command for consistency
        print()
        cmd_fleet_status([])
        return

    # Compact one-liner. Renders through the SHARED staleness rule so `check`
    # and `status` can never disagree about who is alive (status-heartbeat 1.2).
    from otaman_cli.status.staleness import is_stale, last_seen, render_state, ttl_seconds

    ttl = ttl_seconds(root)
    parts: list[str] = []
    for r in non_idle:
        if is_stale(r, ttl=ttl):
            # The claim and the last-seen time, not just the word "stale".
            parts.append(f"{r.agent} STALE (was {r.state.value}, {last_seen(r)})")
        else:
            tag = r.task or r.change or "—"
            parts.append(f"{r.agent} {render_state(r, ttl=ttl)} ({tag})")
    print()
    UI.muted(f"Fleet: {' · '.join(parts)}")


register(
    CommandSpec(
        name="check",
        handler=cmd_check,
        help="Check pending messages for an agent (auto-detects from cwd)",
    )
)
