"""hitl-default-approver 2.1/2.2 — the shared approver-eligibility resolution.

One helper answers "may this human work with proposals?" for BOTH HITL
confirmation and console approval, so the two can't diverge. Three outcomes:
unresolved (unchanged), refused (resolved non-approver), approved.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.approver_eligibility import refusal_message, resolve_eligibility


def _platform(tmp_path: Path, roster: str | None) -> Path:
    p = tmp_path / "platform.yaml"
    body = "project: p\nversion: '1.0'\nrepos: []\n"
    if roster is not None:
        body += "human-roster:\n" + roster
    p.write_text(body, encoding="utf-8")
    return p


def _entry(name: str, roles: str, email: str | None = "x@example.com") -> str:
    line = f"  - name: {name}\n    roles: {roles}\n"
    if email is not None:
        line += f"    email: {email}\n"
    return line


def test_approver_is_approved(tmp_path):
    pf = _platform(tmp_path, _entry("roman", "[approver]"))
    e = resolve_eligibility(pf, "roman")
    assert (e.resolved, e.approved, e.refused) == (True, True, False)


def test_resolved_non_approver_is_refused_and_named(tmp_path):
    pf = _platform(tmp_path, _entry("roman", "[developer]"))
    e = resolve_eligibility(pf, "roman")
    assert (e.resolved, e.approved, e.refused) == (True, False, True)
    msg = refusal_message(e)
    assert "roman" in msg and "approver" in msg


def test_refusal_is_actionable_naming_the_fix(tmp_path):
    # mgmt-agent rollout-gap report: the refusal must say HOW to fix it, not
    # just which role is missing (Roman hit a fail-closed refusal with no next
    # step). The hint names the remedy + the exact roster file.
    pf = _platform(tmp_path, _entry("roman", "[founder]"))
    msg = refusal_message(resolve_eligibility(pf, "roman"))
    assert "add 'approver'" in msg
    assert "roles" in msg
    assert str(pf) in msg  # points at the exact platform.yaml to edit


def test_unknown_human_is_unresolved(tmp_path):
    pf = _platform(tmp_path, _entry("roman", "[approver]"))
    e = resolve_eligibility(pf, "ghost")
    assert (e.resolved, e.approved, e.refused) == (False, False, False)


def test_empty_otaman_human_is_unresolved(tmp_path):
    pf = _platform(tmp_path, _entry("roman", "[approver]"))
    assert resolve_eligibility(pf, "").resolved is False
    assert resolve_eligibility(pf, "   ").resolved is False


def test_no_roster_block_is_unresolved(tmp_path):
    pf = _platform(tmp_path, None)
    assert resolve_eligibility(pf, "roman").resolved is False


def test_missing_platform_file_is_unresolved(tmp_path):
    # never crash the caller — a missing/broken roster resolves to unresolved
    assert resolve_eligibility(tmp_path / "absent.yaml", "roman").resolved is False


def test_email_optional_entry_still_resolves(tmp_path):
    # contract change: HumanRosterEntry.email is now optional (str | None)
    pf = _platform(tmp_path, _entry("roman", "[approver]", email=None))
    e = resolve_eligibility(pf, "roman")
    assert e.approved is True


def test_defaults_to_env_otaman_human(tmp_path, monkeypatch):
    pf = _platform(tmp_path, _entry("roman", "[approver]"))
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert resolve_eligibility(pf).approved is True
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    assert resolve_eligibility(pf).resolved is False
