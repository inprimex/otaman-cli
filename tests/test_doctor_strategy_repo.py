"""team-mode Phase A 1.1 — doctor's registry-home check.

A program actually using the registries (spec policy process.level >= solutions)
must configure program.registries.strategy_repo; a missing/unresolvable key is a
loud ERROR (no silent find_business_repo guess). Spec-first programs are skipped.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.doctor import _check_strategy_repo, _print_strategy_repo_report


@pytest.fixture(autouse=True)
def _no_strategy_env(monkeypatch):
    monkeypatch.delenv("OTAMAN_STRATEGY_DIR", raising=False)


def _prog(tmp_path, *, level="outcomes", strategy=None):
    root = tmp_path / "meta"
    root.mkdir()
    lines = ["project: demo", "version: '1.0'", "repos:"]
    if strategy:
        lines.append(f"  - {{name: {strategy}, path: ../{strategy}, owner: cofounder-agent}}")
        (tmp_path / strategy).mkdir()
    else:
        lines.append("  - {name: r, path: ., owner: main-agent}")
    lines.append(f"spec_policy:\n  process:\n    level: {level}")
    if strategy:
        lines.append(f"program:\n  registries:\n    strategy_repo: {strategy}")
    root.joinpath("platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_error_when_registries_in_use_and_no_key(tmp_path):
    r = _check_strategy_repo(_prog(tmp_path, level="outcomes", strategy=None))
    assert r["applicable"] is True and r["ok"] is False
    assert "strategy_repo" in r["detail"]


def test_ok_when_key_set_and_resolves(tmp_path):
    r = _check_strategy_repo(_prog(tmp_path, level="solutions", strategy="strat"))
    assert r["applicable"] is True and r["ok"] is True and "strat" in r["detail"]


def test_not_applicable_at_spec_first(tmp_path):
    # spec-first programs don't use the registries → no error
    assert _check_strategy_repo(_prog(tmp_path, level="spec-first"))["applicable"] is False


def test_report_error_names_fix(tmp_path, capsys):
    _print_strategy_repo_report(_check_strategy_repo(_prog(tmp_path, level="outcomes")))
    out = capsys.readouterr().out
    assert "ERROR" in out and "program.registries.strategy_repo" in out


def test_report_quiet_when_not_applicable(tmp_path, capsys):
    _print_strategy_repo_report(_check_strategy_repo(_prog(tmp_path, level="spec-first")))
    assert capsys.readouterr().out == ""
