"""SSH-derived operator identity for console approvals (Q3 / task 1.2).

The human connects over SSH; provisioning (deploy-agent 2.1) writes annotated
`authorized_keys` lines that make sshd set `OTAMAN_HUMAN=<roster-id>` per key,
pre-shell. The console reads that variable and validates it against the
program's human-roster (which stores key FINGERPRINTS — raw keys never leave
the human's machine). Every approval/rejection is stamped with the result.

Honest threat model (Q7): on a shared tenant user this is a trust+policy
binding, not cryptographic. When `OTAMAN_HUMAN` is absent the console does NOT
silently proceed as a known human — it marks the audit `unverified-identity`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The operator resolve_identity() reports when OTAMAN_HUMAN is absent (vs a set
# but unverified value) — the badge uses it to tell "none" from a real value.
_ABSENT_OPERATOR = "unknown-operator"


def tenant_roster_path(home: Path | None = None) -> Path:
    """The provisioning roster deploy-agent's mechanism writes (contract
    20260826T213316): ``/etc/otaman/human-roster.yaml`` — root-owned, 0644,
    FINGERPRINTS only (never raw keys), keyed by ``roster_id``. Split out so
    tests can point it at a fixture."""
    return (
        (home / "etc" / "otaman" / "human-roster.yaml")
        if home
        else Path("/etc/otaman/human-roster.yaml")
    )


def _tenant_roster_ids(path: Path) -> set[str]:
    """The ``roster_id`` set from the tenant provisioning roster (values-free)."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/unreadable → no ids
        return set()
    humans = data.get("humans") if isinstance(data, dict) else None
    if not isinstance(humans, list):
        return set()
    return {str(h["roster_id"]) for h in humans if isinstance(h, dict) and h.get("roster_id")}


def tenant_roster_entries(path: Path | None = None) -> list[dict]:
    """The tenant provisioning roster rows (values-free: roster_id, fingerprint,
    key_type, comment, added_at — never a raw key). Empty if absent/malformed.
    """
    p = path or tenant_roster_path()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/unreadable → empty
        return []
    humans = data.get("humans") if isinstance(data, dict) else None
    return [h for h in humans if isinstance(h, dict)] if isinstance(humans, list) else []


def _platform_roster_ids(program_root: Path) -> set[str]:
    """Fallback: the program platform.yaml human-roster (name/email) — used on
    CE/self-serve tenants (and tests) that have no /etc/otaman roster yet."""
    try:
        from otaman_core.human_roster import load_human_roster

        roster = load_human_roster(program_root / "platform.yaml")
    except Exception:  # noqa: BLE001 - no/broken roster → nothing to match
        return set()
    ids: set[str] = set()
    for h in roster:
        for attr in ("name", "email"):
            val = getattr(h, attr, None)
            if val:
                ids.add(str(val))
    return ids


def roster_drift(program_root: Path, *, tenant_path: Path | None = None) -> list[dict]:
    """Enrolled identities that CANNOT be verified (console-roster-verification 1.2).

    The two rosters must stay connected: every enrolled fingerprint's
    ``roster_id`` in the tenant enrollment store
    (``/etc/otaman/human-roster.yaml``) SHALL have a matching entry
    (name/email) in the program platform.yaml ``human-roster`` — otherwise the
    human is enrolled-but-unverifiable (Roman's exact failure: sshd-set
    OTAMAN_HUMAN=roman against an EMPTY platform.yaml roster → unverified).

    Returns one ``{roster_id, fingerprint}`` per drifted enrollment. Empty when
    the tenant store is absent (CE/self-serve) or fully in sync.
    """
    entries = tenant_roster_entries(tenant_path or tenant_roster_path())
    if not entries:
        return []
    verifiable = _platform_roster_ids(program_root)
    drift: list[dict] = []
    for e in entries:
        rid = str(e.get("roster_id") or "")
        if rid and rid not in verifiable:
            drift.append({"roster_id": rid, "fingerprint": str(e.get("fingerprint") or "")})
    return drift


@dataclass(frozen=True)
class ConsoleIdentity:
    """Who the console attributes an approval to."""

    operator: str
    verified: bool

    @property
    def audit_label(self) -> str:
        """The exact string stamped into the approval's audit record."""
        return self.operator if self.verified else f"{self.operator} (unverified-identity)"


def resolve_identity(program_root: Path) -> ConsoleIdentity:
    """Resolve the acting operator from `OTAMAN_HUMAN`, validated vs the roster.

    Verification sources (union): the tenant provisioning roster
    ``/etc/otaman/human-roster.yaml`` (deploy 2.1 — matches OTAMAN_HUMAN
    against ``roster_id``) and, as a fallback, the program platform.yaml
    human-roster (name/email) for CE/self-serve tenants without the tenant
    roster yet.

    - present AND in a roster → verified.
    - present but in NO roster → unverified (a value we cannot vouch for).
    - absent → unverified fallback operator (the spec's require-explicit-id +
      mark-unverified path; the caller may still prompt for a name).
    """
    raw = os.environ.get("OTAMAN_HUMAN", "").strip()
    if not raw:
        return ConsoleIdentity(operator=_ABSENT_OPERATOR, verified=False)

    known = _tenant_roster_ids(tenant_roster_path()) | _platform_roster_ids(program_root)
    return ConsoleIdentity(operator=raw, verified=raw in known)


def identity_badge(identity: ConsoleIdentity) -> str:
    """The persistent title-bar badge string (Roman's request via deploy 2.1).

    Surfaces resolve_identity()'s verdict BEFORE the human acts — three states:
    - verified                    → ``✓ Verified(<name>)``
    - OTAMAN_HUMAN set, no match   → ``⚠ Unverified(<value>)``  (self-diagnosing)
    - OTAMAN_HUMAN absent          → ``⚠ Unverified(none)``

    Showing the unmatched VALUE in state 2 makes a name-format mismatch (a
    full display name vs the roster's lowercase handle) obvious at a glance,
    without log spelunking.
    """
    if identity.verified:
        return f"✓ Verified({identity.operator})"
    if identity.operator == _ABSENT_OPERATOR:
        return "⚠ Unverified(none)"
    return f"⚠ Unverified({identity.operator})"


__all__ = [
    "ConsoleIdentity",
    "identity_badge",
    "resolve_identity",
    "roster_drift",
    "tenant_roster_entries",
    "tenant_roster_path",
]
