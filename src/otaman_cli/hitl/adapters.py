"""Confirmation-adapter framework for HUMAN-DECISION commands.

hitl-confirmation-adapters 1.1. `otaman approve` (and future
HUMAN-DECISION commands) must execute only on a real, current human
decision — never on an agent's initiative, never as self-approval. That
guard used to be TTY-only. This makes confirmation a pluggable adapter
stack so the platform can verify the HUMAN (TOTP device, authenticated
messenger, …) rather than just the terminal — while keeping the default
identical to today.

Selection rule (design.md): the strongest CONFIGURED adapter is REQUIRED
— configuration rot cannot silently downgrade the guard. When nothing is
configured, the always-available TTY adapter is the default, preserving
today's exact behavior (and its no-TTY refusal). This module ships only
the TTY adapter; TOTP (1.2), chat-fallback (1.3) and the bridge messenger
adapter (2.1) register into the same framework later.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Honest strength ranking (design.md table) — higher wins among CONFIGURED
# adapters. Centralised here so later adapters slot into one ordering.
# TTY is the always-available default, not a "configured" adapter; chat is
# the weakest and only reachable via the insecure opt-in (1.3).
STRENGTH_CHAT = 5
STRENGTH_TTY = 10
STRENGTH_MESSENGER = 20
STRENGTH_TOTP = 30


@dataclass(frozen=True)
class ConfirmationResult:
    """Outcome of a confirmation attempt.

    `human_id` records WHICH human confirmed (roster identity) once
    identity-bearing adapters land; the TTY adapter leaves it None.
    """

    approved: bool
    adapter: str
    human_id: str | None = None


@runtime_checkable
class ConfirmationAdapter(Protocol):
    """A pluggable way to confirm a HUMAN-DECISION command.

    `is_configured()` is the enrollment gate: an adapter that returns True
    enters the strongest-configured-required selection. The TTY default
    returns False — it is the fallback, not a configured adapter.
    """

    name: str
    strength: int

    def is_configured(self) -> bool: ...

    def confirm(
        self, description: str, *, expected_phrase: str = "CONFIRM"
    ) -> ConfirmationResult: ...


class TTYAdapter:
    """Today's behavior: a human's hands on a terminal the agent doesn't own.

    Delegates verbatim to `safety.confirm_human_decision`, so an
    unconfigured install is byte-for-byte identical to before this
    framework existed — including the outright refusal when stdin is not a
    real interactive TTY (an agent Bash-tool session cannot satisfy it).
    """

    name = "tty"
    strength = STRENGTH_TTY

    def is_configured(self) -> bool:
        # TTY is the always-available DEFAULT, never a "configured" adapter:
        # keeping it out of the configured set is what makes an enrolled
        # TOTP/messenger adapter REQUIRED rather than merely preferred.
        return False

    def confirm(self, description: str, *, expected_phrase: str = "CONFIRM") -> ConfirmationResult:
        from otaman_cli.safety import confirm_human_decision as _tty_confirm

        approved = _tty_confirm(description, expected_phrase=expected_phrase)
        return ConfirmationResult(approved=approved, adapter=self.name)


class TOTPAdapter:
    """Verify the HUMAN via an RFC 6238 authenticator code (strength 30).

    hitl-confirmation-adapters 1.2. `is_configured()` is True once at least
    one human has a `totp_secret_ref` in the tenant `hitl.yaml` — at which
    point this becomes the REQUIRED adapter (strongest configured wins) and
    TTY-phrase alone stops being accepted (no silent downgrade).

    `confirm()` reads no secret itself: for each enrolled human it resolves
    the seed on demand via `otaman_core._secrets.resolve` (the ref points at
    the tenant dotenv, 0600) and checks the typed 6-digit code with the
    dep-free `hitl.totp` verifier. The seed value never enters this object,
    the bus, or agent context (Q5: values never exposed). `human_id` is the
    email whose seed matched, so multi-human installs resolve WHO confirmed
    without changing the 1.1 `confirm()` interface.

    Security property that motivates the whole feature: an agent Bash-tool
    session has no interactive TTY and cannot produce a live authenticator
    code, so it can never satisfy this — only a real human with the enrolled
    device can.
    """

    name = "totp"
    strength = STRENGTH_TOTP

    def is_configured(self) -> bool:
        from otaman_cli.hitl.config import load_hitl_config, totp_enrollments

        return bool(totp_enrollments(load_hitl_config()))

    def confirm(self, description: str, *, expected_phrase: str = "CONFIRM") -> ConfirmationResult:
        import time

        from otaman_core._secrets import SecretRef, resolve

        from otaman_cli.hitl.config import load_hitl_config, totp_enrollments
        from otaman_cli.hitl.totp import verify_totp

        enrollments = totp_enrollments(load_hitl_config())
        if not enrollments:
            # Defensive: select_adapter only routes here when configured.
            return ConfirmationResult(approved=False, adapter=self.name)

        print()
        print(description)
        print()

        # A live authenticator code is the human proof; a non-interactive
        # caller (agent Bash session, piped stdin) is refused outright — the
        # same fail-closed stance as the TTY human-decision gate.
        if not sys.stdin.isatty():
            print(
                "Refusing: this action requires a TOTP code from your enrolled "
                "authenticator on an interactive terminal. Non-interactive/scripted "
                "callers — including agent Bash-tool sessions — cannot satisfy this.",
                file=sys.stderr,
            )
            return ConfirmationResult(approved=False, adapter=self.name)

        try:
            code = input("Enter the 6-digit code from your authenticator: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ConfirmationResult(approved=False, adapter=self.name)

        now = int(time.time())
        for email, ref in enrollments.items():
            secret = resolve(SecretRef([ref]))
            if secret and verify_totp(secret, code, timestamp=now):
                return ConfirmationResult(approved=True, adapter=self.name, human_id=email)
        return ConfirmationResult(approved=False, adapter=self.name)


# Registry. chat/messenger append via register_adapter() as they land.
# TTY is always present and is the default fallback; TOTP is registered by
# default so an enrolled install auto-selects it (it reports unconfigured —
# and stays out of the way — until a human enrolls).
_REGISTRY: list[ConfirmationAdapter] = [TTYAdapter(), TOTPAdapter()]


def register_adapter(adapter: ConfirmationAdapter) -> None:
    """Add an adapter to the selection pool (idempotent per name)."""
    _REGISTRY[:] = [a for a in _REGISTRY if a.name != adapter.name] + [adapter]


def registered_adapters() -> list[ConfirmationAdapter]:
    """Snapshot of the current pool (test/introspection helper)."""
    return list(_REGISTRY)


def _default_adapter() -> ConfirmationAdapter:
    for a in _REGISTRY:
        if isinstance(a, TTYAdapter):
            return a
    return TTYAdapter()


def select_adapter() -> ConfirmationAdapter:
    """Return the adapter that MUST satisfy the next confirmation.

    Strongest CONFIGURED adapter wins; if none is configured, the
    always-available TTY default is used (today's behavior). This is the
    no-silent-downgrade rule: the moment a stronger adapter is enrolled it
    becomes required, and TTY stops being an accepted path.
    """
    configured = [a for a in _REGISTRY if a.is_configured()]
    if configured:
        return max(configured, key=lambda a: a.strength)
    return _default_adapter()


def confirm_human_decision(
    description: str, *, expected_phrase: str = "CONFIRM"
) -> ConfirmationResult:
    """Framework entry point for a HUMAN-DECISION confirmation.

    Routes through the selected adapter. Unconfigured → TTY → identical to
    the pre-framework `safety.confirm_human_decision`.
    """
    return select_adapter().confirm(description, expected_phrase=expected_phrase)


__all__ = [
    "STRENGTH_CHAT",
    "STRENGTH_MESSENGER",
    "STRENGTH_TOTP",
    "STRENGTH_TTY",
    "ConfirmationAdapter",
    "ConfirmationResult",
    "TOTPAdapter",
    "TTYAdapter",
    "confirm_human_decision",
    "register_adapter",
    "registered_adapters",
    "select_adapter",
]
