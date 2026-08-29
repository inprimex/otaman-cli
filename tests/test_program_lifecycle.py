"""program-lifecycle-states 2.3 — `otaman program` transitions + authority + broadcast.

Covers status, limit/resume (approver tier), suspend (approver + interactive
confirm), the D4 lifecycle-change broadcast, the D3 refusals (non-approver,
agent session, no identity), and the archive/unarchive not-yet gate. State is
recorded via core's registry (read back through it) and never invented here.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.commands.program import cmd_program


@pytest.fixture
def org(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A CE-layout org with one program `shop` whose roster human roman is an
    approver. cwd is the program meta; OTAMAN_ROOT is unset so resolution walks
    this tree."""
    meta = tmp_path / "orgs" / "acme" / "programs" / "shop" / "shop-meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - name: roman\n    email: roman@example.com\n    roles: [approver]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(meta)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    return tmp_path, meta


def _set_roles(meta: Path, roles: str) -> None:
    (meta / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\nrepos: []\n"
        f"human-roster:\n  - name: roman\n    email: roman@example.com\n    roles: {roles}\n",
        encoding="utf-8",
    )


def _state(org_root: Path, program: str = "shop") -> str:
    from otaman_core.lifecycle import read_program_state

    return read_program_state(org_root / "orgs" / "acme", program)


def _broadcasts(meta: Path) -> list[Path]:
    active = meta / ".agents" / "bus" / "active"
    return [f for f in active.glob("*lifecycle-change*.md") if f.is_file()]


# --- status -------------------------------------------------------------------


def test_status_defaults_to_active(org, capsys, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "active"


# --- limit / resume (approver tier) ------------------------------------------


def test_limit_records_and_broadcasts(org, monkeypatch):
    tmp, meta = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["limit", "--reason", "maintenance"]) == 0
    assert _state(tmp) == "limited"
    bc = _broadcasts(meta)
    assert len(bc) == 1
    text = bc[0].read_text(encoding="utf-8")
    assert "type: lifecycle-change" in text and "to: all" in text
    assert "active → limited" in text and "roman" in text and "maintenance" in text


def test_resume_returns_to_active(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    cmd_program(["limit"])
    assert cmd_program(["resume"]) == 0
    assert _state(tmp) == "active"


def test_noop_when_already_in_state(org, monkeypatch):
    tmp, meta = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    cmd_program(["limit"])
    before = len(_broadcasts(meta))
    assert cmd_program(["limit"]) == 0  # already limited → no-op
    assert len(_broadcasts(meta)) == before  # no second broadcast


# --- suspend (approver + interactive confirm) --------------------------------


def test_suspend_dry_run_needs_no_tty_and_writes_nothing(org, monkeypatch):
    tmp, meta = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    # non-interactive stdin: a real suspend would refuse, but --dry-run previews
    with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
        assert cmd_program(["suspend", "--dry-run"]) == 0
    assert _state(tmp) == "active"  # nothing recorded
    assert _broadcasts(meta) == []


def test_suspend_records_with_tty(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True):
        assert cmd_program(["suspend"]) == 0
    assert _state(tmp) == "suspended"


def test_suspend_without_tty_refused(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    with mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=False):
        assert cmd_program(["suspend"]) != 0
    assert _state(tmp) == "active"  # not recorded


# --- authority refusals (D3) -------------------------------------------------


def test_unknown_human_refused(org, monkeypatch, capsys):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "ghost")
    assert cmd_program(["limit"]) != 0
    assert "roster" in capsys.readouterr().out
    assert _state(tmp) == "active"


def test_resolved_non_approver_refused_actionably(org, monkeypatch, capsys):
    tmp, meta = org
    _set_roles(meta, "[developer]")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["limit"]) != 0
    out = capsys.readouterr().out
    assert "approver" in out and "add 'approver'" in out  # actionable
    assert _state(tmp) == "active"


def test_agent_session_categorically_refused(org, monkeypatch, capsys):
    tmp, _ = org
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    assert cmd_program(["limit"]) != 0
    assert "agents cannot perform" in capsys.readouterr().out
    assert _state(tmp) == "active"


# --- archived guard + archive/unarchive not-yet ------------------------------


def test_archive_and_unarchive_not_available_yet(org, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["archive"]) == 2
    assert cmd_program(["unarchive"]) == 2


def test_unknown_action_errors(org):
    assert cmd_program(["frobnicate"]) != 0
