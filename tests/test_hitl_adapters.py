"""hitl-confirmation-adapters 1.1 — confirmation-adapter framework.

Covers the three load-bearing properties of the framework:
  - unconfigured default is the TTY adapter, byte-identical to today
    (including the no-TTY refusal an agent session cannot bypass);
  - the strongest CONFIGURED adapter is REQUIRED — once one is enrolled,
    TTY stops being an accepted path (no silent downgrade);
  - `approve` is classified HUMAN_DECISION and routes through it.
"""

from __future__ import annotations

from unittest import mock

import pytest

from otaman_cli.hitl import adapters
from otaman_cli.hitl.adapters import (
    ConfirmationResult,
    TTYAdapter,
    confirm_human_decision,
    register_adapter,
    registered_adapters,
    select_adapter,
)
from otaman_cli.safety import (
    HUMAN_DECISION_COMMANDS,
    SafetyTier,
    classify_command,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot/restore the module-global adapter registry per test."""
    saved = list(adapters._REGISTRY)
    try:
        yield
    finally:
        adapters._REGISTRY[:] = saved


class _FakeAdapter:
    def __init__(self, name, strength, configured, approved=True, human_id=None):
        self.name = name
        self.strength = strength
        self._configured = configured
        self._approved = approved
        self._human_id = human_id

    def is_configured(self):
        return self._configured

    def confirm(self, description, *, expected_phrase="CONFIRM"):
        return ConfirmationResult(
            approved=self._approved, adapter=self.name, human_id=self._human_id
        )


# ---------------------------------------------------------------------------
# Default selection — TTY when nothing configured


def test_default_is_tty_when_nothing_configured():
    assert isinstance(select_adapter(), TTYAdapter)


def test_unconfigured_confirm_refuses_without_tty():
    # Mirrors the standing no-TTY refusal (agent Bash-tool session cannot
    # satisfy it) — now proven through the framework entry point.
    with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
        result = confirm_human_decision("do the thing")
    assert isinstance(result, ConfirmationResult)
    assert result.approved is False
    assert result.adapter == "tty"


def test_unconfigured_confirm_approves_on_tty_correct_phrase():
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="CONFIRM"),
    ):
        result = confirm_human_decision("do the thing")
    assert result.approved is True
    assert result.adapter == "tty"


def test_unconfigured_confirm_refuses_on_wrong_phrase():
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="nope"),
    ):
        result = confirm_human_decision("do the thing")
    assert result.approved is False


# ---------------------------------------------------------------------------
# Strongest-configured-required — no silent downgrade


def test_configured_adapter_is_required_over_tty():
    strong = _FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=True)
    register_adapter(strong)
    # TTY is present but not "configured"; the enrolled adapter must win.
    assert select_adapter() is strong


def test_configured_adapter_used_not_tty_delegation():
    # If a configured adapter is selected, the TTY path must NOT run — even
    # when there is a real TTY that would otherwise approve.
    register_adapter(_FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=True, approved=False))
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="CONFIRM"),
    ):
        result = confirm_human_decision("do the thing")
    # The strong adapter refused; TTY did not get to rubber-stamp it.
    assert result.adapter == "totp"
    assert result.approved is False


def test_strongest_of_several_configured_wins():
    register_adapter(_FakeAdapter("chat", adapters.STRENGTH_CHAT, configured=True))
    register_adapter(_FakeAdapter("messenger", adapters.STRENGTH_MESSENGER, configured=True))
    register_adapter(_FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=True))
    assert select_adapter().name == "totp"


def test_result_carries_human_id():
    register_adapter(
        _FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=True, human_id="alice")
    )
    result = confirm_human_decision("do the thing")
    assert result.human_id == "alice"


# ---------------------------------------------------------------------------
# Registry hygiene + classification


def test_register_adapter_is_idempotent_per_name():
    register_adapter(_FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=True))
    register_adapter(_FakeAdapter("totp", adapters.STRENGTH_TOTP, configured=False))
    totps = [a for a in registered_adapters() if a.name == "totp"]
    assert len(totps) == 1
    assert totps[0].is_configured() is False  # last write wins


def test_tty_adapter_is_never_reported_configured():
    assert TTYAdapter().is_configured() is False


def test_approve_is_classified_human_decision():
    assert "approve" in HUMAN_DECISION_COMMANDS
    assert classify_command("approve") is SafetyTier.HUMAN_DECISION


def test_unknown_command_is_unclassified():
    assert classify_command("status") is None
