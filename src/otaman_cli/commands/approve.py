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
        print("No bus directory found.")
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
            pending.append(
                {
                    "file": f,
                    "stem": f.stem,
                    "fm": fm,
                    "subject": subject,
                    "body": body,
                }
            )
        except (OSError, yaml.YAMLError):
            continue

    # Determine action from args if not explicit
    chat_phrase: str | None = None
    if args and action == "list":
        first = args[0].lower()
        if first in ("approve", "reject", "request", "confirm"):
            action = first
            args = args[1:]
            # `confirm <stem> <phrase>` — capture the echoed phrase before the
            # stem-matching below consumes args[0] as the pattern.
            if action == "confirm" and len(args) > 1:
                chat_phrase = args[1]
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
            UI.bullet(
                f"from {UI.agent(fm.get('from', '?'))} "
                f"[{UI.priority(fm.get('priority', 'normal'))}]"
            )
            print(f"    {p['subject']}")
            UI.muted(f"{fm.get('timestamp', '')} | {p['stem']}")
            print()
        UI.muted("To approve: otaman approve approve <stem-or-partial>")
        UI.muted('To reject:  otaman approve reject <stem-or-partial> [-d "reason"]')
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
                UI.muted(p["stem"])
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
            UI.muted(
                "Tip: paste either the full file stem OR the frontmatter id: value "
                "from `otaman approve list`."
            )
            return 1
        if len(matches) > 1:
            UI.error(f"Multiple matches for '{pattern}':")
            for m in matches:
                UI.muted(m["stem"])
            return 1
        target = matches[0]

    # hitl 1.3 — the insecure chat fallback is a TWO-STEP flow (the phrase-echo
    # is inherently two-turn), so it routes to its own request/confirm handlers
    # rather than the single-shot adapter confirm() below.
    if action == "request":
        return _chat_request(target)
    if action == "confirm":
        return _chat_confirm(
            target,
            chat_phrase,
            active_dir=active_dir,
            acks_dir=acks_dir,
            root=root,
            comment=comment,
        )

    # F012 (security GAP finding, 2026-07-04): approve/reject produce a
    # PRIVILEGED message (spec-change-approved/-rejected, asserts
    # `from: human`) — gate on a real human confirmation first.
    # Deliberately no --yes bypass: a Bash-tool-driven agent session has no
    # real TTY and must not be able to satisfy this on its own.
    #
    # hitl-confirmation-adapters 1.1: `approve` is the first HUMAN_DECISION
    # command, so it confirms through the adapter framework rather than a
    # direct TTY call. Unconfigured installs select the always-available
    # TTY adapter, which delegates verbatim to safety.confirm_human_decision
    # — behavior is byte-identical to before, including the no-TTY refusal.
    from otaman_cli.hitl.adapters import confirm_human_decision

    result = confirm_human_decision(
        f"About to {action} — {target['subject']}\n(proposal: {target['stem']})",
    )
    if not result.approved:
        UI.error(f"{action.capitalize()} cancelled — not confirmed.")
        return 1

    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # bus-test-isolation 2.1 — the approved/rejected messages are PRIVILEGED;
    # both helpers below ledger-gate before any file is written (fail closed).
    if action == "approve":
        return _perform_approval(
            target,
            active_dir=active_dir,
            acks_dir=acks_dir,
            root=root,
            comment=comment,
            now_ts=now_ts,
            now_iso=now_iso,
        )

    elif action == "reject":
        return _perform_rejection(
            target,
            active_dir=active_dir,
            acks_dir=acks_dir,
            root=root,
            comment=comment,
            now_ts=now_ts,
            now_iso=now_iso,
        )

    return 0


def _perform_approval(
    target: dict,
    *,
    active_dir,
    acks_dir,
    root,
    comment: str,
    now_ts: str,
    now_iso: str,
) -> int:
    """Write the PRIVILEGED spec-change-approved broadcast (ledger-gated).

    Extracted so BOTH the normal `approve approve` path (after adapter
    confirmation) and the chat-fallback `approve confirm` path (after the
    read-to-confirm phrase-echo) produce the identical privileged message
    through the identical fail-closed ledger gate — the confirmation method
    differs, the privileged write does not.
    """
    import yaml

    from otaman_cli.safety import record_privileged_confirmation

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

## Subject: Approved: {target["subject"].replace("Spec change request: ", "")}

The spec-change-request from **{target["fm"].get("from", "?")}** has been **approved**.

**Original proposal**: {target["stem"]}
{comment_section}
### Next steps
1. Specs will be created/updated in the specs repo (via OpenSpec or manually)
2. All agents will be notified when specs are committed (via post-commit hook)
3. Affected agents should review updated specs and adapt implementation

Use `/otaman:check` to track updates.
"""
    if not record_privileged_confirmation(
        message_id=f"{now_ts}-approved-{slug}",
        content=broadcast,
        command="approve",
    ):
        return 1

    ack_file = acks_dir / f"{target['stem']}.human.ack"
    ack_file.write_text("approved\n", encoding="utf-8")
    broadcast_file.write_text(broadcast, encoding="utf-8")

    UI.header("Proposal Approved")
    UI.ok(f"Approved: {target['subject']}")
    UI.kv("From", UI.agent(target["fm"].get("from", "?")))
    UI.kv("Ack", str(ack_file.relative_to(root)))
    UI.kv("Broadcast", str(broadcast_file.relative_to(root)))

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
            UI.action(f'cd {specs_path} && openspec new change "{proposal_title}"')
            UI.muted(f'Or use /opsx:new "{proposal_title}" in the specs repo Claude session')
            UI.muted(
                f"Then work on artifacts: openspec instructions <artifact> "
                f'--change "{proposal_title}"'
            )
    return 0


def _perform_rejection(
    target: dict,
    *,
    active_dir,
    acks_dir,
    root,
    comment: str,
    now_ts: str,
    now_iso: str,
) -> int:
    """Write the PRIVILEGED spec-change-rejected message (ledger-gated).

    Parallel to :func:`_perform_approval` so both the CLI `reject` path and the
    interactive console reuse one fail-closed privileged writer.
    """
    from otaman_cli.safety import record_privileged_confirmation

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

## Subject: Rejected: {target["subject"].replace("Spec change request: ", "")}

The spec-change-request has been **rejected**.

**Reason**: {reason}

**Original proposal**: {target["stem"]}
"""
    if not record_privileged_confirmation(
        message_id=f"{now_ts}-rejected",
        content=reject_msg,
        command="approve",
    ):
        return 1

    ack_file = acks_dir / f"{target['stem']}.human.ack"
    ack_file.write_text("rejected\n", encoding="utf-8")
    reject_file.write_text(reject_msg, encoding="utf-8")

    UI.header("Proposal Rejected")
    UI.error(f"Rejected: {target['subject']}")
    UI.kv("From", UI.agent(target["fm"].get("from", "?")))
    UI.kv("Reason", reason)
    UI.kv("Notification sent to", UI.agent(proposer))
    return 0


# ---------------------------------------------------------------------------
# hitl 1.3 — insecure chat-approval fallback (two-step read-to-confirm)


def _chat_reason_if_unavailable() -> str | None:
    """Why chat approval is unavailable, or None if it IS the active path.

    Returns a human-facing refusal reason honoring the design's precedence:
    autonomous marker > stronger adapter enrolled > flag off.
    """
    from otaman_cli.hitl import chat_fallback as cf
    from otaman_cli.hitl.adapters import STRENGTH_CHAT, registered_adapters
    from otaman_cli.hitl.config import load_hitl_config

    if cf.is_autonomous_context():
        return (
            f"refused: this session is marked autonomous "
            f"(OTAMAN_SESSION_MODE={cf.session_mode()}). Chat approval requires a "
            "human-attended session."
        )
    if not cf.chat_approval_enabled(load_hitl_config()):
        return (
            "chat approval is not enabled. A tenant admin must set "
            "`allow_insecure_chat_approval: true` in ~/.otaman/hitl.yaml "
            "(it is insecure by design and tenant-only)."
        )
    stronger = [
        a.name
        for a in registered_adapters()
        if a.strength > STRENGTH_CHAT and a.name != "chat" and a.is_configured()
    ]
    if stronger:
        return (
            f"refused: a stronger confirmation adapter is enrolled "
            f"({', '.join(sorted(stronger))}); "
            "chat fallback is disabled while it is configured (no silent downgrade)."
        )
    return None


def _chat_request(target: dict) -> int:
    """`approve request <stem>` — mint + append the read-to-confirm phrase."""
    import secrets
    import time
    from datetime import datetime, timezone

    from otaman_cli.hitl import chat_fallback as cf
    from otaman_cli.hitl.commands import _human_id

    reason = _chat_reason_if_unavailable()
    if reason is not None:
        UI.error(reason)
        return 1

    state = cf.chat_state_path()
    audit_path = cf.chat_audit_path()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cf.daily_cap_reached(state, today):
        UI.error(
            f"daily chat-approval limit reached ({cf.DAILY_CAP}/day). Try again tomorrow "
            "or use a stronger adapter."
        )
        return 1

    stem = target["stem"]
    human = _human_id()
    sid = cf.session_id()
    phrase = cf.generate_phrase()
    nonce_id = secrets.token_hex(4)
    nonce = cf.ChatNonce(
        stem=stem,
        nonce_id=nonce_id,
        phrase=phrase,
        human_id=human,
        session_id=sid,
        created_at=int(time.time()),
    )
    cf.append_phrase_to_proposal(target["file"], phrase, stem=stem, nonce_id=nonce_id)
    cf.record_request(state, nonce, today=today)
    cf.audit(
        audit_path,
        action="request",
        stem=stem,
        nonce_id=nonce_id,
        human_id=human,
        session_id=sid,
        outcome="phrase-minted",
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    UI.header("Chat approval — confirmation phrase appended")
    UI.warn("This is the INSECURE chat fallback (friction + audit, not cryptographic proof).")
    UI.kv("Proposal", str(target["file"]))
    UI.info(
        "Open and READ the proposal above. Copy the confirmation phrase from its END, then run:"
    )
    UI.action(f"otaman approve confirm {stem} <phrase>")
    UI.muted(
        f"The phrase expires in {cf.PHRASE_TTL_SECONDS // 60} min (re-run request for a fresh one)."
    )
    UI.muted(
        "The phrase is intentionally NOT printed here — it lives only in the "
        "proposal you must read."
    )
    return 0


def _chat_confirm(
    target: dict, phrase: str | None, *, active_dir, acks_dir, root, comment: str
) -> int:
    """`approve confirm <stem> <phrase>` — verify the echo, then approve."""
    import time
    from datetime import datetime, timezone

    from otaman_cli.hitl import chat_fallback as cf
    from otaman_cli.hitl.commands import _human_id

    state = cf.chat_state_path()
    audit_path = cf.chat_audit_path()
    stem = target["stem"]
    human = _human_id()
    sid = cf.session_id()
    pending = cf.pending_nonce(state)
    nonce_id = pending.nonce_id if pending else "-"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not phrase:
        UI.error("Usage: otaman approve confirm <stem> <phrase>  (paste the phrase you read).")
        return 1

    ok, why = cf.verify_phrase(state, stem, phrase, now=int(time.time()))
    if not ok:
        # Refusal invalidated the nonce (in verify); clean the block off the doc.
        _strip_confirm_block(target["file"])
        cf.audit(
            audit_path,
            action="confirm",
            stem=stem,
            nonce_id=nonce_id,
            human_id=human,
            session_id=sid,
            outcome="refused",
            timestamp=now_iso,
        )
        UI.error(f"Chat approval refused — {why}")
        return 1

    # Phrase matched: clean the block, perform the identical privileged approval.
    _strip_confirm_block(target["file"])
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rc = _perform_approval(
        target,
        active_dir=active_dir,
        acks_dir=acks_dir,
        root=root,
        comment=comment,
        now_ts=now_ts,
        now_iso=now_iso,
    )
    outcome = "approved" if rc == 0 else "approval-write-failed"
    cf.audit(
        audit_path,
        action="confirm",
        stem=stem,
        nonce_id=nonce_id,
        human_id=human,
        session_id=sid,
        outcome=outcome,
        timestamp=now_iso,
    )
    if rc == 0:
        _emit_chat_notice(active_dir, stem=stem, human=human, now_ts=now_ts, now_iso=now_iso)
        UI.muted(
            "Provenance: this approval used the INSECURE chat fallback — a bus notice was posted."
        )
    return rc


def _strip_confirm_block(doc_path) -> None:
    from otaman_cli.hitl import chat_fallback as cf

    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError:
        return
    doc_path.write_text(cf.strip_phrase_block(text), encoding="utf-8")


def _emit_chat_notice(active_dir, *, stem: str, human: str, now_ts: str, now_iso: str) -> None:
    """Surface on the bus that the insecure chat path was used (design: audit +
    surfacing). Non-privileged info; distinct from the approval broadcast."""
    notice = f"""---
id: {now_ts}-chat-approval-notice
from: hitl-audit
to: human
priority: normal
type: info
timestamp: {now_iso}
status: pending
---

## Subject: Insecure chat-approval used for {stem}

A spec-change was approved via the INSECURE chat fallback
(`hitl.allow_insecure_chat_approval`) by **{human}**. This mode is friction +
audit, not cryptographic proof of humanness. If you did not perform this
confirmation, review ~/.otaman/hitl-chat-audit.log and rotate trust.
"""
    (active_dir / f"{now_ts}-hitl-audit-to-human-chat-approval-notice.md").write_text(
        notice, encoding="utf-8"
    )


register(
    CommandSpec(
        name="approve",
        handler=cmd_approve,
        help="Review/approve agent-initiated spec-change-requests",
    )
)
