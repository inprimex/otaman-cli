"""rcg 1.3 — `otaman spec status` renders cut-eligibility (the pre-cut view).

Core owns the verdict (`otaman_core.release_gate.cut_eligibility`, rcg 1.1); this is the
surface over it. What is asserted here is everything the surface decides and core does
not: where the verification gate starts (the HEADING, not the task numbering), that an
undeterminable gate reads `not-checked` rather than `gate-unpassed`, that a verdict
resting on an `--all` filing says so, that the fleet-wide render is opt-in while it costs
a bus read per change — and that it stops being opt-in by PROBE the day core accepts
pre-read filings, with no edit here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from otaman_cli import cut_eligibility as ce
from otaman_cli.commands import spec as spec_cmd

PLATFORM = "project: demo\nspecs:\n  path: specs\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "prog"
    (r / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (r / "specs" / "openspec" / "changes").mkdir(parents=True)
    (r / "platform.yaml").write_text(PLATFORM, encoding="utf-8")
    monkeypatch.setattr(spec_cmd, "find_project_root", lambda: r)
    monkeypatch.setattr(
        "otaman_cli.bus_paths._resolve_bus_paths",
        lambda root: (
            root / ".agents" / "bus" / "active",
            root / ".agents" / "bus" / "active" / "acks",
        ),
    )
    return r


def _change(root, name, *, impl, gate=None, gate_heading="## 2. Verification gate", stage=None):
    """A change whose tasks.md carries *impl* ids and (optionally) *gate* ids."""
    folder = root / "specs" / "openspec" / "changes" / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# tasks", "", "## 1. Implementation", ""]
    lines += [f"- [ ] {tid} @otaman-cli do the thing" for tid in impl]
    if gate:
        lines += ["", gate_heading, ""]
        lines += [f"- [ ] {tid} @otaman-specs E2E" for tid in gate]
    (folder / "tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if stage:
        (folder / ".openspec.yaml").write_text(f"stage: {stage}\n", encoding="utf-8")
    return folder


def _filing(root, change, completed, *, ts="20260101T120000", frm="cli-agent"):
    """A `task-complete` filing on the fixture bus. `completed` is the spec text."""
    path = root / ".agents" / "bus" / "active" / f"{ts}-{frm}-to-spec-agent-task-complete.md"
    path.write_text(
        f"---\nfrom: {frm}\nto: spec-agent\ntype: task-complete\n"
        f"timestamp: 2026-01-01T12:00:00Z\nchange: {change}\nstatus: pending\n---\n\n"
        f"**Completed**: {completed}\n",
        encoding="utf-8",
    )
    return path


def _changes_dir(root):
    return root / "specs" / "openspec" / "changes"


# --- the split: headings, not numbering -------------------------------------------


def test_split_reads_the_gate_from_the_heading_not_the_task_numbering(root):
    """A gate section numbered `## 7.` is still the gate; a `2.x` task is not."""
    _change(
        root,
        "c",
        impl=["1.1", "2.3"],
        gate=["7.1"],
        gate_heading="## 7. Verification gate",
    )
    impl, gate, error = ce.split_tasks(_changes_dir(root) / "c" / "tasks.md")
    assert impl == ["1.1", "2.3"]
    assert gate == ["7.1"]
    assert error == ""


def test_split_without_a_gate_heading_is_an_error_not_an_empty_gate(root):
    _change(root, "c", impl=["1.1"])
    impl, gate, error = ce.split_tasks(_changes_dir(root) / "c" / "tasks.md")
    assert impl == ["1.1"]
    assert gate == []
    assert "Verification gate" in error


def test_split_distinguishes_unauthored_from_unreadable(root):
    (_changes_dir(root) / "c").mkdir()
    _, _, error = ce.split_tasks(_changes_dir(root) / "c" / "tasks.md")
    assert "not authored" in error
    assert "FileNotFoundError" not in error


# --- the three verdicts, and the fourth that is mine ------------------------------


def test_a_blanket_filing_cannot_pass_a_mandatory_gate_where_core_allows_the_ask(root):
    """team-mode's MANDATORY 3.1 test-tenant gate read as PASSED on one `--all`.

    The gate arm is this surface's to establish, so it asks core's predicate to ignore
    the sentinel when core offers that (`honor_all`, core #109). On a bundle without
    the kwarg the old behaviour stands — which is why this asserts the two admissible
    outcomes rather than one, and why the annotation (asserted above) matters there.
    """
    import inspect

    from otaman_core.task_complete import is_effectively_complete

    _change(root, "c", impl=["1.1"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1")
    _filing(root, "c", "all tasks", ts="20260102T120000", frm="plugin-agent")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    honors = "honor_all" in inspect.signature(is_effectively_complete).parameters
    assert verdict.gate_passed is not honors, (
        "with the kwarg the blanket filing must NOT pass the gate; without it, it does"
    )


def test_eligible_when_every_task_and_the_gate_are_filed(root):
    _change(root, "c", impl=["1.1", "1.2"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1, 1.2, 2.1")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.status == "eligible"
    assert verdict.eligible is True
    # 2/2, not 3/3: the counts are of IMPLEMENTATION tasks; the gate is its own arm.
    assert verdict.label == "eligible (2/2 filed)"


def test_tasks_outstanding_names_the_unfiled_ids(root):
    _change(root, "c", impl=["1.1", "1.2"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.status == "tasks-outstanding"
    assert verdict.outstanding == ("1.2",)
    assert verdict.eligible is False
    assert "outstanding 1.2" in verdict.label


def test_gate_unpassed_when_the_work_is_filed_and_the_gate_is_not(root):
    _change(root, "c", impl=["1.1"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.status == "gate-unpassed"
    assert verdict.gate_passed is False
    assert verdict.eligible is False


def test_an_undeterminable_gate_is_not_checked_never_gate_unpassed(root):
    """The distinction core cannot make: it has three statuses and all three assert."""
    _change(root, "c", impl=["1.1"])  # no gate section at all
    _filing(root, "c", "tasks 1.1")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.status == ce.NOT_CHECKED
    assert verdict.status != "gate-unpassed"
    assert verdict.checked is False
    assert verdict.eligible is False
    assert verdict.label.startswith("not-checked — ")


def test_core_without_release_gate_is_not_checked_never_eligible(root, monkeypatch):
    _change(root, "c", impl=["1.1"], gate=["2.1"])
    _filing(root, "c", "all tasks")
    monkeypatch.setattr(ce, "_core", lambda: None)
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.eligible is False
    assert "release_gate" in verdict.reason


# --- the --all sentinel, annotated and never silently eligible --------------------


def test_a_verdict_resting_on_an_all_filing_says_so(root):
    """Found live: one `--all` filing made a change with an unticked task cut-eligible.

    The STATUS is deliberately not asserted. It was core's ruling to make and the
    ruling landed (#109: `honor_all=False` for a cut), so the same fixture reads
    `eligible` on a bundle that predates it and `tasks-outstanding` on one that
    carries it. What this surface owns either way is the COUNT — a reader has to be
    able to see that three tasks are filed only by somebody's blanket claim, whichever
    status that produces. Pinning the status here would have broken cli main on the
    day the defect this test reported was fixed.
    """
    _change(root, "c", impl=["1.1", "1.2"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1")
    _filing(root, "c", "all tasks", ts="20260102T120000", frm="plugin-agent")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.checked is True
    assert verdict.via_all == 2  # 1.2 and the gate task 2.1
    assert "2 via --all" in verdict.label


def test_per_task_filings_are_not_annotated_even_alongside_an_all_filing(root):
    _change(root, "c", impl=["1.1"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1, 2.1")
    _filing(root, "c", "all tasks", ts="20260102T120000", frm="plugin-agent")
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {})
    assert verdict.via_all == 0
    assert "--all" not in verdict.label


def test_summarize_counts_the_blanket_filings_and_what_was_never_checked(root):
    verdicts = {
        "a": ce.Verdict(change="a", status="eligible", total_tasks=1, complete_tasks=1),
        "b": ce.Verdict(change="b", status="eligible", total_tasks=1, complete_tasks=1, via_all=2),
        "c": ce.Verdict(change="c", reason="gate state unknown"),
    }
    line = ce.summarize(verdicts, skipped=4)
    assert "2 of 7 cut-eligible" in line
    assert "1 not-checked" in line
    assert "4 not reached" in line
    assert "1 resting on an --all filing" in line


# --- the batch seam, probed rather than version-pinned ----------------------------


@dataclass
class _StubVerdict:
    change: str
    status: str = "eligible"
    total_tasks: int = 1
    complete_tasks: int = 1
    outstanding: tuple = ()
    gate_passed: bool = True


class _StubCore:
    """A stand-in for core's release_gate, with or without the `filed` seam."""

    def __init__(self, *, accepts_filed):
        self.seen = []
        if accepts_filed:

            def cut_eligibility(root, change, ids, config, tasks_path, *, gate_passed, filed=None):
                self.seen.append(filed)
                return _StubVerdict(change=change)

        else:

            def cut_eligibility(root, change, ids, config, tasks_path, *, gate_passed):
                self.seen.append("not-offered")
                return _StubVerdict(change=change)

        self.cut_eligibility = cut_eligibility


def test_batch_seam_is_probed_on_cores_signature(monkeypatch):
    monkeypatch.setattr(ce, "_core", lambda: _StubCore(accepts_filed=True))
    assert ce.batch_seam_present() is True
    monkeypatch.setattr(ce, "_core", lambda: _StubCore(accepts_filed=False))
    assert ce.batch_seam_present() is False
    monkeypatch.setattr(ce, "_core", lambda: None)
    assert ce.batch_seam_present() is False


def test_pre_read_filings_reach_core_only_when_core_accepts_them(root, monkeypatch):
    """The probe is what keeps this working on both sides of core's next release."""
    _change(root, "c", impl=["1.1"], gate=["2.1"])
    _filing(root, "c", "tasks 1.1, 2.1")

    taking = _StubCore(accepts_filed=True)
    monkeypatch.setattr(ce, "_core", lambda: taking)
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {}, filed={"1.1": None, "2.1": None})
    assert verdict.status == "eligible"
    assert taking.seen == [{"1.1": None, "2.1": None}]

    # The old signature must still be CALLED, not refused — and not with `filed=`,
    # which would raise TypeError and render the change not-checked.
    old = _StubCore(accepts_filed=False)
    monkeypatch.setattr(ce, "_core", lambda: old)
    verdict = ce.verdict_for(root, "c", _changes_dir(root), {}, filed={"1.1": None, "2.1": None})
    assert verdict.status == "eligible"
    assert old.seen == ["not-offered"]


# --- the fleet-wide render: a budget that counts what it skipped ------------------


def test_the_budget_counts_what_it_never_looked_at(root):
    for name in ("a", "b", "c"):
        _change(root, name, impl=["1.1"], gate=["2.1"])
    verdicts, skipped = ce.verdicts_for(root, ["a", "b", "c"], _changes_dir(root), {}, budget=-1.0)
    assert verdicts == {}
    assert skipped == 3
    assert "3 not reached" in ce.summarize(verdicts, skipped)


def test_a_generous_budget_reaches_every_change(root):
    for name in ("a", "b"):
        _change(root, name, impl=["1.1"], gate=["2.1"])
    _filing(root, "a", "tasks 1.1, 2.1")
    verdicts, skipped = ce.verdicts_for(root, ["a", "b"], _changes_dir(root), {})
    assert skipped == 0
    assert verdicts["a"].status == "eligible"
    assert verdicts["b"].status == "tasks-outstanding"


# --- the surface ------------------------------------------------------------------


def test_status_default_names_the_flag_instead_of_computing_an_arbitrary_subset(
    root, capsys, monkeypatch
):
    """Opt-in while core reads the bus once per change: 377ms x 68 rows, measured."""
    monkeypatch.setattr(ce, "batch_seam_present", lambda: False)
    _change(root, "c", impl=["1.1"], gate=["2.1"], stage="spec-approved")
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "not computed" in out
    assert "--cut-eligibility" in out
    assert "cut:" not in out


def test_status_renders_the_verdict_per_change_with_the_flag(root, capsys):
    _change(root, "c", impl=["1.1"], gate=["2.1"], stage="spec-approved")
    _filing(root, "c", "tasks 1.1, 2.1")
    spec_cmd.cmd_spec(["status", "--cut-eligibility"])
    out = capsys.readouterr().out
    assert "cut: eligible (1/1 filed)" in out
    assert "1 of 1 cut-eligible" in out


def test_status_computes_without_the_flag_once_core_takes_pre_read_filings(
    root, capsys, monkeypatch
):
    monkeypatch.setattr(ce, "batch_seam_present", lambda: True)
    _change(root, "c", impl=["1.1"], gate=["2.1"], stage="spec-approved")
    _filing(root, "c", "tasks 1.1")
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "not computed" not in out
    assert "cut: gate-unpassed (1/1 filed)" in out


def test_status_json_states_whether_the_cut_view_ran(root, capsys, monkeypatch):
    # The seam decides whether the DEFAULT path computes, so it is forced here rather
    # than left to whichever core is installed — core #109 adds the kwarg the probe
    # looks for, which flips this test's premise without touching its subject.
    monkeypatch.setattr(ce, "batch_seam_present", lambda: False)
    _change(root, "c", impl=["1.1"], gate=["2.1"], stage="spec-approved")
    _filing(root, "c", "tasks 1.1, 2.1")

    spec_cmd.cmd_spec(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["cut_eligibility"]["computed"] is False
    assert "--cut-eligibility" in payload["cut_eligibility"]["note"]
    row = next(c for c in payload["changes"] if c["change"] == "c")
    # Never `False`: "not computed" and "not eligible" are different facts.
    assert row["cut_status"] is None
    assert row["cut_eligible"] is None

    spec_cmd.cmd_spec(["status", "--cut-eligibility", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["cut_eligibility"]["computed"] is True
    assert payload["cut_eligibility"]["skipped"] == 0
    row = next(c for c in payload["changes"] if c["change"] == "c")
    assert row["cut_status"] == "eligible"
    assert row["cut_eligible"] is True
    assert row["cut_via_all"] == 0


def test_status_json_carries_the_blanket_filing_count(root, capsys):
    """The count travels to deploy whatever core's ruling says the status is."""
    _change(root, "c", impl=["1.1", "1.2"], gate=["2.1"], stage="spec-approved")
    _filing(root, "c", "all tasks", frm="plugin-agent")
    spec_cmd.cmd_spec(["status", "--cut-eligibility", "--json"])
    payload = json.loads(capsys.readouterr().out)
    row = next(c for c in payload["changes"] if c["change"] == "c")
    assert row["cut_status"] in ("eligible", "tasks-outstanding")
    assert row["cut_via_all"] == 3
    assert "resting on an --all filing" in payload["cut_eligibility"]["summary"]
