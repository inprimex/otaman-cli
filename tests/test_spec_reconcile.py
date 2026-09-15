"""ratify-spec-approve-split 1.6 — reconciliation of contradictory ratified records.

A change with `ratified: true` but a stage short of `spec-approved` carries a
human attestation the dispatch gate cannot see. pmeets accumulated at least four
and hand-edited each one. The reporter finds them so `otaman spec approve` can
fix them, and SPLITS the findings so closed work never nags: active = actionable,
archived = history, unparseable = could not be assessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.spec_reconcile import ACTIONABLE, CLOSED, UNREADABLE, by_category, scan


def _change(changes: Path, name: str, data: dict | str, *, archived: bool = False) -> Path:
    base = (changes / "archive") if archived else changes
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else yaml.dump(data, sort_keys=False)
    (d / ".openspec.yaml").write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def changes(tmp_path):
    d = tmp_path / "openspec" / "changes"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# the contradiction itself


def test_active_ratified_short_of_spec_approved_is_actionable(changes):
    _change(
        changes,
        "half-approved",
        {"stage": "approved", "ratified": True, "approved_by": "ratified: roman — urgent"},
    )
    (finding,) = scan(changes)
    assert finding.category == ACTIONABLE
    assert finding.change == "half-approved"
    assert finding.stage == "approved"
    assert finding.ratified_by == "roman"
    assert finding.fix_command == "otaman spec approve half-approved"


def test_authored_and_ratified_is_also_actionable(changes):
    _change(changes, "c", {"stage": "authored", "ratified": True})
    (f,) = scan(changes)
    assert f.category == ACTIONABLE and f.stage == "authored"


def test_spec_approved_and_ratified_is_not_contradictory(changes):
    """The post-fix correct state — ratified AND advanced. Nothing to report."""
    _change(changes, "fine", {"stage": "spec-approved", "ratified": True})
    assert scan(changes) == []


def test_stages_past_spec_approved_are_not_contradictory(changes):
    for i, stage in enumerate(("dispatched", "implemented", "verified", "archived")):
        _change(changes, f"c{i}", {"stage": stage, "ratified": True})
    assert scan(changes) == []


def test_unratified_change_is_never_reported(changes):
    _change(changes, "plain", {"stage": "authored"})
    _change(changes, "explicit-false", {"stage": "approved", "ratified": False})
    assert scan(changes) == []


def test_ratified_must_be_true_not_merely_truthy(changes):
    # a stray string must not be read as a ratification
    _change(changes, "odd", {"stage": "approved", "ratified": "yes"})
    assert scan(changes) == []


# ---------------------------------------------------------------------------
# the split: closed work must not nag (spec-agent's constraint)


def test_archived_contradiction_is_closed_not_actionable(changes):
    _change(
        changes,
        "2026-09-09-old",
        {"stage": "approved", "ratified": True, "approved_by": "ratified: roman — done"},
        archived=True,
    )
    (f,) = scan(changes)
    assert f.category == CLOSED
    assert f.fix_command == ""  # nothing to run for closed work


def test_actionable_sorts_before_closed(changes):
    _change(changes, "zzz-active", {"stage": "approved", "ratified": True})
    _change(changes, "aaa-archived", {"stage": "approved", "ratified": True}, archived=True)
    cats = [f.category for f in scan(changes)]
    assert cats == [ACTIONABLE, CLOSED]  # the thing to DO leads, despite the names


def test_both_categories_reported_together(changes):
    _change(changes, "live", {"stage": "approved", "ratified": True})
    _change(changes, "old", {"stage": "approved", "ratified": True}, archived=True)
    groups = by_category(scan(changes))
    assert [f.change for f in groups[ACTIONABLE]] == ["live"]
    assert [f.change for f in groups[CLOSED]] == ["old"]


# ---------------------------------------------------------------------------
# unparseable records: found in the wild, and silently invisible before this


def test_unparseable_record_is_reported_not_counted_clean(changes):
    """`read_openspec` returns {} for a broken file, so a contradiction inside
    one would be INVISIBLE — the same silent-absence failure this change ends.
    Reproduces the real defect: an unquoted archive stamp with inner colons."""
    _change(
        changes,
        "broken",
        "stage: approved\nratified: true\n"
        "archived: 2026-09-14 [auto-delivery] — gate passed, 6/6 legs (a: b; c: d)\n",
    )
    (f,) = scan(changes)
    assert f.category == UNREADABLE
    assert f.change == "broken"
    assert "unparseable" in f.error.lower()
    assert f.fix_command == ""


def test_unparseable_does_not_hide_other_findings(changes):
    _change(changes, "broken", "a: b: c\n")
    _change(changes, "live", {"stage": "approved", "ratified": True})
    groups = by_category(scan(changes))
    assert [f.change for f in groups[ACTIONABLE]] == ["live"]
    assert [f.change for f in groups[UNREADABLE]] == ["broken"]


def test_empty_openspec_is_clean_not_unreadable(changes):
    """An empty file parses to None — genuinely no ratification, not a failure."""
    _change(changes, "empty", "")
    assert scan(changes) == []


# ---------------------------------------------------------------------------
# tolerance / edges


def test_missing_openspec_file_is_skipped(changes):
    (changes / "no-metadata").mkdir()
    assert scan(changes) == []


def test_absent_and_none_changes_dir(tmp_path):
    assert scan(None) == []
    assert scan(tmp_path / "nope") == []


def test_archive_dir_itself_is_not_treated_as_a_change(changes):
    (changes / "archive").mkdir()
    assert scan(changes) == []


def test_ratifier_falls_back_when_marker_unparsed(changes):
    _change(changes, "a", {"stage": "approved", "ratified": True, "approved_by": "someone else"})
    (f,) = scan(changes)
    assert f.ratified_by == "someone else"


def test_ratifier_empty_when_no_marker(changes):
    _change(changes, "a", {"stage": "approved", "ratified": True})
    (f,) = scan(changes)
    assert f.ratified_by == ""


def test_ratified_at_is_carried(changes):
    _change(
        changes, "a", {"stage": "approved", "ratified": True, "ratified_at": "2026-09-09T10:00:00Z"}
    )
    (f,) = scan(changes)
    assert f.ratified_at == "2026-09-09T10:00:00Z"


def test_unknown_stage_is_contradictory_not_crash(changes):
    _change(changes, "a", {"stage": "not-a-real-stage", "ratified": True})
    (f,) = scan(changes)
    assert f.category == ACTIONABLE and f.stage == "not-a-real-stage"


def test_missing_stage_reports_unknown(changes):
    _change(changes, "a", {"ratified": True})
    (f,) = scan(changes)
    assert f.category == ACTIONABLE and f.stage == "unknown"


# ---------------------------------------------------------------------------
# the CLI surface


@pytest.fixture
def program(tmp_path, changes, monkeypatch):
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "demo", "version": "1.0", "repos": [], "specs": {"path": ".."}}),
        encoding="utf-8",
    )
    return root


def test_reconcile_is_a_known_action():
    from otaman_cli.commands.spec import _ACTIONS

    assert "reconcile" in _ACTIONS


def test_reconcile_exits_zero_when_nothing_actionable(program, changes, capsys):
    _change(changes, "old", {"stage": "approved", "ratified": True}, archived=True)
    from otaman_cli.commands.spec import _cmd_reconcile

    rc = _cmd_reconcile(program, [])
    out = capsys.readouterr().out
    assert rc == 0  # closed work alone is not a failure
    assert "no dispatch-relevant" in out.lower()
    assert "old" in out and "no action" in out.lower()


def test_reconcile_exits_one_and_names_the_fix(program, changes, capsys):
    _change(changes, "live", {"stage": "approved", "ratified": True, "approved_by": "ratified: r"})
    from otaman_cli.commands.spec import _cmd_reconcile

    rc = _cmd_reconcile(program, [])
    out = capsys.readouterr().out
    assert rc == 1
    assert "otaman spec approve live" in out
    assert "hand-edit" in out.lower()  # steers away from the old workaround


def test_reconcile_json_shape(program, changes, capsys):
    _change(changes, "live", {"stage": "approved", "ratified": True})
    _change(changes, "old", {"stage": "approved", "ratified": True}, archived=True)
    _change(changes, "broken", "a: b: c\n")
    from otaman_cli.commands.spec import _cmd_reconcile

    rc = _cmd_reconcile(program, ["--json"])
    import json

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert [x["change"] for x in payload["actionable"]] == ["live"]
    assert payload["actionable"][0]["fix"] == "otaman spec approve live"
    assert [x["change"] for x in payload["already_archived"]] == ["old"]
    assert [x["change"] for x in payload["unreadable"]] == ["broken"]


def test_reconcile_json_zero_when_only_closed(program, changes, capsys):
    _change(changes, "old", {"stage": "approved", "ratified": True}, archived=True)
    from otaman_cli.commands.spec import _cmd_reconcile

    assert _cmd_reconcile(program, ["--json"]) == 0
    capsys.readouterr()
