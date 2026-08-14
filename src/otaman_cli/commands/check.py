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
from otaman_cli.commands.status_cluster import cmd_fleet_status
from otaman_cli.identity import find_project_root, resolve_agent_identity
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
        UI.error("Not in an otaman project")
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

            to = fm.get("to", "")
            # bus-cc-routing task 2.2 — also pick up CC copies addressed to
            # someone else but with this agent in the `cc:` list (and the
            # `x-cc: true` marker indicating the bus_server wrote this copy
            # for a CC recipient).
            cc_field = fm.get("cc") or []
            is_cc_copy = bool(fm.get("x-cc")) and isinstance(cc_field, list) and (agent in cc_field)
            if to != agent and to != "all" and not is_cc_copy:
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

            messages.append(
                {
                    "id": fm.get("id", "?"),
                    "from": fm.get("from", "?"),
                    "to": str(fm.get("to", "")),
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
                    "is_cc": bool(fm.get("x-cc")),
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
            UI.bullet(
                f"{m['id']} from {UI.agent(m['from'])} "
                f"[{UI.priority(m['priority'])}]{broadcast_label}{deadline_label}"
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

    # Show blocked tasks
    blocked_file = root / ".agents" / "blocked" / f"{agent}.md"
    if blocked_file.exists():
        blocked_content = blocked_file.read_text(encoding="utf-8").strip()
        # Tombstoned entries (`otaman blocked --clear` / `blocked clear <stem>`)
        # are wrapped in `<!-- ... cleared YYYY-MM-DD — manually-cleared -->`
        # rather than deleted outright. Strip them before parsing, otherwise
        # the split below fails to find a bare "\n## Blocked: " boundary
        # (the tombstoned line reads "<!-- ## Blocked: ...", not "## Blocked:
        # ..."), so the whole file — including already-cleared entries — is
        # treated as one active block and nagged forever.
        blocked_content = re.sub(
            r"<!--.*?-->",
            "",
            blocked_content,
            flags=re.DOTALL,
        ).strip()
        if blocked_content:
            print()
            UI.blocked("BLOCKED TASKS:")
            # Parse blocked entries and check if any are now unblocked
            for block in blocked_content.split("\n## Blocked: "):
                block = block.strip()
                if not block:
                    continue
                lines = block.splitlines()
                task_title = lines[0] if lines else "?"
                # Find the proposal stem
                proposal_stem = ""
                for line in lines:
                    if line.strip().startswith("- **Proposal**:"):
                        proposal_stem = line.split(":", 1)[1].strip()
                        break

                # Check if approval + spec-change arrived
                has_approval = any(
                    m["type"] == "spec-change-approved"
                    and proposal_stem
                    and proposal_stem in m.get("subject", "")
                    for m in messages
                )
                has_spec_change = any(m["type"] == "spec-change" for m in messages)

                if has_approval and has_spec_change:
                    UI.ok(f"READY TO RESUME: {task_title}")
                    UI.ok("Specs updated — read them and continue implementation")
                elif has_approval:
                    UI.bullet(f"{task_title} — approved, waiting for spec commit...")
                else:
                    # Check for rejection
                    has_rejection = any(
                        m["type"] == "spec-change-rejected"
                        and proposal_stem
                        and proposal_stem in m.get("subject", "")
                        for m in messages
                    )
                    if has_rejection:
                        UI.error(f"REJECTED: {task_title} — read rejection reason and adapt")
                    else:
                        UI.bullet(f"{task_title} — waiting for human approval")
                if proposal_stem:
                    UI.muted(f"Proposal: {proposal_stem}")

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

    # Compact one-liner
    parts: list[str] = []
    for r in non_idle:
        tag = r.task or r.change or "—"
        parts.append(f"{r.agent} {r.state.value} ({tag})")
    print()
    UI.muted(f"Fleet: {' · '.join(parts)}")


register(
    CommandSpec(
        name="check",
        handler=cmd_check,
        help="Check pending messages for an agent (auto-detects from cwd)",
    )
)
