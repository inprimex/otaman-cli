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
    """Two task lines carrying the SAME id: a tick aimed at it lands on both.

    `1.7-bis` reading as `1.7` was the live instance when this was written;
    core #88 taught `task_id_of` the suffix, so that case is fixed upstream and
    this now uses a literal duplicate — which a hand-edited tasks.md can still
    contain, and which still writes to the wrong line.
    """
    root, bus, changes = fleet
    (changes / "demo" / "tasks.md").write_text(
        "- [ ] 1.1 @otaman-cli first\n- [ ] 1.1 @otaman-cli duplicated\n", encoding="utf-8"
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


# ---------------------------------------------------------------------------
# 2.2 — the awaiting-tick count, and what it says when it cannot count


def test_the_count_is_the_number_of_owed_ticks(fleet):
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    count, note = spec_sweep.awaiting_tick(root, changes, {})
    assert (count, note) == (1, "")


def test_a_clean_fleet_counts_zero_with_no_complaint(fleet):
    root, _, changes = fleet
    assert spec_sweep.awaiting_tick(root, changes, {}) == (0, "")


def test_an_absent_reader_is_not_checked_never_zero(fleet, monkeypatch):
    """The distinction the whole change exists for: 0 must mean "looked and
    found none", never "did not look"."""
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    monkeypatch.setattr(spec_sweep, "_core", lambda: None)
    count, note = spec_sweep.awaiting_tick(root, changes, {})
    assert count is None and "reader" in note


def test_exceeding_the_budget_is_not_checked_never_a_partial_number(fleet):
    """A partial count reads as authoritative while being short — worse than
    admitting the count did not finish."""
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    count, note = spec_sweep.awaiting_tick(root, changes, {}, budget=-1.0)
    assert count is None
    assert "budget" in note and "spec sweep" in note, "name the command that has no budget"


def test_a_reader_that_raises_is_not_checked(fleet, monkeypatch):
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")

    def boom(*a, **k):
        raise RuntimeError("bus unreadable")

    monkeypatch.setattr(spec_sweep, "plan", boom)
    count, note = spec_sweep.awaiting_tick(root, changes, {})
    assert count is None and "RuntimeError" in note


# ---------------------------------------------------------------------------
# the two surfaces, rendering the SAME reader


def test_check_renders_the_count_when_work_is_owed(fleet, monkeypatch, capsys):
    from otaman_cli.commands import check as CH

    root, _, changes = fleet
    monkeypatch.setattr(spec_sweep, "awaiting_tick", lambda *a, **k: (7, ""))
    monkeypatch.setattr("otaman_cli.commands.spec._specs_changes_dir", lambda r: changes)
    CH._render_awaiting_tick(root)
    out = capsys.readouterr().out
    assert "7 filed task(s)" in out and "spec sweep" in out


def test_check_stays_quiet_when_nothing_is_owed(fleet, monkeypatch, capsys):
    """A healthy fleet must not grow a line that everyone learns to skip."""
    from otaman_cli.commands import check as CH

    root, _, changes = fleet
    monkeypatch.setattr(spec_sweep, "awaiting_tick", lambda *a, **k: (0, ""))
    monkeypatch.setattr("otaman_cli.commands.spec._specs_changes_dir", lambda r: changes)
    CH._render_awaiting_tick(root)
    assert capsys.readouterr().out.strip() == ""


def test_check_says_not_checked_rather_than_nothing(fleet, monkeypatch, capsys):
    """Silence and "nothing owed" must not look the same — that equivalence is
    what kept the pmeets backlog invisible for two weeks."""
    from otaman_cli.commands import check as CH

    root, _, changes = fleet
    monkeypatch.setattr(spec_sweep, "awaiting_tick", lambda *a, **k: (None, "reader exploded"))
    monkeypatch.setattr("otaman_cli.commands.spec._specs_changes_dir", lambda r: changes)
    CH._render_awaiting_tick(root)
    out = capsys.readouterr().out
    assert "NOT CHECKED" in out and "reader exploded" in out


def test_check_is_silent_where_there_is_no_specs_repo(fleet, monkeypatch, capsys):
    from otaman_cli.commands import check as CH

    root, _, _ = fleet
    monkeypatch.setattr("otaman_cli.commands.spec._specs_changes_dir", lambda r: None)
    CH._render_awaiting_tick(root)
    assert capsys.readouterr().out.strip() == ""


def test_check_never_dies_on_the_count(fleet, monkeypatch, capsys):
    """`otaman check` is the fleet's most-run command; a drift counter must
    never be the reason it fails."""
    from otaman_cli.commands import check as CH

    root, _, changes = fleet

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("otaman_cli.commands.spec._specs_changes_dir", boom)
    CH._render_awaiting_tick(root)  # must not raise


def test_both_surfaces_use_one_reader():
    """The spec says "from the same reader" — two counts that can disagree are
    worse than one that is sometimes not-checked."""
    check_src = Path("src/otaman_cli/commands/check.py").read_text(encoding="utf-8")
    spec_src = Path("src/otaman_cli/commands/spec.py").read_text(encoding="utf-8")
    assert "spec_sweep.awaiting_tick" in check_src
    assert "spec_sweep.awaiting_tick" in spec_src
    for src in (check_src, spec_src):
        assert "filed_complete_at" not in src, "neither surface may re-derive the count"


# ---------------------------------------------------------------------------
# core #89's batch reader — one bus pass instead of one per change


def test_the_batch_reader_is_probed_not_version_pinned():
    """The same core version exists with and without it; a version compare is
    false assurance."""
    src = Path(spec_sweep.__file__).read_text(encoding="utf-8")
    assert 'getattr(core, "filed_complete_by_change", None)' in src
    assert "__version__" not in src


def test_the_count_uses_one_bus_pass_not_one_per_change(fleet, monkeypatch):
    """The defect the batch reader removes: N changes cost N whole-bus scans,
    measured at 23s for 61 changes over 6657 messages."""
    root, bus, changes = fleet
    for name in ("demo", "second", "third"):
        d = changes / name
        d.mkdir(exist_ok=True)
        (d / "tasks.md").write_text("- [ ] 1.1 @otaman-cli x\n", encoding="utf-8")
    _filing(bus, "demo", "tasks 1.1")

    calls = {"batch": 0, "per_change": 0}
    core = spec_sweep._core()
    real_batch = core.filed_complete_by_change
    real_single = core.filed_complete_at

    def batch(rootp, cfg):
        calls["batch"] += 1
        return real_batch(rootp, cfg)

    def single(rootp, change, cfg):
        calls["per_change"] += 1
        return real_single(rootp, change, cfg)

    monkeypatch.setattr(core, "filed_complete_by_change", batch)
    monkeypatch.setattr(core, "filed_complete_at", single)
    spec_sweep.awaiting_tick(root, changes, {}, budget=600)
    assert calls["batch"] == 1, "the bus must be read once"
    assert calls["per_change"] == 0, "no per-change rescan once the batch reader exists"


def test_an_older_core_still_counts_by_the_slower_path(fleet, monkeypatch):
    """Degrading to slower is acceptable; degrading to wrong or to silence is
    not."""
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    monkeypatch.setattr(spec_sweep, "_batch_reader", lambda core: None)
    count, note = spec_sweep.awaiting_tick(root, changes, {}, budget=600)
    assert count == 1 and note == ""


def test_the_filings_are_read_once_for_every_change(fleet):
    """Reading each message once matters as much as globbing once — it is the
    same files either way."""
    root, bus, changes = fleet
    _filing(bus, "demo", "live-test", stem="20260930T130000-a-to-b-task-complete")
    filings = spec_sweep._all_filings(root, {})
    assert filings and isinstance(filings[0], tuple) and len(filings[0]) == 2


def test_the_budget_note_says_which_half_is_expensive(fleet):
    """ "exceeded its budget" without a cause sends the reader to the wrong half
    — the bus side is now about a second; the git retraction scan is the 50."""
    root, bus, changes = fleet
    _filing(bus, "demo", "tasks 1.1")
    _, note = spec_sweep.awaiting_tick(root, changes, {}, budget=-1.0)
    assert "retraction" in note and "git" in note


def test_the_count_is_never_computed_without_retraction():
    """115-that-might-be-113 rendered confidently is the failure this change
    exists to remove. NOT CHECKED is the honest answer, not an approximation."""
    doc = " ".join((spec_sweep.awaiting_tick.__doc__ or "").split())
    assert "NOT computed without retraction" in doc
