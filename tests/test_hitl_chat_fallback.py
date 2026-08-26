"""hitl-confirmation-adapters 1.3 (PR 1) — chat-fallback security primitives + adapter.

Covers session-mode detection, the tenant nonce state (one-pending + daily
cap), phrase mint/append/verify (incl. the spec's invalidate-on-bad-confirm),
the values-free audit log, and the ChatAdapter gating (flag + autonomous
refusal + strongest-configured-disables + two-step-not-single-step).

Isolation: the autouse conftest fixture redirects the tenant hitl.yaml,
chat state, and audit log to tmp. verify_phrase takes an explicit clock, so
tests are deterministic without mocking time.
"""

from __future__ import annotations

import json
import sys

import pytest

from otaman_cli.hitl import adapters
from otaman_cli.hitl import chat_fallback as cf
from otaman_cli.hitl import config as cfg
from otaman_cli.hitl.adapters import ChatAdapter, TOTPAdapter, TTYAdapter, select_adapter

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only: 0600 chmod semantics don't apply on Windows"
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved = list(adapters._REGISTRY)
    try:
        yield
    finally:
        adapters._REGISTRY[:] = saved


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hitl-chat.json"


def _enable_flag():
    path = cfg.hitl_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    path.write_text(yaml.safe_dump({"allow_insecure_chat_approval": True}), encoding="utf-8")


# ---------------------------------------------------------------------------
# session mode / autonomous marker


def test_session_mode_unset_is_none(monkeypatch):
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    assert cf.session_mode() is None
    assert cf.is_autonomous_context() is False


@pytest.mark.parametrize(
    "mode,autonomous",
    [("interactive", False), ("headless", True), ("cron", True), ("garbage", False)],
)
def test_session_mode_values(monkeypatch, mode, autonomous):
    monkeypatch.setenv("OTAMAN_SESSION_MODE", mode)
    assert cf.is_autonomous_context() is autonomous


# ---------------------------------------------------------------------------
# flag + phrase


def test_chat_approval_flag():
    assert cf.chat_approval_enabled({}) is False
    assert cf.chat_approval_enabled({"allow_insecure_chat_approval": False}) is False
    assert cf.chat_approval_enabled({"allow_insecure_chat_approval": True}) is True


def test_generate_phrase_shape_and_uniqueness():
    a, b = cf.generate_phrase(), cf.generate_phrase()
    assert cf.is_phrase_shaped(a) and cf.is_phrase_shaped(b)
    assert a.count("-") == 3
    assert a != b  # 64^4 space → collisions vanishingly unlikely


# ---------------------------------------------------------------------------
# proposal-document phrase block


def test_append_phrase_and_replace_not_stack(tmp_path):
    doc = tmp_path / "proposal.md"
    doc.write_text("# Proposal\n\nbody\n", encoding="utf-8")
    cf.append_phrase_to_proposal(doc, "otter-slate-verbena-quill", stem="s1", nonce_id="n1")
    t1 = doc.read_text(encoding="utf-8")
    assert "otter-slate-verbena-quill" in t1
    assert t1.count(cf._BLOCK_START) == 1
    # re-request with a new phrase strips the old block (no stacking)
    cf.append_phrase_to_proposal(doc, "amber-reef-teal-onyx", stem="s1", nonce_id="n2")
    t2 = doc.read_text(encoding="utf-8")
    assert t2.count(cf._BLOCK_START) == 1
    assert "otter-slate-verbena-quill" not in t2
    assert "amber-reef-teal-onyx" in t2
    assert "# Proposal" in t2 and "body" in t2  # original preserved


# ---------------------------------------------------------------------------
# nonce state: one pending + daily cap


def _nonce(stem="s1", phrase="otter-slate-verbena-quill", nid="n1", created=1000):
    return cf.ChatNonce(
        stem=stem,
        nonce_id=nid,
        phrase=phrase,
        human_id="roman",
        session_id="sess",
        created_at=created,
    )


def test_record_and_read_pending(state):
    assert cf.pending_nonce(state) is None
    cf.record_request(state, _nonce(), today="2026-08-26")
    p = cf.pending_nonce(state)
    assert p is not None and p.stem == "s1" and p.phrase == "otter-slate-verbena-quill"


def test_one_pending_replaces_previous(state):
    cf.record_request(state, _nonce(nid="n1", phrase="amber-reef-teal-onyx"), today="2026-08-26")
    cf.record_request(
        state, _nonce(nid="n2", phrase="otter-slate-verbena-quill"), today="2026-08-26"
    )
    assert cf.pending_nonce(state).nonce_id == "n2"


def test_daily_cap(state):
    for i in range(cf.DAILY_CAP):
        assert cf.daily_cap_reached(state, "2026-08-26") is False
        cf.record_request(state, _nonce(nid=f"n{i}"), today="2026-08-26")
    assert cf.daily_cap_reached(state, "2026-08-26") is True
    # a new day resets the counter
    assert cf.daily_cap_reached(state, "2026-08-27") is False


def test_clear_pending_keeps_day_count(state):
    cf.record_request(state, _nonce(), today="2026-08-26")
    cf.clear_pending(state)
    assert cf.pending_nonce(state) is None
    assert cf.requests_used_today(state, "2026-08-26") == 1  # cap still enforced


# ---------------------------------------------------------------------------
# verify_phrase — the security core


def test_verify_no_pending(state):
    ok, reason = cf.verify_phrase(state, "s1", "otter-slate-verbena-quill", now=1000)
    assert ok is False and "no pending" in reason


def test_verify_stem_mismatch(state):
    cf.record_request(state, _nonce(stem="s1"), today="2026-08-26")
    ok, _ = cf.verify_phrase(state, "OTHER", "otter-slate-verbena-quill", now=1000)
    assert ok is False


def test_verify_correct_phrase_consumes(state):
    cf.record_request(state, _nonce(created=1000), today="2026-08-26")
    ok, _ = cf.verify_phrase(state, "s1", "otter-slate-verbena-quill", now=1100)
    assert ok is True
    assert cf.pending_nonce(state) is None  # one-shot: consumed


def test_verify_wrong_phrase_refuses_and_invalidates(state):
    # spec scenario: confirm without the correct phrase → refuse AND invalidate.
    cf.record_request(state, _nonce(created=1000), today="2026-08-26")
    ok, reason = cf.verify_phrase(state, "s1", "wrong-wrong-wrong-wrong", now=1100)
    assert ok is False and "invalidated" in reason
    assert cf.pending_nonce(state) is None  # nonce burned — no retry


def test_verify_expired_phrase(state):
    cf.record_request(state, _nonce(created=1000), today="2026-08-26")
    ok, reason = cf.verify_phrase(
        state, "s1", "otter-slate-verbena-quill", now=1000 + cf.PHRASE_TTL_SECONDS + 1
    )
    assert ok is False and "expired" in reason
    assert cf.pending_nonce(state) is None


# ---------------------------------------------------------------------------
# audit


def test_audit_appends_provenance_never_the_phrase(tmp_path):
    log = tmp_path / "audit.log"
    cf.audit(
        log,
        action="confirm",
        stem="s1",
        nonce_id="n1",
        human_id="roman",
        session_id="sess",
        outcome="approved",
        timestamp="2026-08-26T14:00:00Z",
    )
    text = log.read_text(encoding="utf-8")
    rec = json.loads(text.strip())
    assert (
        rec["action"] == "confirm" and rec["outcome"] == "approved" and rec["human_id"] == "roman"
    )
    # phrase must never be in the audit record
    assert "phrase" not in rec


@_POSIX_ONLY
def test_audit_and_state_are_0600(tmp_path, state):
    log = tmp_path / "audit.log"
    cf.audit(
        log,
        action="request",
        stem="s",
        nonce_id="n",
        human_id="h",
        session_id="s",
        outcome="ok",
        timestamp="t",
    )
    cf.record_request(state, _nonce(), today="2026-08-26")
    assert (log.stat().st_mode & 0o777) == 0o600
    assert (state.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# ChatAdapter gating


def test_chat_unconfigured_without_flag(monkeypatch):
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    assert ChatAdapter().is_configured() is False


def test_chat_configured_with_flag_and_nothing_stronger(monkeypatch):
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # chat is the NON-TTY fallback
    _enable_flag()
    assert ChatAdapter().is_configured() is True
    # it becomes the selected adapter (strongest CONFIGURED; TTY is default-only)
    assert isinstance(select_adapter(), ChatAdapter)


def test_interactive_tty_takes_precedence_over_chat(monkeypatch):
    # Live 4.1 finding (spec PR #232): a human at a real terminal must get the
    # normal TTY confirmation, NOT the chat two-step — even with the flag set.
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    _enable_flag()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert ChatAdapter().is_configured() is False
    assert isinstance(select_adapter(), TTYAdapter)


def test_chat_disabled_in_autonomous_context(monkeypatch):
    _enable_flag()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("OTAMAN_SESSION_MODE", "cron")
    assert ChatAdapter().is_configured() is False
    # falls back to the TTY default (which itself refuses no-TTY)
    assert isinstance(select_adapter(), TTYAdapter)


def test_totp_enrollment_disables_chat(monkeypatch):
    # strongest-configured-wins: enrolling TOTP disables chat even with the flag.
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _enable_flag()
    cfg.set_totp_enrollment("roman@x.io", "HITL_TOTP_roman-x-io")
    assert TOTPAdapter().is_configured() is True
    assert ChatAdapter().is_configured() is False
    assert isinstance(select_adapter(), TOTPAdapter)


def test_chat_confirm_never_single_steps(monkeypatch):
    _enable_flag()
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    result = ChatAdapter().confirm("about to approve X")
    assert result.approved is False  # single-shot confirm can't approve chat
    assert result.adapter == "chat"
