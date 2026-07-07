"""`otaman approve` — migrated from main.py.

`comment` came from main()'s shared `-d`/`--desc` flag, still needed by
`propose`/`team` (not yet migrated), so cmd_approve parses `-d`/`--desc`
itself here -- temporary duplication until those two migrate and the
shared-loop copy can be deleted (same pattern as scan/check).

`action` was passed from a main()-level `approve_action` variable that
turned out to be dead: cmd_approve always re-derives the actual action
from args[0] itself (approve/reject/list), so that keyword arg never did
anything. Dropped -- default "list" plus the function's own args[0]
sniffing is unchanged behavior.
"""

from __future__ import annotations

import re

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, _resolve_bus_paths


def cmd_approve(args: list[str]) -> int:
    """Review and approve/reject pending spec-change-requests."""
    comment = ""
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in ("-d", "--desc") and i + 1 < len(args):
            comment = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1
    args = positional
    action = "list"

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    try:
        import yaml
    except ImportError:
        UI.error("PyYAML required")
        return 2

    active_dir, acks_dir = _resolve_bus_paths(root)
    acks_dir.mkdir(parents=True, exist_ok=True)

    if not active_dir.is_dir():
        print(f"No bus directory found.")
        return 1

    # Find pending spec-change-requests (no human.ack file)
    pending = []
    for f in sorted(active_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict):
                continue
            if fm.get("type") != "spec-change-request":
                continue
            # Check if already approved/rejected
            ack_file = acks_dir / f"{f.stem}.human.ack"
            if ack_file.exists():
                continue
            # Extract subject
            subject = ""
            body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            for line in body.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break
            pending.append({
                "file": f,
                "stem": f.stem,
                "fm": fm,
                "subject": subject,
                "body": body,
            })
        except (OSError, yaml.YAMLError):
            continue

    # Determine action from args if not explicit
    if args and action == "list":
        first = args[0].lower()
        if first in ("approve", "reject"):
            action = first
            args = args[1:]
        elif first != "list":
            # Treat as message ID for approval
            action = "approve"

    # LIST
    if action == "list":
        UI.header("Pending Spec Change Requests")
        if not pending:
            UI.muted("No pending spec-change-requests.")
            return 0
        for p in pending:
            fm = p["fm"]
            UI.bullet(f"from {UI.agent(fm.get('from', '?'))} [{UI.priority(fm.get('priority', 'normal'))}]")
            print(f"    {p['subject']}")
            UI.muted(f"{fm.get('timestamp', '')} | {p['stem']}")
            print()
        UI.muted("To approve: otaman approve approve <stem-or-partial>")
        UI.muted("To reject:  otaman approve reject <stem-or-partial> [-d \"reason\"]")
        return 0

    # APPROVE or REJECT — need a target
    if not args:
        if len(pending) == 1:
            target = pending[0]
        elif not pending:
            UI.error("No pending spec-change-requests")
            return 1
        else:
            UI.error("Multiple pending requests. Specify which one:")
            for p in pending:
                UI.muted(p['stem'])
            return 1
    else:
        pattern = args[0]
        # Tier 1: substring match on file stem
        matches = [p for p in pending if pattern in p["stem"]]
        # Tier 2: token-based fallback (covers logical reconstruction stems)
        if not matches and "-" in pattern:
            import fnmatch
            tokens = [tok for tok in pattern.split("-") if tok]
            if len(tokens) >= 2:
                glob_pat = "*" + "*".join(tokens) + "*"
                matches = [p for p in pending if fnmatch.fnmatch(p["stem"], glob_pat)]
        # Tier 3: frontmatter id field match (agents copy from top line of check)
        if not matches:
            for p_ in pending:
                fm_id = str(p_.get("fm", {}).get("id", ""))
                if fm_id and (fm_id == pattern or pattern in fm_id):
                    matches.append(p_)
        if not matches:
            UI.error(f"No pending request matching '{pattern}'")
            UI.muted("Tip: paste either the full file stem OR the frontmatter id: value from `otaman approve list`.")
            return 1
        if len(matches) > 1:
            UI.error(f"Multiple matches for '{pattern}':")
            for m in matches:
                UI.muted(m['stem'])
            return 1
        target = matches[0]

    # F012 (security GAP finding, 2026-07-04): approve/reject produce a
    # PRIVILEGED message (spec-change-approved/-rejected, asserts
    # `from: human`) — gate on a real interactive confirmation first.
    # Deliberately no --yes bypass: a Bash-tool-driven agent session has no
    # real TTY and must not be able to satisfy this on its own.
    from otaman_cli.safety import confirm_human_decision
    if not confirm_human_decision(
        f"About to {action} — {target['subject']}\n(proposal: {target['stem']})",
    ):
        UI.error(f"{action.capitalize()} cancelled — not confirmed.")
        return 1

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    if action == "approve":
        # Create approval ack
        ack_file = acks_dir / f"{target['stem']}.human.ack"
        ack_file.write_text("approved\n", encoding="utf-8")

        # Broadcast approval
        slug = re.sub(r"[^a-z0-9]+", "-", target["subject"].lower()).strip("-")[:30]
        broadcast_file = active_dir / f"{now_ts}-human-to-all-spec-change-approved.md"
        comment_section = f"\n### Human comments\n{comment}\n" if comment else ""

        broadcast = f"""---
id: {now_ts}-approved-{slug}
from: human
to: all
priority: high
type: spec-change-approved
timestamp: {now_iso}
status: pending
---

## Subject: Approved: {target['subject'].replace('Spec change request: ', '')}

The spec-change-request from **{target['fm'].get('from', '?')}** has been **approved**.

**Original proposal**: {target['stem']}
{comment_section}
### Next steps
1. Specs will be created/updated in the specs repo (via OpenSpec or manually)
2. All agents will be notified when specs are committed (via post-commit hook)
3. Affected agents should review updated specs and adapt implementation

Use `/otaman:check` to track updates.
"""
        broadcast_file.write_text(broadcast, encoding="utf-8")

        UI.header("Proposal Approved")
        UI.ok(f"Approved: {target['subject']}")
        UI.kv("From", UI.agent(target['fm'].get('from', '?')))
        UI.kv("Ack", str(ack_file.relative_to(root)))
        UI.kv("Broadcast", str(broadcast_file.relative_to(root)))

        # Check if OpenSpec is available
        config_path = root / "platform.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            specs_format = config.get("specs", {}).get("format", "fallback")
            specs_path = config.get("specs", {}).get("path", "")
            if specs_format == "openspec" and specs_path:
                proposal_title = target["subject"].replace("Spec change request: ", "")
                print()
                UI.info("OpenSpec mode: To create the spec, run in the specs repo:")
                UI.action(f"cd {specs_path} && openspec new change \"{proposal_title}\"")
                UI.muted(f"Or use /opsx:new \"{proposal_title}\" in the specs repo Claude session")
                UI.muted(f"Then work on artifacts: openspec instructions <artifact> --change \"{proposal_title}\"")

        return 0

    elif action == "reject":
        # Create rejection ack
        ack_file = acks_dir / f"{target['stem']}.human.ack"
        ack_file.write_text("rejected\n", encoding="utf-8")

        # Notify the proposing agent
        proposer = target["fm"].get("from", "all")
        reject_file = active_dir / f"{now_ts}-human-to-{proposer}-spec-change-rejected.md"
        reason = comment or "No reason provided."

        reject_msg = f"""---
id: {now_ts}-rejected
from: human
to: {proposer}
priority: normal
type: spec-change-rejected
timestamp: {now_iso}
status: pending
---

## Subject: Rejected: {target['subject'].replace('Spec change request: ', '')}

The spec-change-request has been **rejected**.

**Reason**: {reason}

**Original proposal**: {target['stem']}
"""
        reject_file.write_text(reject_msg, encoding="utf-8")

        UI.header("Proposal Rejected")
        UI.error(f"Rejected: {target['subject']}")
        UI.kv("From", UI.agent(target['fm'].get('from', '?')))
        UI.kv("Reason", reason)
        UI.kv("Notification sent to", UI.agent(proposer))
        return 0

    return 0


register(CommandSpec(
    name="approve",
    handler=cmd_approve,
    help="Review/approve agent-initiated spec-change-requests",
))
