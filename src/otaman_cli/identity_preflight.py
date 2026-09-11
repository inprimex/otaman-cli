"""Proactive human-identity-chain preflight (identity-chain-preflight).

Doctor's Roster Sync is reactive: it compares two POPULATED sources and says
nothing when the enrollment store is empty — so a tenant where the chain was
never wired (zero SSH keys carrying ``environment="OTAMAN_HUMAN=<id>"``) looks
identical to a perfectly-synced one, and the gap only surfaces as
"unverified-identity" inside the console, well after the fact (riseapps, mildef).

This preflight checks the chain's PREREQUISITES independently of enrollment
state: it warns when no annotated key exists at all, and warns distinctly when
annotations exist but sshd would silently ignore them. Reusable by doctor,
``otaman scan`` and ``otaman init``. Paths are injectable for tests.
"""

from __future__ import annotations

import re
from pathlib import Path

_ANNOTATION_RE = re.compile(r'environment="OTAMAN_HUMAN=')


def _default_authorized_keys() -> Path:
    return Path.home() / ".ssh" / "authorized_keys"


def _default_sshd_config() -> Path:
    return Path("/etc/ssh/sshd_config")


def _count_annotated_keys(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and _ANNOTATION_RE.search(line)
    )


def _sshd_env_state(path: Path) -> tuple[bool, bool]:
    """(PermitUserEnvironment honors OTAMAN_HUMAN, has AuthorizedKeysCommand).

    sshd's default for ``PermitUserEnvironment`` is ``no``. A value of ``yes`` (or
    a list naming ``OTAMAN_HUMAN``) honors the annotation; anything else does not.
    An ``AuthorizedKeysCommand`` is an equivalent way to inject the env, so its
    presence suppresses the inert-annotation warning."""
    permit = False  # sshd default is off
    has_akc = False
    if not path.is_file():
        return permit, has_akc
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return permit, has_akc
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if low.startswith("permituserenvironment"):
            parts = s.split(None, 1)
            val = parts[1].strip().lower() if len(parts) > 1 else ""
            permit = val == "yes" or "otaman_human" in val
        elif low.startswith("authorizedkeyscommand") and not low.startswith(
            "authorizedkeyscommanduser"
        ):
            has_akc = True
    return permit, has_akc


def identity_chain_preflight(
    *, authorized_keys_path: Path | None = None, sshd_config_path: Path | None = None
) -> list[str]:
    """Warnings about the human-identity chain's prerequisites — empty list when
    the chain looks wired. Never raises."""
    ak = authorized_keys_path or _default_authorized_keys()
    sshd = sshd_config_path or _default_sshd_config()
    warnings: list[str] = []

    if _count_annotated_keys(ak) == 0:
        warnings.append(
            "identity chain unwired: no authorized_keys entry carries "
            'environment="OTAMAN_HUMAN=<id>" — every SSH session resolves as '
            f'unverified. Add environment="OTAMAN_HUMAN=<roster-id>" to keys in {ak}.'
        )
        return warnings  # nothing annotated → the inert-honoring check is moot

    permit, has_akc = _sshd_env_state(sshd)
    if not permit and not has_akc:
        warnings.append(
            'identity chain inert: authorized_keys carry environment="OTAMAN_HUMAN=" '
            f"but sshd PermitUserEnvironment is not enabled in {sshd} (and no "
            "AuthorizedKeysCommand) — the annotations are silently ignored. Set "
            "`PermitUserEnvironment yes` (or add an AuthorizedKeysCommand)."
        )
    return warnings


def surface_preflight_warnings(printer) -> int:
    """Print each identity-chain preflight warning via *printer* (a ``UI.warn``-
    like callable). Returns how many were printed. Used to surface the same check
    at ``otaman scan`` / ``otaman init`` setup time, before any console session."""
    warnings = identity_chain_preflight()
    for w in warnings:
        printer(w)
    return len(warnings)


__all__ = ["identity_chain_preflight", "surface_preflight_warnings"]
