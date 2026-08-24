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


# Registry. TOTP/chat/messenger append via register_adapter() as they land.
# TTY is always present and is the default fallback.
_REGISTRY: list[ConfirmationAdapter] = [TTYAdapter()]


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
    "TTYAdapter",
    "confirm_human_decision",
    "register_adapter",
    "registered_adapters",
    "select_adapter",
]
