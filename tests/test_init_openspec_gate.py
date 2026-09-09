"""scan-init-edition-backfill 1.3 — init re-evaluates the openspec bootstrap gate.

When a program declares specs.format: openspec but the pinned OpenSpec CLI is
absent (the org skipped it at bootstrap), init OFFERS to install it per the org
convention — never a bare FAIL. Class rule: org-bootstrap gates are per-need,
not once-forever.
"""

from __future__ import annotations

import subprocess

from otaman_cli.commands import init as init_mod
from otaman_cli.commands.init import _ensure_openspec_cli, _openspec_cli_present

# ---------------------------------------------------------------------------
# _openspec_cli_present


def test_present_when_global_command_exists(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/openspec" if name == "openspec" else None
    )
    assert _openspec_cli_present() is True


def test_absent_when_neither_command_nor_npx(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _openspec_cli_present() is False


# ---------------------------------------------------------------------------
# _ensure_openspec_cli


def test_noop_when_format_not_openspec(capsys):
    _ensure_openspec_cli({"specs": {"format": "fallback"}}, interactive=True)
    assert capsys.readouterr().out == ""


def test_noop_when_cli_present(monkeypatch, capsys):
    monkeypatch.setattr(init_mod, "_openspec_cli_present", lambda: True)
    _ensure_openspec_cli({"specs": {"format": "openspec"}}, interactive=True)
    assert capsys.readouterr().out == ""


def test_non_tty_prints_instruction_never_fails(monkeypatch, capsys):
    monkeypatch.setattr(init_mod, "_openspec_cli_present", lambda: False)
    _ensure_openspec_cli({"specs": {"format": "openspec"}}, interactive=False)
    out = capsys.readouterr().out
    assert "not installed" in out
    assert "npm install -g @fission-ai/openspec@" in out  # the precise, pinned command
    assert "offered, not a hard failure" in out  # never FAIL-only


def test_interactive_decline_prints_manual_hint(monkeypatch, capsys):
    monkeypatch.setattr(init_mod, "_openspec_cli_present", lambda: False)
    monkeypatch.setattr(init_mod, "_ask_yes_no_init", lambda *a, **k: False)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    _ensure_openspec_cli({"specs": {"format": "openspec"}}, interactive=True)
    out = capsys.readouterr().out
    assert "Install later with" in out
    assert called == []  # declined → no install attempt


def test_interactive_accept_runs_pinned_install(monkeypatch, capsys):
    # absent before, present after the install succeeds
    states = iter([False, True])
    monkeypatch.setattr(init_mod, "_openspec_cli_present", lambda: next(states))
    monkeypatch.setattr(init_mod, "_ask_yes_no_init", lambda *a, **k: True)
    ran = {}

    def _fake_run(cmd, **kwargs):
        ran["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    _ensure_openspec_cli({"specs": {"format": "openspec"}}, interactive=True)
    out = capsys.readouterr().out
    assert ran["cmd"][:3] == ["npm", "install", "-g"]  # the pinned convention command
    assert "@fission-ai/openspec@" in ran["cmd"][-1]
    assert "Installed OpenSpec CLI" in out


def test_interactive_accept_reports_failure_with_manual_fallback(monkeypatch, capsys):
    monkeypatch.setattr(init_mod, "_openspec_cli_present", lambda: False)  # stays absent
    monkeypatch.setattr(init_mod, "_ask_yes_no_init", lambda *a, **k: True)

    def _fail_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="EACCES")

    monkeypatch.setattr(subprocess, "run", _fail_run)
    _ensure_openspec_cli({"specs": {"format": "openspec"}}, interactive=True)
    out = capsys.readouterr().out
    assert "did not complete cleanly" in out
    assert "Run it manually" in out  # never a dead end
