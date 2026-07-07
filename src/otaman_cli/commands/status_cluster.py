"""`otaman status`/`set-status`/`whoami`/`iam` — migrated from main.py.

cmd_fleet_status is imported by commands/check.py (its "fleet summary"
section reuses it for the full-table case) -- that import now points
here instead of otaman_cli.main.

_resolve_bus_paths and _get_agent_ack_status stay in main.py: both are
genuinely shared utilities used by many already-migrated command
modules (check, complete, approve, bus_messaging, this one), not
exclusive to any single command.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import C, UI, _get_agent_ack_status, _resolve_bus_paths, run_script


def cmd_set_status(args: list[str]) -> int:
    """agent-status-presence task 1.5 — write a status record for the current agent.

    Usage:
      otaman set-status <state> [--task "..."] [--change <slug>] [--outcome <slug>]
                                [--blocked-by <agent|human>] [--agent <name>]

    States: working | blocked | waiting | idle.

    Heartbeat: re-calling with the same state preserves `since`; only
    `updated_at` advances.
    """
    import argparse
    parser = argparse.ArgumentParser(prog="otaman set-status", add_help=False)
    parser.add_argument("state", nargs="?")
    parser.add_argument("--task", default=None)
    parser.add_argument("--change", default=None)
    parser.add_argument("--outcome", default=None)
    parser.add_argument("--blocked-by", dest="blocked_by", default=None)
    parser.add_argument("--agent", dest="explicit_agent", default=None)
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        UI.muted("Usage: otaman set-status <working|blocked|waiting|idle> "
                 "[--task \"...\"] [--change SLUG] [--blocked-by NAME] [--outcome SLUG]")
        return 2

    if not ns.state:
        UI.error("set-status requires a state argument")
        UI.muted("Usage: otaman set-status <working|blocked|waiting|idle> [...]")
        return 2

    from otaman_cli.status import (
        AgentStatus, State, get_backend, is_agent_presence_enabled,
    )

    try:
        new_state = State(ns.state.lower())
    except ValueError:
        UI.error(f"Invalid state {ns.state!r}; expected one of: "
                 "working, blocked, waiting, idle")
        return 2

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    agent = resolve_agent_identity(root, explicit=ns.explicit_agent)
    if not agent:
        UI.error("Agent identity could not be resolved.")
        UI.muted("  Sources tried: OTAMAN_AGENT env, .otaman agent: field (CWD walk), .agents/current-agent")
        UI.muted("  Fix: pass --agent <name>, or set OTAMAN_AGENT, or run 'otaman init --update'")
        return 1

    if not is_agent_presence_enabled(root):
        UI.muted("Agent presence disabled (platform.agent_presence: false) — no-op")
        return 0

    backend = get_backend(root)
    existing = backend.read(agent)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    # Heartbeat: same state → preserve `since`; advance `updated_at` only.
    if existing is not None and existing.state == new_state:
        since = existing.since
    else:
        since = now_iso

    # Clearing fields when transitioning to idle (per spec: task/change null when idle)
    if new_state == State.IDLE:
        task = None
        change = None
        outcome = None
        blocked_by = None
    else:
        task = ns.task if ns.task is not None else (existing.task if existing else None)
        change = ns.change if ns.change is not None else (existing.change if existing else None)
        outcome = ns.outcome if ns.outcome is not None else (existing.outcome if existing else None)
        # blocked_by only meaningful for blocked state
        if new_state == State.BLOCKED:
            blocked_by = ns.blocked_by if ns.blocked_by is not None else (
                existing.blocked_by if existing else "human"
            )
        else:
            blocked_by = None

    status = AgentStatus(
        agent=agent,
        state=new_state,
        task=task,
        change=change,
        outcome=outcome,
        blocked_by=blocked_by,
        since=since,
        updated_at=now_iso,
    )
    try:
        backend.write(status)
    except Exception as exc:
        UI.error(f"Failed to write status: {exc}")
        return 1

    UI.ok(f"Status: {agent} → {new_state.value}")
    if task:
        UI.muted(f"  task:       {task}")
    if change:
        UI.muted(f"  change:     {change}")
    if blocked_by:
        UI.muted(f"  blocked_by: {blocked_by}")
    UI.muted(f"  since:      {since}")
    return 0


def cmd_fleet_status(args: list[str]) -> int:
    """agent-status-presence task 1.9 — fleet status table.

    Usage:
      otaman status [--blocked] [--agent NAME] [--json]

    Reads all status files via the configured backend, sorts by priority
    (blocked → working → waiting → idle), prints a table with summary counts.
    """
    import argparse
    parser = argparse.ArgumentParser(prog="otaman status", add_help=False)
    parser.add_argument("--blocked", action="store_true")
    parser.add_argument("--agent", dest="agent_filter", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    try:
        ns, _unknown = parser.parse_known_args(args)
    except SystemExit:
        UI.muted("Usage: otaman status [--blocked] [--agent NAME] [--json]")
        return 2

    from otaman_cli.status import State, get_backend, is_agent_presence_enabled
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    if not is_agent_presence_enabled(root):
        if ns.as_json:
            print(json.dumps({"enabled": False, "agents": []}))
        else:
            print("Agent presence disabled (platform.agent_presence: false)")
        return 0

    backend = get_backend(root)
    try:
        records = backend.read_all()
    except NotImplementedError as exc:
        UI.error(str(exc))
        return 2

    if ns.agent_filter:
        records = [r for r in records if r.agent == ns.agent_filter]
    if ns.blocked:
        records = [r for r in records if r.state == State.BLOCKED]

    # Priority sort: blocked, working, waiting, idle
    order = {State.BLOCKED: 0, State.WORKING: 1, State.WAITING: 2, State.IDLE: 3}
    records.sort(key=lambda r: (order.get(r.state, 99), r.agent))

    if ns.as_json:
        print(json.dumps({
            "enabled": True,
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "agents": [r.to_dict() for r in records],
        }, indent=2))
        return 0

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    print()
    UI.header(f"Fleet status  ({now_utc.strftime('%Y-%m-%d %H:%M UTC')})")
    if not records:
        UI.muted("  No agents reporting status yet.")
        return 0

    def _since_human(iso: str) -> str:
        try:
            t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return iso
        delta = now_utc - t
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"

    # Render compact table
    print(f"  {'AGENT':<18} {'STATE':<9} {'SINCE':<8} TASK / CHANGE")
    counts = {s: 0 for s in State}
    for r in records:
        counts[r.state] = counts.get(r.state, 0) + 1
        tail = ""
        if r.state != State.IDLE:
            parts: list[str] = []
            if r.task:
                parts.append(r.task)
            if r.change:
                parts.append(r.change)
            tail = "  ·  ".join(parts) if parts else "—"
        elif r.state == State.IDLE:
            tail = "—"
        if r.state == State.BLOCKED and r.blocked_by:
            tail = f"blocked by {r.blocked_by}  ·  {tail}".rstrip("  ·  ")
        print(f"  {r.agent:<18} {r.state.value:<9} {_since_human(r.since):<8} {tail}")

    print()
    UI.muted(
        f"Blocked: {counts.get(State.BLOCKED, 0)}   "
        f"Waiting: {counts.get(State.WAITING, 0)}   "
        f"Working: {counts.get(State.WORKING, 0)}   "
        f"Idle: {counts.get(State.IDLE, 0)}"
    )
    return 0


def cmd_status(args: list[str]) -> int:
    """Show fleet status (default) or cross-repo dashboard (--repos).

    Default: agent-status-presence fleet view (task 1.9).  Pass --repos to
    get the legacy cross-repo dashboard.
    """
    if "--repos" in args:
        args = [a for a in args if a != "--repos"]
        return _cmd_status_repos(args)
    return cmd_fleet_status(args)


def _cmd_status_repos(args: list[str]) -> int:
    """Show cross-repo status dashboard. Also runs silent bus cleanup."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project (no platform.yaml or .agents/ found)")
        return 1

    script_args = [str(root)]
    if args:
        script_args.append(args[0])  # repo filter

    result = run_script("status-report.py", *script_args, capture=True)
    if result.returncode != 0:
        UI.error(result.stderr or result.stdout)
        return result.returncode

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ImportError):
        print(result.stdout)
        return 0

    if "error" in report:
        UI.error(report['error'])
        return 1

    UI.header(f"Otaman Status: {report.get('project', '?')}")

    # Repos table
    repos = report.get("repos", [])
    headers = ["Repo", "Owner", "Branch", "State", "Messages"]
    rows = []

    for repo in repos:
        name = repo["name"]
        owner = repo.get("owner", "-")
        if not repo.get("exists"):
            rows.append([UI.repo(name), UI.agent(owner), f"{C.RED}NOT FOUND{C.RESET}", "", ""])
            continue
        if not repo.get("is_git"):
            rows.append([UI.repo(name), UI.agent(owner), UI.path("not a git repo"), "", ""])
            continue

        branch = repo.get("branch", "?")
        if len(branch) > 13:
            branch = branch[:12] + ".."

        clean = repo.get("clean", True)
        ahead = repo.get("ahead", 0)
        state_parts = []
        if clean:
            state_parts.append(UI.badge("clean", C.GREEN))
        else:
            state_parts.append(f"{C.YELLOW}{repo.get('modified_files', 0)} mod{C.RESET}")
        if ahead:
            state_parts.append(f"{ahead}^")
        behind = repo.get("behind", 0)
        if behind:
            state_parts.append(f"{behind}v")
        state = " ".join(state_parts)

        pending = repo.get("pending_messages", 0)
        msg_str = f"{C.YELLOW}{pending} pending{C.RESET}" if pending else UI.path("none")

        rows.append([UI.repo(name), UI.agent(owner), branch, state, msg_str])

    UI.table(headers, rows, col_widths=[20, 18, 15, 18, 12])

    # Messages summary
    msgs = report.get("messages", {})
    UI.kv("Messages", f"{msgs.get('pending', 0)} pending | {msgs.get('read', 0)} read | {msgs.get('resolved', 0)} resolved")

    # Pending reviews
    reviews = report.get("pending_reviews", [])
    if reviews:
        UI.subheader("Pending reviews:")
        for r in reviews:
            UI.bullet(f"{r.get('reviewer', '?')}: {r.get('scope', '?')} [{r.get('status', '?')}]")

    # Proposals
    proposals = report.get("proposals", [])
    if proposals:
        UI.subheader("Active proposals:")
        for p in proposals:
            UI.bullet(f"{p.get('id', '?')}: {p.get('title', '?')} [{p.get('status', '?')}]", color=C.BLUE)

    # Silent bus cleanup
    cleanup_result = run_script("cleanup-bus.py", str(root), capture=True)
    if cleanup_result.returncode == 0:
        try:
            cr = json.loads(cleanup_result.stdout)
            migrated = cr.get("migrated", 0)
            archived = len(cr.get("archived", []))
            if migrated or archived:
                parts = []
                if migrated:
                    parts.append(f"{migrated} migrated")
                if archived:
                    parts.append(f"{archived} archived")
                UI.muted(f"Bus cleanup: {', '.join(parts)}")
        except (json.JSONDecodeError, ImportError):
            pass

    print()
    return 0


def _cmd_whoami_for_path(raw_path: str) -> int:
    """monorepo-path-ownership task 2.1 — `otaman whoami --for-path <path>`.

    Resolves the owning agent for *raw_path* against the current project's
    `platform.yaml owner-paths` configuration.  Prints a 3-line table per
    the spec example:

        repo:  <repo-name>
        path:  <relative-path>
        owner: <agent>  (<source>)

    where `<source>` is either `matched glob: "<pat>"`, `fallback — no glob
    matched`, or `no owner-paths configured`.  Exit 0 on success, 1 when
    the path isn't under any registered repo.
    """
    from pathlib import Path as _Path
    from otaman_cli.owner_paths import resolve_owner_for_path

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    target = _Path(raw_path)
    # Resolve relative to CWD (per spec).  Don't require the file to exist —
    # we're matching by glob, not by filesystem state.
    if not target.is_absolute():
        target = _Path.cwd() / target

    result = resolve_owner_for_path(target, project_root=root)
    if result is None:
        UI.error(
            f'path "{raw_path}" is not under any repo registered in platform.yaml'
        )
        return 1

    UI.kv("  repo", result.repo_name)
    UI.kv("  path", result.relative_path)
    if result.matched_glob is not None:
        tail = f"(matched glob: \"{result.matched_glob}\")"
    else:
        tail = f"({result.fallback_reason})"
    UI.kv("  owner", f"{result.agent}  {tail}")
    return 0


def _cmd_whoami_resolve_only() -> int:
    """F013 — lightweight non-interactive wrapper around
    `otaman_core.identity.resolve_enforcement_identity()`.

    Exists so non-Python callers (the Bash PreToolUse hook,
    `otaman-plugin/scripts/_resolve.sh`) can shell out to this instead of
    reimplementing the enforcement-identity priority chain themselves —
    that exact kind of drift between independently-maintained resolvers
    already caused a real incident (MCP misattributing every `otaman_send`
    call to `plugin-agent`, 2026-06-08).

    Prints ONLY the resolved agent name on success (nothing else, so a
    shell can capture it directly via `$(...)`), and exits 1 with no
    output when identity can't be resolved.
    """
    from otaman_core.identity import resolve_enforcement_identity

    result = resolve_enforcement_identity()
    if not result.agent:
        return 1
    print(result.agent)
    return 0


def cmd_whoami(args: list[str]) -> int:
    """Print current agent identity + project + routing + bus state.

    Usage:
      otaman whoami [--json]
      otaman whoami --for-path <path>    # monorepo-path-ownership 2.1
      otaman whoami --resolve-only       # F013: enforcement identity only

    Useful for confirming which agent / project / routing identity is
    loaded in this tab, especially when terminal tab titles get
    overwritten by claude or tmux.
    """
    import yaml

    # monorepo-path-ownership task 2.1 — `--for-path <path>` subcommand
    if "--for-path" in args:
        idx = args.index("--for-path")
        if idx + 1 >= len(args):
            UI.error("--for-path requires a path argument")
            UI.muted("Usage: otaman whoami --for-path <path>")
            return 1
        return _cmd_whoami_for_path(args[idx + 1])

    # F013 — `--resolve-only` dispatches before the heavier display logic
    # below (routing/tmux/bus-state lookups); it's meant to be cheap enough
    # for a hook to shell out to on every relevant tool call.
    if "--resolve-only" in args:
        return _cmd_whoami_resolve_only()

    json_mode = "--json" in args

    root = find_project_root()
    agent = resolve_agent_identity(root) if root else None

    # Routing: read via the same env-var chain the hooks use.
    try:
        from otaman_core._resolve import active_routing_env
        routing = active_routing_env()
    except ImportError:
        routing = (os.environ.get("OTAMAN_ACTIVE_ROUTING")
                   or os.environ.get("OTAMAN_ACTIVE_ACCOUNT")
                   or os.environ.get("MAESTRO_ACTIVE_ACCOUNT"))

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or "<default ~/.claude>"
    tmux_env = os.environ.get("TMUX", "")
    # TMUX env is "/path/to/socket,pid,session-id"; session name needs `tmux display`,
    # but the env var alone is enough to flag "yes, inside tmux".
    in_tmux = bool(tmux_env)
    tmux_session = None
    if in_tmux:
        try:
            import subprocess
            res = subprocess.run(
                ["tmux", "display-message", "-p", "#S"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if res.returncode == 0:
                tmux_session = res.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

    project_name = None
    if root and (root / "platform.yaml").is_file():
        try:
            cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
            project_name = cfg.get("project")
        except (yaml.YAMLError, OSError):
            pass

    # Bus state: pending/read/resolved counts for this agent.
    counts = {"pending": 0, "read": 0, "resolved": 0}
    if root and agent:
        active_dir, acks_dir = _resolve_bus_paths(root)
        if active_dir.is_dir():
            import re
            for f in sorted(active_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8")
                    m = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
                    if not m:
                        continue
                    fm = yaml.safe_load(m.group(1))
                    if not isinstance(fm, dict):
                        continue
                    to = fm.get("to", "")
                    if to != agent and to != "all":
                        continue
                    status = _get_agent_ack_status(f.stem, agent, acks_dir)
                    counts[status] = counts.get(status, 0) + 1
                except (OSError, yaml.YAMLError):
                    continue

    if json_mode:
        print(json.dumps({
            "agent": agent,
            "project": project_name,
            "project_root": str(root) if root else None,
            "cwd": str(Path.cwd()),
            "routing": routing,
            "config_dir": config_dir,
            "in_tmux": in_tmux,
            "tmux_session": tmux_session,
            "bus_counts": counts,
        }, indent=2))
        return 0

    # Pretty output
    UI.header(f"Otaman: {agent or '<unknown agent>'}")
    if project_name:
        UI.kv("  Project", project_name)
    if root:
        UI.kv("  Project root", str(root))
    else:
        UI.muted("  (not inside a otaman project)")
    UI.kv("  Cwd", str(Path.cwd()))
    UI.kv("  Routing", routing or "<not set>")
    UI.kv("  Config dir", config_dir)
    if in_tmux:
        UI.kv("  Tmux", tmux_session or "<unknown session>")
    if root and agent:
        UI.kv("  Bus", f"{counts['pending']} pending | {counts['read']} read | {counts['resolved']} resolved")
    return 0


register(CommandSpec(name="status", handler=cmd_status, help="Fleet status (per-agent presence; --repos for legacy view)"))
register(CommandSpec(name="set-status", handler=cmd_set_status, help="Update this agent's status (working|blocked|waiting|idle)"))
register(CommandSpec(name="whoami", handler=cmd_whoami, help="Show agent identity + project + routing + bus state"))
register(CommandSpec(name="iam", handler=cmd_whoami, help="Show agent identity + project + routing + bus state"))
