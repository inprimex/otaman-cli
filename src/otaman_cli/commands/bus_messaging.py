"""`otaman send`/`read`/`ack`/`assign` — migrated from main.py.

MESSAGE_TYPES and _OUTCOME_SUBJECT_RE were send-exclusive; _status_hook_after_ack
and _parse_task_and_change_from_body were ack-exclusive; _write_spec_owner
was assign-exclusive (its read/write pair partner, _read_spec_owner, already
lives in commands/complete.py) -- all moved here with their commands.

_read_platform_specs_path stays in main.py: _write_spec_owner needs it, and
so does commands/complete.py's _read_spec_owner, so it's a genuinely shared
utility, not exclusive to any one migrated command.
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_core.validate_message import PRIVILEGED_TYPES

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import UI, C, _read_platform_specs_path, _resolve_bus_paths, run_script

# outcome-proposal-routing task 3.1 — message-type registry for `otaman send`
# validation.  Keep this list lean: deliberately limited to types that have
# bus-server / CLI / downstream-agent semantics today.  Adding a new type is
# a spec-level change.
#
# F012 (security GAP finding, 2026-07-04): `spec-change-approved` and
# `spec-change-rejected` are deliberately ABSENT here even though otaman-core's
# `validate_message.py` VALID_TYPES includes them — they're PRIVILEGED_TYPES
# (assert a human decision was made) and must only be producible via
# `otaman approve`'s TTY-gated confirmation, never the general send path.
# See the PRIVILEGED_TYPES check in cmd_send below.
MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "info",
        "question",
        "task-assignment",
        "task-complete",
        "spec-change",
        "spec-change-request",
        "contract-change",
        "review-request",
        "proposal",
        "outcome-proposal",
    }
)

_PRIVILEGED_TYPE_HINTS: dict[str, str] = {
    "spec-change-approved": "Use `otaman approve approve <stem>` instead.",
    "spec-change-rejected": "Use `otaman approve reject <stem>` instead.",
    "emergency-halt": "Use `otaman emergency-halt` instead.",
    "human-decision": "Use `otaman hitl take <stem>` instead.",
}

# outcome-proposal-routing task 3.2 — subject-line keywords that suggest
# the operator probably meant `--type outcome-proposal` instead of the
# default `--type info`.
_OUTCOME_SUBJECT_RE = re.compile(r"outcome|proposal|business impact", re.IGNORECASE)


def cmd_send(args: list[str]) -> int:
    """Send a bus message to another agent (mirrors otaman_send MCP tool).

    Usage:
      otaman send <to> --subject "..." --body "..." [--type TYPE] [--priority P]

    `to` is the recipient agent name, "all" for broadcast, or "human".
    """
    import argparse

    parser = argparse.ArgumentParser(prog="otaman send", add_help=False)
    parser.add_argument("to", nargs="?")
    parser.add_argument("--subject", required=False)
    parser.add_argument("--body", required=False)
    parser.add_argument("--type", dest="msg_type", default="info")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--from", dest="explicit_from")
    parser.add_argument(
        "--cc",
        action="append",
        default=None,
        metavar="AGENT",
        help="add a CC recipient; repeat for multiple (bus-cc-routing task 2.1)",
    )
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        UI.muted(
            'Usage: otaman send <to> --subject "..." --body "..." '
            "[--type info|question|task-assignment|...] [--priority low|normal|high|urgent]"
        )
        return 2

    if not ns.to or not ns.subject or not ns.body:
        UI.error("send requires <to>, --subject, and --body")
        UI.muted(
            'Usage: otaman send <to> --subject "..." --body "..." [--type ...] [--priority ...]'
        )
        return 2

    # F012 — privileged types assert a human decision; forging one defeats
    # the platform's HITL guarantee.  Reject them from the general send path
    # entirely, with a pointer to the dedicated, TTY-gated command that can
    # actually produce them — checked BEFORE the general registry lookup
    # below so the error is directed, not a generic "unknown type".
    if ns.msg_type in PRIVILEGED_TYPES:
        UI.error(
            f"'{ns.msg_type}' is a privileged message type and cannot be sent via `otaman send`."
        )
        UI.muted(
            "  "
            + _PRIVILEGED_TYPE_HINTS.get(
                ns.msg_type,
                "This type asserts a human decision and requires a dedicated command.",
            )
        )
        return 2

    # outcome-proposal-routing task 3.1 — validate message type against the
    # registry.  Unknown types are rejected outright (typo guard); the
    # spec-listed types are accepted.
    if ns.msg_type not in MESSAGE_TYPES:
        UI.error(f"Unknown message type {ns.msg_type!r}.")
        UI.muted("  Allowed types: " + ", ".join(sorted(MESSAGE_TYPES)))
        return 2

    # outcome-proposal-routing task 3.2 — subject-pattern nudge.  When
    # --type info is used with a subject that looks like an outcome
    # statement, emit a non-blocking warning suggesting --type
    # outcome-proposal.  Do NOT block the send.
    if ns.msg_type == "info" and _OUTCOME_SUBJECT_RE.search(ns.subject or ""):
        UI.warn(
            "Subject looks like an outcome/proposal — consider "
            "`--type outcome-proposal` so strategic agents (CPO, cofounder) "
            "are auto-CC'd via routing rules."
        )

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    agent = resolve_agent_identity(root, explicit=ns.explicit_from)
    if not agent:
        UI.error("Agent identity could not be resolved.")
        UI.muted(
            "  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), "
            ".agents/current-agent"
        )
        UI.muted(
            "  Fix: set OTAMAN_AGENT env var, or run 'otaman init --update' "
            "to write per-repo .otaman files"
        )
        UI.muted("Tip: run from inside a managed repo, or pass --from <agent>")
        return 1

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")
    ts_iso = now.isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", ns.subject.lower())[:40].strip("-")
    filename = f"{ts}-{agent}-to-{ns.to}-{slug}.md"

    # cli-send-cc-fanout-parity (tasks 1.1-1.5) — compute the effective CC
    # list as the union of explicit --cc and routing-rule-derived CC, then
    # write per-recipient copies after the primary.  Ported from
    # bus_server.py:157-283 so cmd_send and the MCP otaman_send produce
    # byte-identical on-disk files.
    from otaman_cli.cc_fanout import (
        cc_copy_filename,
        compute_effective_cc,
        inject_x_cc,
        load_routing_rules,
    )

    routing_rules = load_routing_rules(root)
    # Strip + drop empties from --cc values (a UX nicety; the ported
    # compute_effective_cc preserves whitespace-only entries because
    # bus_server.py:227 doesn't strip).  cmd_send historically stripped
    # so keep that behavior at the CLI boundary.
    stripped_cc = [c.strip() for c in (ns.cc or []) if isinstance(c, str) and c.strip()]
    effective_cc = compute_effective_cc(
        to=ns.to,
        priority=ns.priority,
        explicit_cc=stripped_cc,
        routing_rules=routing_rules,
        msg_type=ns.msg_type,
    )

    cc_line = f"cc: [{', '.join(effective_cc)}]\n" if effective_cc else ""
    content = (
        f"---\n"
        f"id: {ts}-{agent[:8]}\n"
        f"from: {agent}\n"
        f"to: {ns.to}\n"
        f"{cc_line}"
        f"priority: {ns.priority}\n"
        f"type: {ns.msg_type}\n"
        f"timestamp: {ts_iso}\n"
        f"status: pending\n"
        f"---\n"
        f"\n"
        f"## Subject: {ns.subject}\n"
        f"\n"
        f"{ns.body}\n"
    )

    active_dir, _acks_dir = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    msg_path = active_dir / filename
    msg_path.write_text(content, encoding="utf-8")

    # Per-CC copies (task 1.5): one extra file per effective_cc recipient,
    # frontmatter augmented with `x-cc: true`.  Stem includes the recipient
    # so each agent's `otaman check` glob picks up its copy.
    cc_copy_paths: list[Path] = []
    if effective_cc:
        cc_content = inject_x_cc(content)
        for recipient in effective_cc:
            cc_fname = cc_copy_filename(
                timestamp=ts,
                from_agent=agent,
                cc_recipient=recipient,
                slug=slug,
            )
            cc_path = active_dir / cc_fname
            cc_path.write_text(cc_content, encoding="utf-8")
            cc_copy_paths.append(cc_path)

    UI.ok(f"Sent: {filename}")
    UI.kv("  From", agent)
    UI.kv("  To", ns.to)
    if effective_cc:
        UI.kv("  CC", ", ".join(effective_cc))
    UI.kv("  Type", ns.msg_type)
    UI.kv("  Priority", ns.priority)
    UI.muted(f"  Path: {msg_path.relative_to(root)}")
    if cc_copy_paths:
        UI.muted(f"  CC copies: {len(cc_copy_paths)} written (x-cc: true)")
        for p in cc_copy_paths:
            UI.muted(f"    {p.relative_to(root)}")
    return 0


def cmd_read(args: list[str]) -> int:
    """Read the full content of a specific bus message.

    Usage:
      otaman read <message-stem>

    The <message-stem> is the filename without .md (as shown in
    `otaman check` output). Substring match accepted when unambiguous.
    Searches active/ first, then archive/YYYY-MM/.
    """
    if not args:
        UI.error("read requires a message stem")
        UI.muted("Usage: otaman read <message-stem>")
        UI.muted("Tip: get the stem from `otaman check` output")
        return 2

    stem = args[0]
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    active_dir, _acks_dir = _resolve_bus_paths(root)

    # 1. Exact match in active/
    candidate = active_dir / f"{stem}.md"
    if candidate.is_file():
        msg_file = candidate
    else:
        # 2. Substring match in active/
        matches = list(active_dir.glob(f"*{stem}*.md"))
        if not matches:
            # 2b. Token-based fallback: agents sometimes pass partial stems
            # like "20260426T15164601-tasks-gitlab-cicd-pipeline" when the
            # legacy: real file may have -maestro-to-backend-agent- in the middle. Match
            # each dash-separated token with wildcards between them.
            tokens = [tok for tok in stem.split("-") if tok]
            if len(tokens) >= 2:
                pattern = "*" + "*".join(tokens) + "*.md"
                matches = list(active_dir.glob(pattern))
        if len(matches) == 1:
            msg_file = matches[0]
        elif len(matches) > 1:
            UI.error(f"Ambiguous stem '{stem}'. Matches:")
            for m in matches[:5]:
                UI.muted(f"  - {m.stem}")
            if len(matches) > 5:
                UI.muted(f"  ... and {len(matches) - 5} more")
            return 1
        else:
            # 3. Try archive/YYYY-MM/
            archive_root = active_dir.parent / "archive"
            archive_matches: list[Path] = []
            if archive_root.is_dir():
                for month_dir in archive_root.iterdir():
                    if month_dir.is_dir():
                        archive_matches.extend(month_dir.glob(f"*{stem}*.md"))
            if len(archive_matches) == 1:
                msg_file = archive_matches[0]
                UI.muted(f"  (found in archive: {msg_file.parent.name})")
            elif len(archive_matches) > 1:
                UI.error(f"Ambiguous in archive: {[m.stem for m in archive_matches[:5]]}")
                return 1
            else:
                UI.error(f"Message not found: {stem}")
                UI.muted(f"  Searched: {active_dir.relative_to(root)} + archive/*/")
                return 1

    # Print the full message content as-is (frontmatter + body)
    print(msg_file.read_text(encoding="utf-8"))
    return 0


def cmd_ack(args: list[str]) -> int:
    """Acknowledge a bus message for the current agent."""
    status = "resolved"
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--read":
            status = "read"
            i += 1
        elif args[i] == "--resolved":
            status = "resolved"
            i += 1
        else:
            positional.append(args[i])
            i += 1
    args = positional

    if not args:
        UI.error("Message identifier required")
        UI.muted("Usage: otaman ack <msg-stem-or-partial> [--read | --resolved]")
        UI.muted("  msg-stem: filename without .md (shown in 'otaman check' output)")
        UI.muted("  --read: mark as read (will still show in pending-ish view)")
        UI.muted("  --resolved: mark as resolved (default)")
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # Determine agent: CWD→repo→owner → .agents/current-agent
    agent = resolve_agent_identity(root)
    if not agent:
        UI.error("No agent identity set.")
        UI.muted(
            "  Set OTAMAN_AGENT env var, or run 'otaman init --update' "
            "to write per-repo .otaman agent: fields"
        )
        return 1

    active_dir, acks_dir = _resolve_bus_paths(root)
    acks_dir.mkdir(parents=True, exist_ok=True)

    pattern = args[0]
    # Find matching message(s) - support partial match
    matches = []
    if active_dir.is_dir():
        for f in active_dir.glob("*.md"):
            if pattern in f.stem or pattern == f.stem:
                matches.append(f)

    # Token-based fallback: split input by dashes and glob between tokens.
    # Handles the "logical reconstruction" stem form
    # (e.g. "20260426T15164601-tasks-gitlab-cicd-pipeline" when the real
    # legacy: filename may have "-maestro-to-backend-agent-" in the middle).
    if not matches and "-" in pattern and active_dir.is_dir():
        tokens = [tok for tok in pattern.split("-") if tok]
        if len(tokens) >= 2:
            glob_pattern = "*" + "*".join(tokens) + "*.md"
            matches = list(active_dir.glob(glob_pattern))

    # Frontmatter-id fallback: scan every .md file's YAML frontmatter and
    # match against the `id:` field. The id field is what's shown at the
    # top of each `otaman check` entry, so agents that copy from there
    # arrive with this form (e.g. "20260409T224058-3aeed02" where 3aeed02
    # is a short hash that doesn't appear in the filename).
    if not matches and active_dir.is_dir():
        import re as _re

        for f in active_dir.glob("*.md"):
            try:
                head = f.read_text(encoding="utf-8")[:512]
                fm_id_match = _re.search(r"^id:\s*(\S+)", head, _re.MULTILINE)
                if fm_id_match and (
                    fm_id_match.group(1) == pattern or pattern in fm_id_match.group(1)
                ):
                    matches.append(f)
            except (OSError, UnicodeDecodeError):
                continue

    if not matches:
        UI.error(f"No message matching '{pattern}' in active bus")
        UI.muted("Tip: paste the full file stem from the bottom line of each `otaman check` entry,")
        UI.muted("     OR the frontmatter `id:` value from the top line.")
        return 1

    # fswatch-agent bug report 20260814T213000: a partial stem matching
    # several distinct messages used to ack ALL of them — including copies
    # addressed to other agents (stray cross-agent sidecar files in acks/).
    # First restrict candidates to files that are actually *this* agent's
    # copy, then reject remaining ambiguity the same way `otaman read` does.
    mine = [f for f in matches if _file_is_for_agent(f.stem, _read_frontmatter(f), agent)]

    if not mine:
        UI.error(f"{len(matches)} match(es) for '{pattern}', but none are addressed to {agent}:")
        for m in matches[:5]:
            UI.muted(f"  - {m.stem}")
        if len(matches) > 5:
            UI.muted(f"  ... and {len(matches) - 5} more")
        UI.muted("  Acks are per-agent; each recipient acks its own copy.")
        return 1

    if len(mine) > 1:
        UI.error(f"Ambiguous stem '{pattern}'. Matches:")
        for m in mine[:5]:
            UI.muted(f"  - {m.stem}")
        if len(mine) > 5:
            UI.muted(f"  ... and {len(mine) - 5} more")
        UI.muted("  Be more specific — paste the full stem from `otaman check`.")
        return 1

    matches = mine

    # Task 2.3 advisory: when resolving a message that expects a response,
    # warn if no outbound reply with reply-to: <this-id> exists.  Do not block.
    if status == "resolved":
        import yaml as _yaml

        from otaman_cli.response_contract import has_outbound_reply as _has_reply

        for msg_file in matches:
            try:
                head = msg_file.read_text(encoding="utf-8")[:2048]
            except OSError:
                continue
            fm_match = re.match(r"^---\n(.+?)\n---", head, re.DOTALL)
            if not fm_match:
                continue
            try:
                fm = _yaml.safe_load(fm_match.group(1))
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            if not fm.get("expects-response"):
                continue
            msg_id = str(fm.get("id") or msg_file.stem)
            if _has_reply(active_dir, in_reply_to_id=msg_id, from_agent=agent):
                continue
            UI.warn(
                "this message expects a response but no reply has been sent. "
                "Ack as 'read' instead, or send a reply first."
            )
            UI.muted(f"  message: {msg_file.stem}")

    for msg_file in matches:
        ack_file = acks_dir / f"{msg_file.stem}.{agent}.ack"
        ack_file.write_text(status + "\n", encoding="utf-8")
        UI.ok(f"Acked: {msg_file.stem} -> {status}")

    # agent-status-presence task 1.6 — when a `task-assignment` is acked,
    # auto-write `working` status with task/change parsed from the message.
    # Best-effort: any parsing failure leaves task/change null.
    _status_hook_after_ack(root, agent, matches)

    return 0


def _read_frontmatter(path: Path) -> dict:
    """Parse a message file's YAML frontmatter; {} on any failure."""
    try:
        head = path.read_text(encoding="utf-8")[:2048]
    except (OSError, UnicodeDecodeError):
        return {}
    m = re.match(r"^---\n(.+?)\n---", head, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml

        fm = yaml.safe_load(m.group(1))
    except Exception:
        return {}
    return fm if isinstance(fm, dict) else {}


def _file_is_for_agent(stem: str, fm: dict, agent: str) -> bool:
    """Is this on-disk message file *agent*'s own copy?

    The per-file recipient lives in the FILENAME, not the frontmatter: CC
    fan-out copies keep the original ``to:`` in frontmatter and carry the
    full ``cc:`` list, so frontmatter alone over-matches other recipients'
    copies. Naming shapes in the live bus:

      primary:            <ts>-<from>-to-<recipient>-<slug>
      cc copy (current):  <ts>-<from>-to-<cc-recipient>-<slug>   + x-cc: true
      cc copy (legacy):   <ts>-<from>-to-<orig-to>-cc-<cc-recipient>-<slug>
      broadcast:          to: all in frontmatter
    """
    if f"-cc-{agent}-" in stem or stem.endswith(f"-cc-{agent}"):
        return True  # legacy cc naming — my copy
    if fm.get("x-cc"):
        if "-cc-" in stem:
            return False  # legacy cc naming — another recipient's copy
        return f"-to-{agent}-" in stem or stem.endswith(f"-to-{agent}")
    if f"-to-{agent}-" in stem or stem.endswith(f"-to-{agent}"):
        return True
    # Comma-tolerant frontmatter fallback: legacy notify-change files carry
    # a multi-recipient "a, b, c" to: field (notify-change-fanout).
    to_list = [t.strip() for t in str(fm.get("to", "")).split(",") if t.strip()]
    return agent in to_list or "all" in to_list


def _status_hook_after_ack(root: Path, agent: str, msg_files: list[Path]) -> None:
    """agent-status-presence task 1.6 — set `working` after acking a task-assignment.

    For each acked message: if `type: task-assignment`, parse task + change from
    the body (best-effort) and write a `working` status record.  Multiple
    task-assignments in one ack call → first one wins (rare path; matches
    spec's "fires when acked message is type: task-assignment" wording).
    """
    try:
        from otaman_cli.status import (
            AgentStatus,
            State,
            get_backend,
            is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    import yaml as _yaml

    for f in msg_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
        if not fm_match:
            continue
        try:
            fm = _yaml.safe_load(fm_match.group(1))
        except Exception:
            continue
        if not isinstance(fm, dict) or fm.get("type") != "task-assignment":
            continue

        body = text[fm_match.end() :] if fm_match else ""
        task, change = _parse_task_and_change_from_body(body)
        backend = get_backend(root)
        existing = backend.read(agent)
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        # State change → reset since; same state → preserve.
        since = existing.since if (existing and existing.state == State.WORKING) else now_iso
        try:
            backend.write(
                AgentStatus(
                    agent=agent,
                    state=State.WORKING,
                    task=task,
                    change=change,
                    since=since,
                    updated_at=now_iso,
                )
            )
        except Exception:
            pass
        return  # first task-assignment in this ack batch is enough


def _parse_task_and_change_from_body(body: str) -> tuple[str | None, str | None]:
    """Best-effort: pull task + change from a task-assignment body.

    Heuristic order (matches plugin-agent's task-assignment templates):
      1. `**Task:** <N.M ...>` or `**Tasks:** <N.M ...>`
      2. `**Change:** <slug>` (sometimes appears in design / task assignment)
      3. `### N.M — <text>` heading (first occurrence) → use heading text
      4. Change slug: a line starting with `**Spec:**` or path hint
    Returns (task, change), either may be None.
    """
    task: str | None = None
    change: str | None = None
    if not body:
        return task, change

    for line in body.splitlines()[:80]:
        s = line.strip()
        if not s:
            continue
        if task is None:
            m = re.match(r"^\*\*Tasks?:\*\*\s+(.+)$", s)
            if m:
                task = m.group(1).strip()[:120]
                continue
        if task is None:
            m = re.match(r"^###\s+(\d+(?:\.\d+)+)\s+[—-]?\s*(.+)$", s)
            if m:
                task = f"{m.group(1)} {m.group(2)}"[:120]
                continue
        if change is None:
            m = re.match(r"^\*\*Change:\*\*\s+(.+)$", s)
            if m:
                change = m.group(1).strip().split()[0]
                continue
        if change is None:
            m = re.match(r"^\*\*Spec:\*\*\s+`?([\w/.\-]+)`?", s)
            if m:
                raw = m.group(1)
                # If it's a path like openspec/changes/<slug>/..., extract the slug
                parts = raw.split("/")
                if "changes" in parts:
                    idx = parts.index("changes")
                    if idx + 1 < len(parts):
                        change = parts[idx + 1]
                        continue
                change = raw
                continue
        if task is not None and change is not None:
            break
    return task, change


def _write_spec_owner(root: Path, change_name: str, agent: str) -> None:
    """Write or update spec_owner field in <change>/.openspec.yaml. Silent no-op on any error."""
    try:
        specs_rel = _read_platform_specs_path(root)
        if not specs_rel:
            return
        openspec_yaml = (
            root / specs_rel / "openspec" / "changes" / change_name / ".openspec.yaml"
        ).resolve()
        if not openspec_yaml.parent.is_dir():
            return
        lines = (
            openspec_yaml.read_text(encoding="utf-8").splitlines()
            if openspec_yaml.is_file()
            else []
        )
        new_lines = [line for line in lines if not line.strip().startswith("spec_owner:")]
        new_lines.append(f"spec_owner: {agent}")
        openspec_yaml.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def cmd_assign(args: list[str]) -> int:
    """Map tasks from OpenSpec tasks.md to repo owners and notify agents."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Task Assignment")

    if not args:
        # Auto-detect: scan OpenSpec changes/ for tasks.md files
        try:
            import yaml

            config_path = root / "platform.yaml"
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                specs_path = config.get("specs", {}).get("path", "")
                if specs_path:
                    changes_dir = root / specs_path / "openspec" / "changes"
                    if changes_dir.is_dir():
                        tasks_files = list(changes_dir.glob("*/tasks.md"))
                        # Exclude archived
                        tasks_files = [t for t in tasks_files if "archive" not in str(t)]
                        if tasks_files:
                            UI.info(f"Found {len(tasks_files)} tasks.md file(s):")
                            print()
                            for t in tasks_files:
                                rel = t.relative_to(root)
                                UI.muted(str(rel))
                            print()
                            UI.muted("Run: otaman assign <path-to-tasks.md-or-feature-dir>")
                            return 0
        except Exception:
            pass
        UI.error("Path to tasks.md or OpenSpec feature directory required")
        UI.muted("Usage: otaman assign <path-to-tasks.md>")
        UI.muted("       otaman assign openspec/changes/my-feature")
        return 1

    target = args[0]
    result = run_script("map-tasks.py", target, capture=True)
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        import json

        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    feature = report.get("feature", "?")
    total = report.get("total_tasks", 0)
    assigned = report.get("assigned", 0)
    unassigned = report.get("unassigned", 0)
    done = report.get("done", 0)
    pending = report.get("pending", 0)

    if feature and feature != "?":
        assign_agent = resolve_agent_identity(root) or "unknown-agent"
        _write_spec_owner(root, feature, assign_agent)

    UI.kv("Feature", feature, C.BOLD)
    UI.kv("Tasks", f"{total} total ({done} done, {pending} pending)")
    UI.kv("Assigned", f"{C.GREEN}{assigned}{C.RESET} | Unassigned: {C.YELLOW}{unassigned}{C.RESET}")

    by_owner = report.get("by_owner", {})
    if by_owner:
        UI.subheader("Assignments:")
        for owner, tasks in sorted(by_owner.items()):
            UI.bullet(f"{UI.agent(owner)}: {len(tasks)} task(s)")
            for t in tasks[:3]:
                UI.muted(f"- {t}")
            if len(tasks) > 3:
                UI.muted(f"... and {len(tasks) - 3} more")

    unassigned_tasks = report.get("unassigned_tasks", [])
    if unassigned_tasks:
        UI.subheader("Unassigned tasks:")
        for t in unassigned_tasks:
            UI.bullet(t, icon="-", color=C.YELLOW)
        UI.muted("Add @repo-name or **repo-name**: prefix to tasks.md to assign")

    created = report.get("bus_messages_created", [])
    if created:
        print()
        UI.ok("Bus messages created:")
        for c in created:
            UI.muted(c)

    # Task 4.2: @solution:<id> annotation scan + validation against solutions.yaml
    from otaman_cli.registries.assign_annotations import (
        resolve_tasks_md_path,
        scan_tasks_md,
    )

    tasks_md = resolve_tasks_md_path(target)
    if tasks_md is not None:
        findings = scan_tasks_md(tasks_md, root)
        if findings.has_findings:
            print()
            UI.subheader("Solution annotations (@solution:)")
            by_id: dict[str, list] = {}
            for ann in findings.annotations:
                by_id.setdefault(ann.solution_id, []).append(ann)
            for sol_id, anns in sorted(by_id.items()):
                marker = "✓" if sol_id in findings.valid_ids else "✗"
                UI.bullet(f"{marker} {sol_id} — {len(anns)} task(s)")
                if sol_id in findings.missing_ids:
                    UI.muted("    not found in solutions.yaml")
            if findings.missing_ids:
                print()
                UI.warn(
                    f"{len(findings.missing_ids)} unknown solution id(s) referenced. "
                    "Either add via `otaman solution add` or fix the annotation."
                )

        # Task 3.1 (auto-session-spawn): mode annotations [headless]/[interactive]
        # in task lines. Report counts in the assign summary so the user can
        # eyeball how many tasks the spawn-decision component will pick up
        # vs. how many still need explicit annotation.
        try:
            from otaman_cli.hitl.mode_annotations import (
                ModeAnnotationError,
            )
            from otaman_cli.hitl.mode_annotations import (
                scan_tasks_md as _mode_scan,
            )

            try:
                _mode_result = _mode_scan(tasks_md)
            except ModeAnnotationError as exc:
                print()
                UI.error(f"Mode annotation error in tasks.md: {exc}")
                return 1
            if _mode_result is not None:
                _tasks_mode, _summary = _mode_result
                if _summary.headless or _summary.explicit_count or _summary.default_count:
                    print()
                    UI.subheader("Task modes ([headless] / [interactive])")
                    UI.bullet(f"[headless]     {_summary.headless} task(s)")
                    UI.bullet(f"[interactive]  {_summary.interactive} task(s)")
                    if _summary.default_count:
                        UI.muted(
                            f"  ({_summary.default_count} defaulted to [interactive] "
                            "— add explicit annotations to silence)"
                        )
        except Exception as _mode_exc:
            UI.warn(f"Mode annotation scan skipped: {_mode_exc}")

    return 0


register(CommandSpec(name="send", handler=cmd_send, help="Send a bus message (substring match OK)"))
register(CommandSpec(name="read", handler=cmd_read, help="Read full content of a bus message"))
register(
    CommandSpec(name="ack", handler=cmd_ack, help="Acknowledge a bus message (resolved is default)")
)
register(CommandSpec(name="assign", handler=cmd_assign, help="Map OpenSpec tasks to repo owners"))
