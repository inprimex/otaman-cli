"""`otaman human <list|enroll|remove>` — human-seat identity provisioning.

interactive-human-console 2.1 (the cli/provisioning split agreed with
deploy-agent, contract 20260826T213316): this command is the operator-facing
surface; the privileged root-side work (compute fingerprint, write the
annotated authorized_keys entry, apply edition hardening) lives in
deploy-agent's sudo-pinned `human-enroll.sh` mechanism, which `enroll`/`remove`
shell out to.

`list` is the values-free read side — it renders the tenant roster
(`/etc/otaman/human-roster.yaml`), which holds key FINGERPRINTS, never raw
keys. It works standalone (no privileged mechanism needed).

`enroll`/`remove` are gated on deploy-agent confirming the installed mechanism
path + exact args; until then they print a clear "not yet wired" message
naming the mechanism, so the surface is discoverable without pretending to work.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI

_ACTIONS = ("list", "enroll", "remove")


def cmd_human(args: list[str]) -> int:
    if not args or args[0] not in _ACTIONS:
        UI.error("Usage: otaman human <list|enroll|remove> [...]")
        UI.muted(
            "  list                     Show enrolled human identities (fingerprints, values-free)"
        )
        UI.muted("  enroll <roster-id> --key <pubkey>   Enroll an SSH key under a roster identity")
        UI.muted("  remove <roster-id>       Retire a key/identity")
        return 1
    action, *rest = args
    if action == "list":
        return _cmd_list(rest)
    return _cmd_enroll_or_remove(action, rest)


def _cmd_list(rest: list[str]) -> int:
    from otaman_cli.console.identity import tenant_roster_entries, tenant_roster_path

    path = tenant_roster_path()
    entries = tenant_roster_entries(path)
    UI.header("Enrolled human identities")
    if not entries:
        UI.muted(f"No enrolled humans (roster {path} is absent or empty).")
        UI.muted("Enroll one: otaman human enroll <roster-id> --key <pubkey>")
        return 0
    for h in entries:
        UI.bullet(str(h.get("roster_id", "?")))
        UI.kv("  fingerprint", str(h.get("fingerprint", "—")))
        UI.kv("  key-type", str(h.get("key_type", "—")))
        if h.get("comment"):
            UI.kv("  comment", str(h["comment"]))
        if h.get("added_at"):
            UI.kv("  added", str(h["added_at"]))
    UI.muted("(fingerprints only — raw keys never leave the human's machine)")
    return 0


def _cmd_enroll_or_remove(action: str, rest: list[str]) -> int:
    # The privileged mechanism (deploy-agent's sudo-pinned human-enroll.sh) is
    # the sole writer of key↔identity bindings. Wiring is pending deploy-agent's
    # confirmed installed path + args (bus 20260826T213316); fail honestly
    # rather than pretend.
    if not rest:
        UI.error(f"Usage: otaman human {action} <roster-id> [...]")
        return 1
    UI.error(
        f"`otaman human {action}` is not wired yet — it must shell to deploy-agent's "
        "sudo-pinned provisioning mechanism (human-enroll.sh), whose installed path is "
        "being confirmed. Track: interactive-human-console 2.1."
    )
    UI.muted("Meanwhile: `otaman human list` reads the roster, and `otaman -i` resolves identity.")
    return 2


register(
    CommandSpec(
        name="human",
        handler=cmd_human,
        help="Human-seat identity: list enrolled humans; enroll/remove SSH-key identities",
    )
)
