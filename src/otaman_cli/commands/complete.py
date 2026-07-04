"""`otaman complete` — migrated from main.py.

--tasks/--all were complete-exclusive, so their flag-loop branches and
variables are removed entirely from main() (F021/F022), same as
check/blocked. cmd_complete now parses its own args.

_read_platform_specs_path stays in main.py -- it's shared with
_write_spec_owner (cmd_assign's helper, not yet migrated). _write_spec_owner
itself also stays for the same reason; only _read_spec_owner (complete's
side of that read/write pair) moves here.
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import UI, _read_platform_specs_path, _resolve_bus_paths, run_script


def _read_spec_owner(root: "Path", change_name: str) -> "str | None":
    """Return spec_owner from <change>/.openspec.yaml, or None if absent/unresolvable."""
    try:
        import yaml as _yaml
        specs_rel = _read_platform_specs_path(root)
        if not specs_rel:
            return None
        openspec_yaml = (root / specs_rel / "openspec" / "changes" / change_name / ".openspec.yaml").resolve()
        if not openspec_yaml.is_file():
            return None
        with open(openspec_yaml, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        owner = data.get("spec_owner", "")
        return str(owner).strip() or None
    except Exception:
        return None


def _find_task_assignment_sender(active_dir: "Path", change_name: str, root: "Path") -> str:
    """Locate the agent who sent the task-assignment for *change_name*.

    Scans bus active/ for messages with type: task-assignment whose
    body or change: frontmatter field matches *change_name*.
    Returns the from: agent name, or human if not found (D2 fallback).
    """
    try:
        import yaml as _yaml
    except ImportError:
        return "human"

    try:
        candidates = sorted(active_dir.glob("*.md"), reverse=True)
    except OSError:
        return "human"

    for f in candidates:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        import re as _re
        fm_match = _re.match(r"^---\n(.+?)\n---", text, _re.DOTALL)
        if not fm_match:
            continue
        try:
            fm = _yaml.safe_load(fm_match.group(1))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        if fm.get("type") != "task-assignment":
            continue
        # Match by explicit change: field or by change_name in subject/body
        fm_change = fm.get("change", "")
        if fm_change and fm_change == change_name:
            return str(fm.get("reply-to") or fm.get("from") or "human").strip()
        # Fallback: check if change_name appears in the message subject
        subject_match = _re.search(r"## Subject:.*" + _re.escape(change_name), text)
        if subject_match:
            return str(fm.get("reply-to") or fm.get("from") or "human").strip()

    return "human"


def _is_spec_agent(agent: str) -> bool:
    """fix-otaman-complete-task-drift task 1.1 — true only when the current
    agent is spec-agent.

    Takes the already-resolved identity (issue #93: this used to re-read
    `.agents/current-agent` directly via a second, divergent resolver that
    ignored `OTAMAN_AGENT` / `.otaman` `agent:` fields — so a session that
    correctly resolved to `spec-agent` via `resolve_agent_identity()` could
    still see `is_spec_agent() == False` and silently skip the tasks.md
    tick). Reuse the one identity `cmd_complete` already resolved instead.
    """
    return agent == "spec-agent"


def cmd_complete(args: list[str]) -> int:
    """Report task completion: send bus notification + (spec-agent only) tick tasks.md.

    fix-otaman-complete-task-drift Part A (tasks 1.1–1.5): when called by any
    agent OTHER than spec-agent, the tasks.md write is skipped because the
    calling agent has no permission to commit to otaman-specs.  The bus
    message is sent unconditionally; spec-agent's session-start sweep applies
    the ticks asynchronously (Part B).
    """
    tasks_spec = ""
    mark_all = False
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--tasks" and i + 1 < len(args):
            tasks_spec = args[i + 1]
            i += 2
        elif args[i] == "--all":
            mark_all = True
            i += 1
        else:
            positional.append(args[i])
            i += 1
    args = positional

    if not args:
        UI.error("Change name required")
        UI.muted("Usage: otaman complete <change-name> --tasks \"2.1,3.1-3.5\"")
        UI.muted("       otaman complete <change-name> --all")
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    change_name = args[0]

    if not tasks_spec and not mark_all:
        UI.error("Specify --tasks or --all")
        UI.muted("Examples:")
        UI.muted(f"  otaman complete {change_name} --tasks \"2.1, 2.3\"")
        UI.muted(f"  otaman complete {change_name} --tasks \"3.1-3.5\"")
        UI.muted(f"  otaman complete {change_name} --all")
        return 1

    UI.header("Task Completion")

    # Get agent identity: CWD→repo→owner → .agents/current-agent → "unknown-agent"
    agent = resolve_agent_identity(root) or "unknown-agent"

    # fix-otaman-complete-task-drift task 1.2 — guard the tasks.md write
    # behind an identity check.  Only spec-agent commits to otaman-specs,
    # so only spec-agent's working-tree edit will reach `main`.  For every
    # other agent, the local working-tree edit gets silently reverted on
    # the next `git pull --ff-only` — this silent drift had ~130 tasks
    # stuck on the wrong state before the fix landed (see PR #96 backfill).
    is_spec = _is_spec_agent(agent)
    if is_spec:
        # Step 1: Update tasks.md via actualize-tasks.py (spec-agent only)
        script_args = ["--change", change_name, "--agent", agent, "--project-root", str(root)]
        if mark_all:
            script_args.append("--all")
        elif tasks_spec:
            script_args.extend(["--tasks", tasks_spec])

        result = run_script("actualize-tasks.py", *script_args, capture=True)

        if result.returncode == 2:
            UI.error(result.stderr or result.stdout)
            return result.returncode

        try:
            import json as _json
            report = _json.loads(result.stdout)
        except Exception:
            print(result.stdout)
            report = {}

        updated = report.get("updated", 0)
        already = report.get("already_done", 0)
        not_found = report.get("not_found", [])
        tasks_file = report.get("tasks_file", "")

        if updated > 0:
            UI.ok(f"Updated: {updated} task(s) marked complete in tasks.md")
        if already > 0:
            UI.muted(f"Already done: {already} task(s)")
        if not_found:
            UI.warn(f"Not found: {', '.join(not_found)}")
        if tasks_file:
            UI.muted(f"File: {tasks_file}")
    else:
        # Non-spec-agent: skip the tasks.md write entirely.  The bus
        # message below is the canonical signal; spec-agent's sweep
        # (Part B of this change) applies the tick on next session start.
        UI.muted(f"(skipping tasks.md write — current agent is {agent!r}, not spec-agent)")

    # Step 2: Create task-complete bus message
    # D1: locate originating task-assignment to route reply to assigner only
    # D2: fall back to 'human' if no task-assignment found (not 'all')
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    active_dir, _ = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "acks").mkdir(exist_ok=True)

    # fix-otaman-complete-task-drift `_send_task_complete_bus_message` contract:
    # the design.md design specifies `to: spec-agent` for non-spec-agent
    # callers (spec-agent is the agent that applies tasks.md ticks via the
    # session-start sweep, so they're the canonical recipient).  The
    # original `_find_task_assignment_sender` path routed to whoever sent
    # the task-assignment, which produced a self-addressed message when
    # the calling agent had ALSO authored a task-assignment for the same
    # change (e.g. plugin-agent's report 2026-06-26).  For non-spec-agent
    # callers, override to spec-agent.  spec-agent's own runs keep the
    # legacy recipient logic since their work IS the canonical tick.
    if is_spec:
        recipient = _find_task_assignment_sender(active_dir, change_name, root)
    else:
        recipient = "spec-agent"

    slug = re.sub(r"[^a-z0-9]+", "-", change_name.lower()).strip("-")[:30]
    msg_id = f"{now_ts}-complete-{slug}"
    filename = f"{now_ts}-{agent}-to-{recipient.replace('/', '-')}-task-complete.md"

    task_label = "all tasks" if mark_all else f"tasks {tasks_spec}"

    # Note: `updated` is only set when the caller is spec-agent (i.e. the
    # actualize-tasks.py path ran).  For non-spec-agents the bus body
    # records the requested task ids; spec-agent's sweep applies the tick
    # asynchronously, so the "Updated: N" line becomes a forward-looking
    # plan rather than a past-tense report.
    if is_spec:
        updated_line = f"**Updated**: {updated} task(s) in tasks.md"
    else:
        updated_line = "**Pending tick**: spec-agent will apply tasks.md ticks on next session sweep"

    content = f"""---
id: {msg_id}
from: {agent}
to: {recipient}
priority: normal
type: task-complete
change: {change_name}
timestamp: {now_iso}
status: pending
---

## Subject: Tasks complete: {change_name}

**Agent**: {agent}
**Change**: {change_name}
**Completed**: {task_label}
{updated_line}
**Timestamp**: {now_iso}
"""

    filepath = active_dir / filename
    filepath.write_text(content, encoding="utf-8")

    print()
    UI.ok(f"Bus notification: {filepath.relative_to(root)}")
    UI.muted(f"Type: task-complete | To: {recipient} | Change: {change_name}")

    # fix-otaman-complete-task-drift task 1.2 — sweep notice for non-spec-agents.
    # The bus message above IS the canonical signal; spec-agent picks it up
    # via the session-start sweep (Part B) and applies the tick to tasks.md
    # at that point.  Print this so the calling agent doesn't expect the
    # checkboxes to be live immediately.
    if not is_spec:
        UI.ok("Bus task-complete sent.")
        UI.muted("    spec-agent will tick tasks.md on next session start.")

    # Step 2b: Fanout to spec_owner if set and different from primary recipient
    spec_owner = _read_spec_owner(root, change_name)
    if spec_owner and spec_owner != recipient:
        fanout_filename = f"{now_ts}-{agent}-to-{spec_owner.replace('/', '-')}-task-complete.md"
        fanout_content = content.replace(f"\nto: {recipient}\n", f"\nto: {spec_owner}\n", 1)
        fanout_path = active_dir / fanout_filename
        fanout_path.write_text(fanout_content, encoding="utf-8")
        UI.ok(f"Bus notification: {fanout_path.relative_to(root)}")
        UI.muted(f"Type: task-complete | To: {spec_owner} (spec_owner) | Change: {change_name}")

    # Step 3: Clear blocked entry if all tasks are done
    if mark_all:
        blocked_file = root / ".agents" / "blocked" / f"{agent}.md"
        if blocked_file.exists():
            blocked_content = blocked_file.read_text(encoding="utf-8")
            # Remove blocked entries for this change
            pattern = re.compile(
                rf"## Blocked:.*?{re.escape(change_name)}.*?(?=\n## |\Z)",
                re.DOTALL | re.IGNORECASE,
            )
            new_blocked = pattern.sub("", blocked_content).strip()
            if new_blocked:
                blocked_file.write_text(new_blocked + "\n", encoding="utf-8")
            else:
                blocked_file.unlink()
            UI.ok(f"Unblocked: Removed blocked entry for {change_name}")

    # agent-status-presence task 1.7 — write idle if all this agent's tasks
    # for the change are complete; otherwise write working (task=null).
    _status_hook_after_complete(root, agent, change_name)

    return 0


def _status_hook_after_complete(root: Path, agent: str, change_name: str) -> None:
    """agent-status-presence task 1.7 — refresh agent status after `complete`.

    Inspect the change's tasks.md for unchecked items assigned to this agent.
    - any unchecked → write `working` with task=null, change=<this change>
    - none unchecked → write `idle`

    Best-effort: silently no-op on any failure (file missing, parse error, etc.).
    """
    try:
        from otaman_cli.status import (
            AgentStatus, State, get_backend, is_agent_presence_enabled,
        )
    except Exception:
        return
    if not is_agent_presence_enabled(root):
        return

    tasks_md = _find_tasks_md_for_change(root, change_name)
    if not tasks_md or not tasks_md.is_file():
        return

    try:
        text = tasks_md.read_text(encoding="utf-8")
    except OSError:
        return

    # An "unchecked task for this agent" looks like one of:
    #   - [ ] 1.2 @otaman-cli ...
    #   - [ ] 1.2 ... (under a "## @otaman-cli" header)
    # Be conservative: count anything `- [ ]` whose line OR enclosing section
    # mentions this agent's @-handle.
    handle = f"@otaman-{agent.replace('-agent', '')}" if agent.endswith("-agent") else f"@{agent}"
    current_section_handle: str | None = None
    has_unchecked = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Section header: `## @otaman-cli` or `## @<handle>`
        if line.startswith("## ") and "@" in line:
            current_section_handle = line.split("@", 1)[1].split()[0]
            current_section_handle = "@" + current_section_handle.rstrip()
        if line.startswith("- [ ]"):
            mine = (
                handle in line
                or (current_section_handle and current_section_handle.lower() == handle.lower())
                or f"@{agent}" in line
            )
            if mine:
                has_unchecked = True
                break

    backend = get_backend(root)
    existing = backend.read(agent)
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if has_unchecked:
        # working with task=null (per spec); preserve since if already working
        since = existing.since if (existing and existing.state == State.WORKING) else now_iso
        new_status = AgentStatus(
            agent=agent, state=State.WORKING,
            task=None, change=change_name,
            since=since, updated_at=now_iso,
        )
    else:
        # idle clears everything
        since = existing.since if (existing and existing.state == State.IDLE) else now_iso
        new_status = AgentStatus(
            agent=agent, state=State.IDLE,
            task=None, change=None, outcome=None, blocked_by=None,
            since=since, updated_at=now_iso,
        )

    try:
        backend.write(new_status)
    except Exception:
        pass


def _find_tasks_md_for_change(root: Path, change_name: str) -> Path | None:
    """Locate `openspec/changes/<change>/tasks.md` in the specs repo.

    Resolution order:
      1. ../<root>-specs/openspec/changes/<change>/tasks.md
      2. Any directory `repos[].path` with name ending `-specs`
      3. Sibling sister directory `<project>-specs` of the meta repo
      4. Direct path: ../otaman-specs/openspec/changes/<change>/tasks.md
    """
    import yaml as _yaml
    candidates: list[Path] = []
    pyaml = root / "platform.yaml"
    if pyaml.is_file():
        try:
            doc = _yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        except Exception:
            doc = {}
        repos = doc.get("repos") if isinstance(doc, dict) else None
        if isinstance(repos, list):
            for r in repos:
                if not isinstance(r, dict):
                    continue
                p = r.get("path")
                name = r.get("name") or ""
                if not p:
                    continue
                if "specs" in str(name):
                    abs_p = (root / str(p)).resolve()
                    candidates.append(abs_p / "openspec" / "changes" / change_name / "tasks.md")
    # Fallback: well-known sibling `otaman-specs`
    candidates.append((root.parent / "otaman-specs" / "openspec" / "changes" / change_name / "tasks.md").resolve())
    # Project-specs sibling
    candidates.append((root.parent / f"{root.name}-specs" / "openspec" / "changes" / change_name / "tasks.md").resolve())
    for c in candidates:
        if c.is_file():
            return c
    return None


register(CommandSpec(
    name="complete",
    handler=cmd_complete,
    help="Report task completion, update tasks.md",
))
