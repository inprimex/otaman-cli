"""outcome-verification-field 1.2 — doctor lint for the verified process level.

At process.level=verified ONLY, a Done outcome lacking the `verification` field
WARNs (naming the id). Lower levels stay untaxed. Reads the raw outcomes registry
(the schema field itself is cofounder-agent's 1.1 — this lint is forward-compatible).
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.doctor import (
    _check_outcome_verification,
    _print_outcome_verification_report,
)


@pytest.fixture
def prog(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    root.mkdir()
    business = tmp_path / "business"
    business.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(business))
    return root, business


def _platform(root, level=None):
    block = "project: demo\n"
    if level:
        block += f"spec_policy:\n  process:\n    level: {level}\n"
    (root / "platform.yaml").write_text(block, encoding="utf-8")


def _outcomes(business, entries):
    import yaml

    (business / "outcomes.yaml").write_text(yaml.safe_dump({"outcomes": entries}), encoding="utf-8")


def test_verified_level_flags_done_without_verification(prog):
    root, business = prog
    _platform(root, level="verified")
    _outcomes(
        business,
        [
            {"id": "JTBD-1", "status": "Done"},  # missing verification → flagged
            {"id": "JTBD-2", "status": "Done", "verification": "measured X↑12%"},  # ok
            {"id": "JTBD-3", "status": "In-Progress"},  # not Done → ignored
        ],
    )
    result = _check_outcome_verification(root)
    assert result["applicable"] is True
    assert result["missing"] == ["JTBD-1"]


def test_lower_level_stays_untaxed(prog):
    root, business = prog
    _platform(root, level="outcomes")  # not verified
    _outcomes(business, [{"id": "JTBD-1", "status": "Done"}])
    result = _check_outcome_verification(root)
    assert result["applicable"] is False and result["missing"] == []


def test_default_level_is_not_verified(prog):
    root, business = prog
    _platform(root)  # no spec_policy → spec-first
    _outcomes(business, [{"id": "JTBD-1", "status": "Done"}])
    assert _check_outcome_verification(root)["applicable"] is False


def test_verified_all_done_verified_is_clean(prog):
    root, business = prog
    _platform(root, level="verified")
    _outcomes(business, [{"id": "JTBD-1", "status": "Done", "verification": "note"}])
    result = _check_outcome_verification(root)
    assert result["applicable"] is True and result["missing"] == []


def test_verified_no_business_repo_is_graceful(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    root.mkdir()
    monkeypatch.delenv("OTAMAN_STRATEGY_DIR", raising=False)
    _platform(root, level="verified")
    # no business repo resolvable → applicable but nothing to lint (no crash)
    result = _check_outcome_verification(root)
    assert result["missing"] == []


def test_report_prints_warn_when_missing(prog, capsys):
    root, business = prog
    _platform(root, level="verified")
    _outcomes(business, [{"id": "JTBD-7", "status": "Done"}])
    _print_outcome_verification_report(_check_outcome_verification(root))
    out = capsys.readouterr().out
    assert "Outcome Verification" in out and "WARN" in out and "JTBD-7" in out


def test_report_quiet_when_not_applicable(prog, capsys):
    root, business = prog
    _platform(root, level="outcomes")
    _outcomes(business, [{"id": "JTBD-1", "status": "Done"}])
    _print_outcome_verification_report(_check_outcome_verification(root))
    assert capsys.readouterr().out == ""
