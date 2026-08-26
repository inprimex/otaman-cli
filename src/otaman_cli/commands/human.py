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

`enroll`/`remove` shell to the mechanism (deploy contract 20260826T221737):
  sudo /opt/otaman/human-enroll.sh <roster-id> <pubkey> [--tenant <user>]
  sudo /opt/otaman/human-enroll.sh --remove <roster-id> [--fingerprint <fp>] [--tenant <user>]
The mechanism auto-detects CE vs EE from edition.yaml, so this surface stays
edition-agnostic. The installed path is overridable via
``OTAMAN_HUMAN_ENROLL_MECHANISM`` (other hosts / tests).
"""

from __future__ import annotations

import os

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI

_ACTIONS = ("list", "enroll", "remove")

MECHANISM_ENV = "OTAMAN_HUMAN_ENROLL_MECHANISM"
DEFAULT_MECHANISM = "/opt/otaman/human-enroll.sh"


def _mechanism_path() -> str:
    return os.environ.get(MECHANISM_ENV, "").strip() or DEFAULT_MECHANISM


def run_mechanism(mech_args: list[str]):
    """Shell to the sudo-pinned provisioning mechanism (test seam — monkeypatch
    this to avoid real sudo). Returns a CompletedProcess."""
    import subprocess

    cmd = ["sudo", _mechanism_path(), *mech_args]
    return subprocess.run(cmd, capture_output=True, text=True)


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
    if action == "enroll":
        return _cmd_enroll(rest)
    return _cmd_remove(rest)


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


def _extract_flag(rest: list[str], flag: str) -> tuple[list[str], str | None]:
    """Pull ``--flag value`` out of *rest*; returns (remaining, value|None)."""
    out: list[str] = []
    value: str | None = None
    i = 0
    while i < len(rest):
        if rest[i] == flag and i + 1 < len(rest):
            value = rest[i + 1]
            i += 2
        else:
            out.append(rest[i])
            i += 1
    return out, value


def _parse_mechanism_result(rc: int, stdout: str, stderr: str, *, what: str) -> int:
    """Surface the mechanism's outcome; return a shell-appropriate code."""
    if rc != 0:
        UI.error(f"{what} failed (mechanism exit {rc}).")
        for line in (stderr or stdout).splitlines():
            if line.strip():
                UI.muted(f"  {line.rstrip()}")
        return rc or 1
    return 0


def _cmd_enroll(rest: list[str]) -> int:
    rest, key = _extract_flag(rest, "--key")
    rest, tenant = _extract_flag(rest, "--tenant")
    if not rest or key is None:
        UI.error(
            "Usage: otaman human enroll <roster-id> --key <pubkey-file-or-string> [--tenant <user>]"
        )
        return 1
    roster_id = rest[0]
    mech_args = [roster_id, key] + (["--tenant", tenant] if tenant else [])
    result = run_mechanism(mech_args)
    rc = _parse_mechanism_result(result.returncode, result.stdout, result.stderr, what="Enroll")
    if rc != 0:
        return rc
    # Parse the mechanism's parseable stdout (FINGERPRINT=... / ROSTER_ID=...).
    fields = _kv_lines(result.stdout)
    UI.ok(f"Enrolled {fields.get('ROSTER_ID', roster_id)}")
    UI.kv("fingerprint", fields.get("FINGERPRINT", "—"))
    UI.muted("Verify with `otaman human list`; the human's key now sets OTAMAN_HUMAN on SSH login.")
    return 0


def _cmd_remove(rest: list[str]) -> int:
    rest, fingerprint = _extract_flag(rest, "--fingerprint")
    rest, tenant = _extract_flag(rest, "--tenant")
    if not rest:
        UI.error("Usage: otaman human remove <roster-id> [--fingerprint <fp>] [--tenant <user>]")
        return 1
    roster_id = rest[0]
    mech_args = (
        ["--remove", roster_id]
        + (["--fingerprint", fingerprint] if fingerprint else [])
        + (["--tenant", tenant] if tenant else [])
    )
    result = run_mechanism(mech_args)
    rc = _parse_mechanism_result(result.returncode, result.stdout, result.stderr, what="Remove")
    if rc != 0:
        return rc
    UI.ok((result.stdout or f"removed {roster_id}").strip())
    return 0


def _kv_lines(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines (the mechanism's parseable output)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith(" "):
            k, _, v = line.partition("=")
            if k.isupper():
                out[k.strip()] = v.strip()
    return out


register(
    CommandSpec(
        name="human",
        handler=cmd_human,
        help="Human-seat identity: list enrolled humans; enroll/remove SSH-key identities",
    )
)
