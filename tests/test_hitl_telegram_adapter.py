"""hitl-confirmation-adapters 2.1 — cli-side TelegramAdapter wrapper.

The confirmation MECHANISM lives in otaman-bridge (`otaman_bridge.hitl_telegram`,
bridge PR #60); this covers the thin cli-side wrapper that registers the
adapter and builds the cli-native `ConfirmationResult`. Tests inject a FAKE
`otaman_bridge.hitl_telegram` into sys.modules so they are deterministic and
independent of whether the real bridge (and its own hitl.yaml lookup) is
installed — exercising both the importable path and the guarded-ImportError
transparent-no-op.
"""

from __future__ import annotations

import types
from types import SimpleNamespace

import pytest

from otaman_cli.hitl import adapters
from otaman_cli.hitl import config as cfg
from otaman_cli.hitl.adapters import TelegramAdapter, TOTPAdapter, TTYAdapter, select_adapter

_BRIDGE_MOD = "otaman_bridge.hitl_telegram"


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved = list(adapters._REGISTRY)
    try:
        yield
    finally:
        adapters._REGISTRY[:] = saved


def _install_fake_bridge(monkeypatch, *, enrolled=True, result=None, calls=None):
    """Inject a fake otaman_bridge.hitl_telegram with controllable behavior."""
    mod = types.ModuleType(_BRIDGE_MOD)

    def is_enrolled(email=None, *, path=None):
        return enrolled

    def confirm_via_telegram(
        description, *, email=None, expected_phrase="CONFIRM", timeout_seconds=540
    ):
        if calls is not None:
            calls.append((description, email, expected_phrase, timeout_seconds))
        return result if result is not None else SimpleNamespace(approved=True, human_id="a@x.io")

    mod.is_enrolled = is_enrolled
    mod.confirm_via_telegram = confirm_via_telegram
    monkeypatch.setitem(__import__("sys").modules, _BRIDGE_MOD, mod)
    return mod


def _force_bridge_absent(monkeypatch):
    # sys.modules[name] = None makes `import name` raise ImportError.
    monkeypatch.setitem(__import__("sys").modules, _BRIDGE_MOD, None)


# ---------------------------------------------------------------------------
# is_configured — enrollment gate + guarded import


def test_unconfigured_when_bridge_absent(monkeypatch):
    _force_bridge_absent(monkeypatch)
    assert TelegramAdapter().is_configured() is False


def test_unconfigured_when_not_enrolled(monkeypatch):
    _install_fake_bridge(monkeypatch, enrolled=False)
    assert TelegramAdapter().is_configured() is False


def test_configured_when_enrolled(monkeypatch):
    _install_fake_bridge(monkeypatch, enrolled=True)
    assert TelegramAdapter().is_configured() is True


# ---------------------------------------------------------------------------
# confirm — fail-closed result mapping


def test_confirm_maps_approved_and_human_id(monkeypatch):
    _install_fake_bridge(monkeypatch, result=SimpleNamespace(approved=True, human_id="roman@x.io"))
    r = TelegramAdapter().confirm("approve X")
    assert r.approved is True
    assert r.adapter == "telegram"
    assert r.human_id == "roman@x.io"


def test_confirm_maps_denied_keeps_human(monkeypatch):
    _install_fake_bridge(monkeypatch, result=SimpleNamespace(approved=False, human_id="roman@x.io"))
    r = TelegramAdapter().confirm("approve X")
    assert r.approved is False
    assert r.human_id == "roman@x.io"


def test_confirm_maps_timeout_to_not_approved_no_human(monkeypatch):
    _install_fake_bridge(monkeypatch, result=SimpleNamespace(approved=False, human_id=None))
    r = TelegramAdapter().confirm("approve X")
    assert r.approved is False
    assert r.human_id is None


def test_confirm_calls_mechanism_positionally(monkeypatch):
    calls: list = []
    _install_fake_bridge(monkeypatch, calls=calls)
    TelegramAdapter().confirm("do the thing")
    # description passed positionally; bridge owns email/phrase/timeout defaults.
    assert calls == [("do the thing", None, "CONFIRM", 540)]


# ---------------------------------------------------------------------------
# selection — strength ordering, no silent downgrade


def test_default_registry_includes_telegram():
    names = [a.name for a in adapters.registered_adapters()]
    assert "telegram" in names
    assert "totp" in names
    assert "tty" in names


def test_telegram_selected_when_enrolled_and_no_totp(monkeypatch):
    _install_fake_bridge(monkeypatch, enrolled=True)
    # No TOTP enrollment (isolated tmp hitl.yaml is empty) → telegram (20) is
    # the strongest CONFIGURED adapter, so it beats the TTY default.
    selected = select_adapter()
    assert isinstance(selected, TelegramAdapter)
    assert not isinstance(selected, TTYAdapter)


def test_totp_beats_telegram_when_both_enrolled(monkeypatch):
    _install_fake_bridge(monkeypatch, enrolled=True)
    cfg.set_totp_enrollment("roman@x.io", "HITL_TOTP_roman-x-io")  # TOTP now configured too
    assert TelegramAdapter().is_configured() is True
    assert TOTPAdapter().is_configured() is True
    # 30 > 20 → TOTP required; Telegram enrolled but not selected (no downgrade).
    assert isinstance(select_adapter(), TOTPAdapter)


def test_telegram_absent_does_not_break_default_selection(monkeypatch):
    # With bridge absent and nothing else configured, selection falls back to
    # the always-available TTY default — the transparent no-op property.
    _force_bridge_absent(monkeypatch)
    assert isinstance(select_adapter(), TTYAdapter)
