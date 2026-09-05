"""`otaman propose` and `otaman team` — migrated from main.py.

Migrated together because both read `-d`/`--desc` from main()'s shared
flag loop and no other command does -- moving them as a pair lets that
flag drop out of the loop entirely (F021/F022) instead of being
duplicated in each module.
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_cli import main as _main
from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import UI, C, _resolve_bus_paths


def _help_requested(args: list[str]) -> bool:
    """True if `-h`/`--help` appears anywhere in args.

    `propose` and `team` turn their positional into content (the proposal
    title / the feature description) with no required flag to gate them, so a
    bare `--help` would otherwise be swallowed as the title and the verb would
    perform its side effect — a real `spec-change-request` on the bus, or a
    workflow orchestration — on a bogus "--help" value. Help must win over
    positional parsing here (post-mortem, Roman 2026-08-31).
    """
    return any(a in ("-h", "--help") for a in args)


def _parse_desc(args: list[str]) -> tuple[str, list[str]]:
    """Extract `-d`/`--desc VALUE` from args, returning (desc, remaining)."""
    desc = ""
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in ("-d", "--desc") and i + 1 < len(args):
            desc = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1
    return desc, positional


def cmd_propose(args: list[str]) -> int:
    """Create a spec-change-request on the bus for human approval."""
    if _help_requested(args):
        UI.muted('Usage: otaman propose "add user pagination" [-d "Detailed description"]')
        return 0

    desc, args = _parse_desc(args)

    if not args:
        UI.error("Title required")
        UI.muted('Usage: otaman propose "add user pagination" [-d "Detailed description"]')
        return 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Spec Change Request")

    title = " ".join(args)

    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # Get agent: CWD→repo→owner → .agents/current-agent → "human"
    agent = resolve_agent_identity(root) or "human"

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    msg_id = f"{now_ts}-scr-{slug}"
    filename = f"{now_ts}-{agent}-to-human-spec-change-request.md"

    active_dir, _ = _resolve_bus_paths(root)
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "acks").mkdir(exist_ok=True)

    content = f"""---
id: {msg_id}
from: {agent}
to: human
priority: high
type: spec-change-request
timestamp: {now_iso}
status: pending
---

## Subject: Spec change request: {title}

### What needs to change
{desc or "TODO: Describe the proposed spec change."}

### Why this is needed
TODO: What was discovered during implementation that triggered this.

### Affected specs
TODO: Which spec files/areas need updating.

### Affected repos
TODO: Which repos will need implementation changes after the spec updates.

### Suggested spec changes
TODO: Concrete suggestions for what the spec should say.
"""

    from otaman_cli.bus_write import write_message_exclusive

    # Never overwrite: two proposals in the same second must not clobber each
    # other (the stem is second-precision). The returned path carries any
    # collision suffix, so the blocked entry + report below stay consistent.
    filepath = write_message_exclusive(active_dir / filename, content)

    # Record blocked task
    msg_stem = filepath.stem
    blocked_dir = root / ".agents" / "blocked"
    blocked_dir.mkdir(parents=True, exist_ok=True)
    blocked_file = blocked_dir / f"{agent}.md"
    blocked_entry = f"""
## Blocked: {title}
- **Proposal**: {msg_stem}
- **Blocked since**: {now_iso}
- **Depends on**: spec-change-approved + spec-change notification
- **Task to resume**: Implement feature after spec is committed
"""
    with open(blocked_file, "a", encoding="utf-8") as f:
        f.write(blocked_entry)

    UI.ok(f"Created: {filepath.relative_to(root)}")
    UI.kv("From", UI.agent(agent))
    UI.kv("Type", "spec-change-request (pending human approval)", C.YELLOW)
    UI.kv("Blocked", str(blocked_file.relative_to(root)), C.YELLOW)
    print()
    UI.blocked("STOP: Do NOT implement features that depend on this spec change.")
    UI.action(f"Switch to other tasks. Run {C.BOLD}otaman check{C.RESET} to poll for approval.")
    print()
    UI.muted("A human must review and approve this via: otaman approve")
    UI.muted("Edit the message file to fill in details if needed.")
    return 0


def cmd_team(args: list[str]) -> int:
    """Orchestrate a cross-repo feature."""
    if _help_requested(args):
        UI.muted("Usage: otaman team <workflow-or-description> [-d details]")
        UI.muted("Examples:")
        UI.muted('  otaman team api-change -d "Add pagination to /users"')
        UI.muted('  otaman team "Add user authentication flow"')
        return 0

    desc, args = _parse_desc(args)

    UI.header("Otaman Team Orchestration")

    if not args:
        UI.error("Feature description required")
        UI.muted("Usage: otaman team <workflow-or-description> [-d details]")
        UI.muted("Examples:")
        UI.muted('  otaman team api-change -d "Add pagination to /users"')
        UI.muted('  otaman team "Add user authentication flow"')
        return 1

    feature = " ".join(args)
    UI.kv("Feature", feature, C.BOLD)
    if desc:
        UI.kv("Details", desc)

    # Check for workflow template. Resolved against otaman_cli.main's file
    # location (not this module's __file__) -- the otaman CLI package is
    # part of the plugin checkout, and main.py is the stable entrypoint
    # file this has always been pinned against.
    plugin_root = Path(_main.__file__).resolve().parent.parent
    template_path = plugin_root / "references" / "workflows" / f"{feature}.md"
    if template_path.exists():
        UI.kv("Template", f"{C.GREEN}found{C.RESET} ({feature}.md)")
    else:
        UI.kv("Template", "custom (no standard workflow template)")

    UI.subheader("To orchestrate this feature:")
    UI.action(f"Use {C.GREEN}/otaman:team {feature}{C.RESET} in Claude Code")
    UI.muted("It will decompose into tasks, assign to agents via bus, and track progress.")
    return 0


register(
    CommandSpec(
        name="propose", handler=cmd_propose, help="Propose a spec change (pending human approval)"
    )
)
register(
    CommandSpec(
        name="team", handler=cmd_team, help="Orchestrate a cross-repo feature (decompose + assign)"
    )
)
