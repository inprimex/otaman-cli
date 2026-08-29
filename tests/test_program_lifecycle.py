"""program-lifecycle-states 2.3 — `otaman program` transitions + authority + broadcast.

Covers status, limit/resume (approver tier), suspend (approver + interactive
confirm), the D4 lifecycle-change broadcast, the D3 refusals (non-approver,
agent session, no identity), and the archive/unarchive not-yet gate. State is
recorded via core's registry (read back through it) and never invented here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.commands import program as _program
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


def test_unknown_action_errors(org):
    assert cmd_program(["frobnicate"]) != 0


# --- archive / unarchive (HUMAN-DECISION tier + folder-move seam) -------------


def _fake_archive_script(tmp_path: Path, monkeypatch, *, code: int = 0) -> Path:
    """A stub deploy `program-archive.sh` on PATH: --dry-run prints a plan and
    exits 0; a real run exits *code*."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    s = bindir / "program-archive.sh"
    s.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "--dry-run" ]; then\n'
        '    echo "plan.$1: move X -> Y ; write ARCHIVED.yaml"; exit 0\n'
        "  fi\n"
        "done\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    s.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return s


def _make_archived(tmp: Path) -> None:
    from otaman_core.lifecycle import lifecycle_registry_path, record_transition

    record_transition(
        lifecycle_registry_path(tmp / "orgs" / "acme"), "shop", "archived", by="roman"
    )


def test_archive_dry_run_prints_plan(org, monkeypatch, capsys):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["archive", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "teardown plan" in out
    assert "bridge" in out and "pending consumer 2.x" in out and "ARCHIVED.yaml" in out
    assert _state(tmp) == "active"  # nothing mutated


def test_archive_gated_when_folder_mechanism_absent(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *_a, **_k: True)
    # ensure no program-archive.sh on PATH
    monkeypatch.setenv("PATH", str(tmp / "empty-bin"))
    (tmp / "empty-bin").mkdir(exist_ok=True)
    assert cmd_program(["archive"]) == 2
    assert _state(tmp) == "active"  # not recorded — gated before mutation


def test_archive_wired_records_and_calls_seam(org, monkeypatch):
    tmp, meta = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *_a, **_k: True)
    monkeypatch.setattr(_program, "_bridge_unit", lambda *_a, **_k: None)
    _fake_archive_script(tmp, monkeypatch, code=0)
    assert cmd_program(["archive", "--reason", "eol"]) == 0
    assert _state(tmp) == "archived"
    bc = _broadcasts(meta)
    assert bc and "active → archived" in bc[0].read_text(encoding="utf-8")


def test_archive_folder_failure_is_surfaced(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *_a, **_k: True)
    monkeypatch.setattr(_program, "_bridge_unit", lambda *_a, **_k: None)
    _fake_archive_script(tmp, monkeypatch, code=5)  # move failed
    assert cmd_program(["archive"]) == 1  # failure surfaced, not silent success


def test_archive_without_confirmation_refused(org, monkeypatch):
    tmp, _ = org
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *_a, **_k: False)
    _fake_archive_script(tmp, monkeypatch, code=0)
    assert cmd_program(["archive"]) != 0
    assert _state(tmp) == "active"  # not recorded


def test_unarchive_wired_restores(org, monkeypatch):
    tmp, _ = org
    _make_archived(tmp)
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr("otaman_cli.safety.confirm_human_decision", lambda *_a, **_k: True)
    monkeypatch.setattr(_program, "_bridge_unit", lambda *_a, **_k: None)
    _fake_archive_script(tmp, monkeypatch, code=0)
    assert cmd_program(["unarchive"]) == 0
    assert _state(tmp) == "active"


def test_archive_non_approver_refused(org, monkeypatch):
    tmp, meta = org
    _set_roles(meta, "[developer]")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert cmd_program(["archive"]) != 0
    assert _state(tmp) == "active"
