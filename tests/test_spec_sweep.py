"""task-complete-reconciler 1.2 — `otaman spec sweep`.

A filed task-complete promises tasks.md will catch up. Nothing kept that
promise: on pmeets the gap ran ~2 weeks, the lens under-counting 5/11 against a
real 11/11, with no warning anywhere.

The properties under test are the ones that make a reconciler trustworthy
rather than merely present: it must not tick what was retracted, must not tick
an id that two task lines share, must say when it checked and found nothing,
and must say NOT CHECKED rather than 0 when it could not look.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import spec_sweep


def _filing(bus, change, completed, *, stem="20260929T120000-a-to-b-task-complete", ts=None):
    p = bus / f"{stem}.md"
    head = f"---\nid: x\nfrom: a\nto: b\ntype: task-complete\nchange: {change}\n"
    if ts:
        head += f"timestamp: {ts}\n"
    p.write_text(head + f"---\n\n**Completed**: {completed}\n", encoding="utf-8")
    return p


@pytest.fixture
def fleet(tmp_path):
    root = tmp_path / "meta"
    bus = root / ".agents" / "bus" / "active"
    bus.mkdir(parents=True)
    (root / ".agents" / "bus" / "archive").mkdir()
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    changes = tmp_path / "specs" / "openspec" / "changes"
    (changes / "demo").mkdir(parents=True)
    (changes / "demo" / "tasks.md").write_text(
        "# demo — tasks\n\n- [ ] 1.1 @otaman-cli first\n- [ ] 1.2 @otaman-cli second\n"
        "- [x] 1.3 @otaman-cli third\n",
        encoding="utf-8",
    )
    return root, bus, changes


def _plan(fleet, change="demo"):
    root, _, changes = fleet
    return spec_sweep.plan(root, changes, change, {})


# ---------------------------------------------------------------------------
# what is owed


def test_a_filed_untickedtask_is_owed(fleet):
    _filing(fleet[1], "demo", "tasks 1.1")
    assert _plan(fleet).owed == ["1.1"]


def test_an_already_ticked_task_is_counted_not_reapplied(fleet):
    _filing(fleet[1], "demo", "tasks 1.3")
    out = _plan(fleet)
    assert out.owed == [] and out.already == 1


def test_an_unfiled_task_is_left_alone(fleet):
    _filing(fleet[1], "demo", "tasks 1.1")
    out = _plan(fleet)
    assert "1.2" not in out.owed, "only FILED work may be ticked"


def test_an_all_filing_owes_every_unticked_task(fleet):
    _filing(fleet[1], "demo", "all tasks")
    assert _plan(fleet).owed == ["1.1", "1.2"]


def test_a_filing_for_another_change_is_ignored(fleet):
    _filing(fleet[1], "other-change", "all tasks")
    assert _plan(fleet).owed == []


# ---------------------------------------------------------------------------
# the honesty properties


def test_a_change_with_no_filings_does_no_work(fleet):
    out = _plan(fleet)
    assert not out.did_work, "no filings must read as nothing owed, not as an error"


def test_an_ambiguous_id_is_surfaced_and_never_ticked(fleet):
    """`1.7-bis` parses to `1.7` (core's task_id_of stops at a word boundary),
    so a tick aimed at either lands on both. That is a wrong write."""
    root, bus, changes = fleet
    (changes / "demo" / "tasks.md").write_text(
        "- [ ] 1.1 @otaman-cli first\n- [ ] 1.1-bis @otaman-cli variant\n", encoding="utf-8"
    )
    _filing(bus, "demo", "tasks 1.1")
    out = _plan(fleet)
    assert out.ambiguous == ["1.1"]
    assert out.owed == [], "an ambiguous id must not be applied to either line"


def test_a_filing_naming_nothing_readable_is_surfaced(fleet):
    """A filer who wrote prose believes the work is recorded. Silence would
    leave the task un-ticked with nobody looking."""
    _filing(fleet[1], "demo", "live-test", stem="20260929T130000-a-to-b-task-complete")
    out = _plan(fleet)
    assert out.unparseable == ["20260929T130000-a-to-b-task-complete"]


def test_a_message_without_a_completed_line_is_not_a_defect(fleet):
    p = fleet[1] / "20260929T140000-a-to-b-task-complete.md"
    p.write_text(
        "---\nid: x\ntype: task-complete\nchange: demo\n---\n\nprose only\n", encoding="utf-8"
    )
    assert _plan(fleet).unparseable == []


def test_filings_are_matched_by_frontmatter_type_not_filename(fleet):
    """The spec says so explicitly, and this repo has already shipped one bug
    from matching a message's type by its name."""
    root, bus, changes = fleet
    p = bus / "20260929T150000-a-to-b-task-complete.md"
    p.write_text(
        "---\nid: x\ntype: info\nchange: demo\n---\n\n**Completed**: tasks 1.1\n", encoding="utf-8"
    )
    assert _plan(fleet).owed == [], "an info message is not a filing"


def test_not_checked_when_core_lacks_the_reader(fleet, monkeypatch):
    """no-silent-success: a sweep that could not look must not report zero."""
    monkeypatch.setattr(spec_sweep, "_core", lambda: None)
    out = _plan(fleet)
    assert out.error and "1.1" in out.error
    assert out.owed == []


def test_changes_with_filings_skips_archive_and_dirless_entries(fleet):
    _, _, changes = fleet
    (changes / "archive").mkdir()
    (changes / "archive" / "old").mkdir()
    (changes / "archive" / "old" / "tasks.md").write_text("- [ ] 1.1 x\n", encoding="utf-8")
    (changes / "no-tasks").mkdir()
    assert spec_sweep.changes_with_filings(changes) == ["demo"]


# ---------------------------------------------------------------------------
# the command surface


def _run(monkeypatch, root, changes, argv):
    from otaman_cli.commands import spec as S

    monkeypatch.setattr(S, "find_project_root", lambda: root)
    monkeypatch.setattr(S, "_specs_changes_dir", lambda r: changes)
    return S.cmd_spec(["sweep", *argv])


def test_zero_work_is_stated_out_loud(fleet, monkeypatch, capsys):
    """A sweep that prints nothing cannot be told from a sweep that never ran —
    which is exactly how the pmeets backlog stayed invisible."""
    root, _, changes = fleet
    assert _run(monkeypatch, root, changes, []) == 0
    out = capsys.readouterr().out
    assert "Nothing owed" in out and "checked" in out


def test_a_dry_run_writes_nothing(fleet, monkeypatch, capsys):
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    before = (changes / "demo" / "tasks.md").read_text(encoding="utf-8")
    assert _run(monkeypatch, root, changes, []) == 0
    assert (changes / "demo" / "tasks.md").read_text(encoding="utf-8") == before
    assert "Dry run" in capsys.readouterr().out


def test_the_dry_run_names_what_it_would_do(fleet, monkeypatch, capsys):
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    _run(monkeypatch, root, changes, [])
    out = capsys.readouterr().out
    assert "owed: 1.1" in out and "1 tick(s) would be applied" in out


def test_an_unknown_change_is_refused_by_name(fleet, monkeypatch, capsys):
    root, _, changes = fleet
    assert _run(monkeypatch, root, changes, ["--change", "nope"]) == 2
    assert "nope" in capsys.readouterr().out


def test_a_missing_reader_renders_not_checked_not_zero(fleet, monkeypatch, capsys):
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    monkeypatch.setattr(spec_sweep, "_core", lambda: None)
    rc = _run(monkeypatch, root, changes, [])
    out = capsys.readouterr().out
    assert rc == 1 and "NOT CHECKED" in out
    assert "Nothing owed" not in out


# ---------------------------------------------------------------------------
# retraction — the rule that needs a real git history to exercise


def _git(repo: Path, *args: str):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def test_a_filing_older_than_an_untick_does_not_retick(tmp_path):
    """Retraction is expressed by un-ticking. A filing older than the most
    recent un-tick of THAT task is withdrawn evidence — it must neither re-tick
    nor vanish silently, or an over-claimed task can never be taken back.
    """
    import shutil

    if not shutil.which("git"):
        pytest.skip("git required to build the un-tick history")

    root = tmp_path / "meta"
    bus = root / ".agents" / "bus" / "active"
    bus.mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")

    changes = tmp_path / "specs" / "openspec" / "changes"
    demo = changes / "demo"
    demo.mkdir(parents=True)
    tasks = demo / "tasks.md"

    _git(demo, "init", "-q")
    _git(demo, "config", "user.email", "t@example.com")
    _git(demo, "config", "user.name", "t")

    # ticked, then un-ticked — the retraction, committed AFTER the filing below.
    tasks.write_text("- [x] 1.1 @otaman-cli first\n", encoding="utf-8")
    _git(demo, "add", "tasks.md")
    _git(demo, "commit", "-q", "-m", "tick 1.1")
    tasks.write_text("- [ ] 1.1 @otaman-cli first\n", encoding="utf-8")
    _git(demo, "add", "tasks.md")
    _git(demo, "commit", "-q", "-m", "un-tick 1.1 — over-claimed")

    _filing(bus, "demo", "tasks 1.1", ts="2020-01-01T00:00:00Z")  # older than the un-tick

    out = spec_sweep.plan(root, changes, "demo", {})
    assert out.owed == [], "a withdrawn filing must not re-tick the task"
    assert out.retracted == ["1.1"], "and it must be said out loud, not dropped"


def test_a_filing_newer_than_an_untick_still_applies(tmp_path):
    """The other half: re-filing after a retraction is how work gets re-claimed."""
    import shutil

    if not shutil.which("git"):
        pytest.skip("git required to build the un-tick history")

    root = tmp_path / "meta"
    bus = root / ".agents" / "bus" / "active"
    bus.mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    changes = tmp_path / "specs" / "openspec" / "changes"
    demo = changes / "demo"
    demo.mkdir(parents=True)
    tasks = demo / "tasks.md"

    _git(demo, "init", "-q")
    _git(demo, "config", "user.email", "t@example.com")
    _git(demo, "config", "user.name", "t")
    tasks.write_text("- [x] 1.1 @otaman-cli first\n", encoding="utf-8")
    _git(demo, "add", "tasks.md")
    _git(demo, "commit", "-q", "-m", "tick")
    tasks.write_text("- [ ] 1.1 @otaman-cli first\n", encoding="utf-8")
    _git(demo, "add", "tasks.md")
    _git(demo, "commit", "-q", "-m", "un-tick")

    _filing(bus, "demo", "tasks 1.1", ts="2099-01-01T00:00:00Z")  # re-filed after

    out = spec_sweep.plan(root, changes, "demo", {})
    assert out.owed == ["1.1"] and out.retracted == []
