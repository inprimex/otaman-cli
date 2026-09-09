"""tenant-local-ownership-doctor 1.1 — ~/.local ownership doctor check.

Fast default pass (~/.local, bin, venv roots) ERRORs naming any foreign owner;
--scan deep mode counts foreign-owned files grouped by owner. Detection only.
POSIX-only (ownership is a POSIX concept) — the suite skips elsewhere. Foreign
ownership is simulated by pretending "me" is a different uid (creating files
owned by another user needs root).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="ownership check is POSIX-only")

from otaman_cli.commands.doctor import (  # noqa: E402
    _check_local_ownership,
    _print_local_ownership_report,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    import pathlib

    h = tmp_path / "home"
    (h / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: h))
    return h


def _seed_venv(home):
    venv = home / ".local" / "share" / "myvenv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    return venv


# ---------------------------------------------------------------------------


def test_clean_tenant_self_owned(home):
    # all files owned by the running user == "me" → no foreign owners
    (home / ".local" / "bin" / "tool").write_text("x", encoding="utf-8")
    result = _check_local_ownership(deep=False)
    assert result["applicable"] is True and result["foreign"] == []


def test_foreign_owner_flagged(home, monkeypatch):
    # pretend the tenant is a different uid → everything reads as foreign-owned
    monkeypatch.setattr(os, "getuid", lambda: 999999)
    result = _check_local_ownership(deep=False)
    assert result["applicable"] is True
    paths = {f["path"] for f in result["foreign"]}
    assert str(home / ".local") in paths  # top-level ~/.local named


def test_venv_root_is_checked(home, monkeypatch):
    venv = _seed_venv(home)
    monkeypatch.setattr(os, "getuid", lambda: 999999)  # foreign
    result = _check_local_ownership(deep=False)
    assert str(venv) in {f["path"] for f in result["foreign"]}


def test_deep_scan_counts_by_owner(home, monkeypatch):
    import pwd

    for i in range(3):
        (home / ".local" / f"f{i}").write_text("x", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: 999999)  # everything foreign
    result = _check_local_ownership(deep=True)
    real_owner = pwd.getpwuid(os.stat(home / ".local").st_uid).pw_name
    assert result["scan"] and result["scan"].get(real_owner, 0) >= 3  # grouped by owner


def test_clean_deep_scan_empty(home):
    (home / ".local" / "f").write_text("x", encoding="utf-8")
    result = _check_local_ownership(deep=True)
    assert result["scan"] == {}  # self-owned → nothing foreign


def test_not_applicable_without_local(tmp_path, monkeypatch):
    import pathlib

    h = tmp_path / "empty-home"
    h.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: h))
    assert _check_local_ownership()["applicable"] is False


# ---------------------------------------------------------------------------
# report


def test_report_ok_when_clean(home, capsys):
    _print_local_ownership_report(_check_local_ownership(), deep=False)
    out = capsys.readouterr().out
    assert "Local Ownership" in out and "self-owned" in out and "FAIL" not in out


def test_report_fail_names_owner_and_remediation(home, monkeypatch, capsys):
    monkeypatch.setattr(os, "getuid", lambda: 999999)
    _print_local_ownership_report(_check_local_ownership(deep=True), deep=True)
    out = capsys.readouterr().out
    assert "FAIL" in out and "chown" in out  # named + remediation pointer
    assert "foreign-owned files by owner" in out  # deep counts


def test_report_quiet_when_not_applicable(tmp_path, monkeypatch, capsys):
    import pathlib

    h = tmp_path / "empty-home"
    h.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: h))
    _print_local_ownership_report(_check_local_ownership(), deep=False)
    assert capsys.readouterr().out == ""
