"""doctor's outcomes-registry loadability check (cofounder-agent Roman requirement).

When the outcomes registry is enabled and its file RESOLVES but does not validate,
the console tree silently degrades to changes-only — so doctor must FAIL loudly
instead of staying green. A present-and-valid file is OK; an absent/unresolved
file is the Registry-Home check's job, not this one.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.doctor import (
    _check_registry_loadability,
    _print_registry_loadability_report,
)


@pytest.fixture
def prog(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    strat = tmp_path / "strat"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    return root, strat


def test_error_when_file_exists_but_invalid(prog):
    root, strat = prog
    # an outcome with no required fields → schema validation fails loudly
    (strat / "outcomes.yaml").write_text("outcomes:\n  - {}\n", encoding="utf-8")
    r = _check_registry_loadability(root)
    assert r["applicable"] is True and r["ok"] is False
    assert "does not validate" in r["detail"] or "fails validation" in r["detail"]


def test_ok_when_file_valid(prog):
    root, strat = prog
    (strat / "outcomes.yaml").write_text("outcomes: []\n", encoding="utf-8")
    r = _check_registry_loadability(root)
    assert r["applicable"] is True and r["ok"] is True


def test_not_applicable_when_no_file(prog):
    # strategy dir resolves but no outcomes.yaml → Registry-Home check owns it
    root, _strat = prog
    r = _check_registry_loadability(root)
    assert r["applicable"] is False


def test_not_applicable_when_outcomes_process_disabled(prog):
    root, strat = prog
    (strat / "outcomes.yaml").write_text("outcomes: []\n", encoding="utf-8")
    # process config lives under the program: block (load_program_extensions)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n"
        "program:\n  processes:\n    outcomes:\n      enabled: false\n",
        encoding="utf-8",
    )
    r = _check_registry_loadability(root)
    assert r["applicable"] is False


def test_report_error_points_to_outcome_list(prog, capsys):
    root, strat = prog
    (strat / "outcomes.yaml").write_text("outcomes:\n  - {}\n", encoding="utf-8")
    _print_registry_loadability_report(_check_registry_loadability(root))
    out = capsys.readouterr().out
    assert "ERROR" in out and "otaman outcome list" in out


def test_report_quiet_when_not_applicable(prog, capsys):
    root, _strat = prog
    _print_registry_loadability_report(_check_registry_loadability(root))
    assert capsys.readouterr().out == ""
