"""`otaman complete` must not report success for a change that does not exist.

The defect, hit live: `otaman complete uniform-ce-directory-layout --all`
against a change that exists in neither `changes/` nor `archive/` printed
"Bus task-complete sent" and "spec-agent will tick tasks.md on next session
start", and exited 0. Nothing will ever tick, because there is nothing to tick.

The expensive shape is a typo. `otaman complete sesion-runtime-freshness --all`
exits 0 while the real change stays unticked, and the caller has been told the
work is filed — so a near-miss is named, not just refused.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.complete import check_change_exists


@pytest.fixture
def program(tmp_path):
    """A program root whose platform.yaml points at a sibling specs repo."""
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nspecs:\n  path: ../specs\nrepos: []\n", encoding="utf-8"
    )
    changes = tmp_path / "specs" / "openspec" / "changes"
    changes.mkdir(parents=True)
    (changes / "session-runtime-freshness").mkdir()
    (changes / "knowledge-v2-retrieval-and-lifecycle").mkdir()
    (changes / "archive").mkdir()
    return root, changes


def test_a_real_change_is_accepted_silently(program):
    root, _ = program
    assert check_change_exists(root, "session-runtime-freshness") == (True, "")


def test_a_nonexistent_change_is_refused(program):
    """The live defect: this used to print 'task-complete sent' and exit 0."""
    root, changes = program
    ok, note = check_change_exists(root, "uniform-ce-directory-layout")
    assert not ok
    assert str(changes) in note, "name where we looked, or the caller cannot tell why"


def test_a_typo_is_told_what_it_probably_meant(program):
    """The expensive case — a silent exit 0 leaves the real change unticked."""
    root, _ = program
    ok, note = check_change_exists(root, "sesion-runtime-freshness")
    assert not ok
    assert "Did you mean" in note and "session-runtime-freshness" in note


def test_a_far_miss_lists_what_is_actually_there(program):
    root, _ = program
    ok, note = check_change_exists(root, "zzzzz-nothing-like-this")
    assert not ok
    assert "Active changes" in note and "session-runtime-freshness" in note


def test_an_archived_change_is_accepted_but_said_out_loud(program):
    """Filing against an archived change is odd, not wrong — the work may
    genuinely predate the archive. Accept it and say so."""
    root, changes = program
    (changes / "archive" / "old-change").mkdir()
    ok, note = check_change_exists(root, "old-change")
    assert ok and "archived" in note


def test_archive_is_not_offered_as_an_active_change(program):
    """`archive/` is a directory in changes/ but is not a change."""
    root, _ = program
    _, note = check_change_exists(root, "zzzzz-nothing-like-this")
    # Assert on the listing LINE, not the whole note: the note embeds an
    # absolute path, and pytest's tmp dir for this very test is named
    # "test_archive_is_not_offered_a0" — a substring check on the whole string
    # matches the directory name and passes for the wrong reason.
    listed = [ln for ln in note.splitlines() if "Active changes" in ln]
    assert listed and "archive" not in listed[0]


def test_an_unreadable_specs_repo_warns_and_proceeds(tmp_path):
    """Losing the gate because a sibling is not checked out is acceptable and
    stated. Refusing would make `complete` unusable wherever specs is absent —
    and the gate exists to catch typos, not to police checkouts."""
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nspecs:\n  path: ../nowhere\nrepos: []\n", encoding="utf-8"
    )
    ok, note = check_change_exists(root, "anything-at-all")
    assert ok, "an unresolvable specs repo must not block a completion"
    assert "could not verify" in note, "but it must never pass silently"


def test_no_specs_path_configured_also_warns_rather_than_refusing(tmp_path):
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("project: demo\nrepos: []\n", encoding="utf-8")
    ok, note = check_change_exists(root, "anything-at-all")
    assert ok and "could not verify" in note
