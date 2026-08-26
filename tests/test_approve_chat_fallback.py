"""hitl-confirmation-adapters 1.3 (PR 2) — `approve request`/`confirm` flow.

The two-step read-to-confirm chat fallback end to end: request appends the
phrase to the proposal doc (never to stdout); confirm verifies the human
echo, then performs the identical ledger-gated privileged approval and posts
the audit + bus notice. Also covers every refusal path (flag off, autonomous
marker, stronger adapter enrolled, wrong/absent phrase burns the nonce).

Isolation: conftest redirects tenant hitl.yaml / chat state / audit / ledger
to tmp; the project fixture builds a tmp bus tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from otaman_cli.commands.approve import cmd_approve
from otaman_cli.hitl import chat_fallback as cf
from otaman_cli.hitl import config as cfg

_STEM = "20260101T000000-cli-agent-to-human-spec-change-request"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text("project: tst\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("OTAMAN_SESSION_MODE", raising=False)
    msg = tmp_path / ".agents" / "bus" / "active" / f"{_STEM}.md"
    msg.write_text(
        "---\nid: req-1\nfrom: cli-agent\nto: human\npriority: normal\n"
        "type: spec-change-request\ntimestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: widget support\n\nbody\n",
        encoding="utf-8",
    )
    return tmp_path


def _enable_flag():
    p = cfg.hitl_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    p.write_text(yaml.safe_dump({"allow_insecure_chat_approval": True}), encoding="utf-8")


def _proposal(project: Path) -> Path:
    return project / ".agents" / "bus" / "active" / f"{_STEM}.md"


def _phrase_from_doc(project: Path) -> str:
    m = re.search(r"Confirmation phrase: \*\*([a-z-]+)\*\*", _proposal(project).read_text("utf-8"))
    assert m, "phrase block not found in proposal"
    return m.group(1)


def _broadcasts(project: Path) -> list[Path]:
    active = project / ".agents" / "bus" / "active"
    return [f for f in active.glob("*.md") if "spec-change-approved" in f.name]


# ---------------------------------------------------------------------------
# request


def test_request_appends_phrase_but_never_prints_it(project, capsys):
    _enable_flag()
    rc = cmd_approve(["request", _STEM])
    assert rc == 0
    phrase = _phrase_from_doc(project)
    assert cf.is_phrase_shaped(phrase)
    out = capsys.readouterr().out
    # stdout names the proposal doc, NEVER the phrase (spec scenario)
    assert phrase not in out
    assert _STEM in out
    # a nonce is now pending + audited
    assert cf.pending_nonce(cf.chat_state_path()).stem == _STEM
    assert "request" in cf.chat_audit_path().read_text("utf-8")


def test_request_refused_when_flag_off(project, capsys):
    rc = cmd_approve(["request", _STEM])
    assert rc == 1
    assert "not enabled" in capsys.readouterr().out
    assert cf.pending_nonce(cf.chat_state_path()) is None


def test_request_refused_in_autonomous_session(project, capsys, monkeypatch):
    _enable_flag()
    monkeypatch.setenv("OTAMAN_SESSION_MODE", "cron")
    rc = cmd_approve(["request", _STEM])
    assert rc == 1
    assert "autonomous" in capsys.readouterr().out


def test_request_refused_when_stronger_adapter_enrolled(project, capsys):
    _enable_flag()
    cfg.set_totp_enrollment("roman@x.io", "HITL_TOTP_roman-x-io")  # TOTP now configured
    rc = cmd_approve(["request", _STEM])
    assert rc == 1
    assert "stronger" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# confirm — happy path


def test_confirm_correct_phrase_approves_and_notices(project):
    _enable_flag()
    cmd_approve(["request", _STEM])
    phrase = _phrase_from_doc(project)
    rc = cmd_approve(["confirm", _STEM, phrase])
    assert rc == 0
    # privileged approval written + ack + chat notice
    assert _broadcasts(project), "spec-change-approved broadcast missing"
    acks = project / ".agents" / "bus" / "active" / "acks"
    assert (acks / f"{_STEM}.human.ack").read_text("utf-8").strip() == "approved"
    active = project / ".agents" / "bus" / "active"
    assert any("chat-approval-notice" in f.name for f in active.glob("*.md"))
    # audit shows approved; nonce consumed; phrase block stripped from doc
    assert "approved" in cf.chat_audit_path().read_text("utf-8")
    assert cf.pending_nonce(cf.chat_state_path()) is None
    assert cf._BLOCK_START not in _proposal(project).read_text("utf-8")


# ---------------------------------------------------------------------------
# confirm — refusal paths


def test_confirm_wrong_phrase_refuses_and_burns_nonce(project):
    _enable_flag()
    cmd_approve(["request", _STEM])
    real = _phrase_from_doc(project)
    rc = cmd_approve(["confirm", _STEM, "wrong-wrong-wrong-wrong"])
    assert rc == 1
    assert not _broadcasts(project)  # nothing approved
    assert cf.pending_nonce(cf.chat_state_path()) is None  # nonce burned
    # even the once-correct phrase now fails — no replay after a bad attempt
    assert cmd_approve(["confirm", _STEM, real]) == 1
    assert not _broadcasts(project)


def test_confirm_without_phrase_is_usage_error(project):
    _enable_flag()
    cmd_approve(["request", _STEM])
    rc = cmd_approve(["confirm", _STEM])
    assert rc == 1
    assert not _broadcasts(project)


def test_confirm_with_no_pending_request(project, capsys):
    _enable_flag()
    rc = cmd_approve(["confirm", _STEM, "otter-slate-verbena-quill"])
    assert rc == 1
    assert "no pending" in capsys.readouterr().out.lower()


def test_normal_approve_still_works_after_refactor(project):
    # regression: the extracted _perform_approval must preserve the TTY path.
    from unittest import mock

    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", return_value="CONFIRM"),
    ):
        rc = cmd_approve(["approve", _STEM])
    assert rc == 0
    assert _broadcasts(project)
