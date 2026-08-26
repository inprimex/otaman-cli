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

    - present AND in the program's human-roster → verified.
    - present but NOT in the roster → unverified (a value we cannot vouch for).
    - absent → unverified fallback operator (the spec's require-explicit-id +
      mark-unverified path; the caller may still prompt for a name).
    """
    raw = os.environ.get("OTAMAN_HUMAN", "").strip()
    if not raw:
        return ConsoleIdentity(operator="unknown-operator", verified=False)

    try:
        from otaman_core.human_roster import load_human_roster

        roster = load_human_roster(program_root / "platform.yaml")
    except Exception:  # noqa: BLE001 - no/broken roster → cannot verify
        roster = []

    known = set()
    for h in roster:
        for attr in ("name", "email"):
            val = getattr(h, attr, None)
            if val:
                known.add(str(val))
    return ConsoleIdentity(operator=raw, verified=raw in known)


__all__ = ["ConsoleIdentity", "resolve_identity"]
