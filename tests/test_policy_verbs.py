"""policy-engine 2.1 (PR1) — read-only `otaman policy list|show|validate`.

Thin operator surface over otaman_core.policy: it renders the shipped/selected
policy and reports resolution errors, reimplementing none of the composition
algebra (that is core's, tested there).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands.policy import cmd_policy  # noqa: E402


@pytest.fixture
def program(tmp_path: Path, monkeypatch):
    """A CE program meta root selecting the shipped git standard, no policy/ dir."""
    (tmp_path / ".agents").mkdir()
    (tmp_path / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\npolicies:\n  git: standard\nrepos: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return tmp_path


def test_list_shows_git_pack_and_shipped_fallback(program, capsys):
    rc = cmd_policy(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "git" in out
    assert "standard" in out and "shipped standard" in out  # no policy/ on disk


def test_show_git_renders_effective_rules(program, capsys):
    rc = cmd_policy(["show", "git"])
    out = capsys.readouterr().out
    assert rc == 0
    # a couple of the shipped git-standard narrow-only rules
    assert "force_push_forbidden" in out
    assert "owner_admission_required" in out


def test_show_defaults_to_git(program, capsys):
    rc = cmd_policy(["show"])
    assert rc == 0
    assert "force_push_forbidden" in capsys.readouterr().out


def test_show_json_is_valid_object(program, capsys):
    rc = cmd_policy(["show", "git", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pack"] == "git"
    assert payload["rules"]["force_push_forbidden"] is True
    assert payload["loosening_refused"] == []


def test_validate_clean(program, capsys):
    rc = cmd_policy(["validate"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_show_unknown_pack_errors(program, capsys):
    # no shipped standard for an unknown pack → PolicyError → exit 2
    rc = cmd_policy(["show", "nonesuch"])
    assert rc == 2


def test_validate_missing_selected_policy_fails(tmp_path, monkeypatch):
    (tmp_path / ".agents").mkdir()
    # select a non-shipped policy name that has no file on disk → resolution error
    (tmp_path / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\npolicies:\n  git: house-rules\nrepos: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    rc = cmd_policy(["validate"])
    assert rc == 2  # house-rules selected but not found, no standard fallback


def test_not_in_project_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    assert cmd_policy(["list"]) == 1


def test_unknown_action_errors(program):
    assert cmd_policy(["frobnicate"]) != 0
