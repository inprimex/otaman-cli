"""A decision is found by the link the decision itself writes.

Two surfaces reported the same symptom on 2026-10-06 and each diagnosed it differently:

* deploy-agent (20261006T144602): `otaman check` listed three of their proposals as
  "waiting for human approval" for two days after Roman approved them.
* spec-agent (20261006T185143): Roman opened the SCR view — 19 items marked mandatory,
  18 already decided. "A mandatory-review tree that is 95 percent ghosts trains the
  operator to stop reading it."

The cause, measured before building:

    approval broadcasts carrying `**Original proposal**: <stem>` in the BODY   104
    approval broadcasts with a stem-shaped string in the SUBJECT                 0 of 12

`check.py` matched `stem in m["subject"]`. The writer puts the stem in the body and the
TITLE in the subject, so the match could never succeed — a writer and a reader naming
different fields, the third instance of that shape in one day.

The console had the second half: its queue filtered on `<stem>.human.ack` alone, and a
decision recorded as a BROADCAST left no such ack. 106 SCRs on the live bus, 58
human-acked, 48 not — and the back-link accounts for most of the difference.

So the format gets ONE home (`approval_link`) that both readers consult, rather than a
second grep in each.
"""

from __future__ import annotations

import pytest

from otaman_cli.approval_link import parse_original_proposal, render_original_proposal

STEM = "20261005T120000-deploy-agent-to-human-spec-change-request"


# ---------------------------------------------------------------------------
# the format has one home


def test_what_the_writer_renders_is_what_the_reader_parses():
    """The round trip IS the fix: these drifted apart because they lived apart."""
    assert parse_original_proposal(f"blah\n\n{render_original_proposal(STEM)}\n\nmore") == STEM


def test_the_legacy_backticked_form_still_parses():
    """Four months of this bus carry `**Original proposal**: `<stem>``. A parser that
    only accepted today's spelling would silently fail to link all of it — and "no
    approval found" is indistinguishable from "not yet approved"."""
    assert parse_original_proposal(f"**Original proposal**: `{STEM}`") == STEM


def test_a_body_with_no_link_returns_None_rather_than_a_guess():
    """None is a real answer. A broadcast minted by hand links to nothing, and
    inventing a guess is how the WRONG proposal gets marked approved."""
    assert parse_original_proposal("## Subject: approved\n\nno link here") is None
    assert parse_original_proposal("") is None
    assert parse_original_proposal(None) is None
    # The case that matters, and the one the first version of this test missed: a body
    # carrying a DIFFERENT bold field. Every rejection body has `**Reason**:`, so a
    # regex loosened to "any bold field" resolves the verdict's prose as a stem and
    # marks the wrong proposal decided. Found by sabotage — the earlier assertions all
    # used bodies with no bold field at all, so they could not see it.
    assert parse_original_proposal("**Reason**: because\n\nno link") is None
    assert parse_original_proposal("**Decided by**: roman\n**Note**: x") is None


def test_the_subject_is_not_where_the_stem_lives():
    """Pins the asymmetry that caused the bug, so a future reader cannot 'simplify'
    the parse back onto the subject."""
    subject_only = "## Subject: spec-change-approved: targeted-bus-messaging"

    assert parse_original_proposal(subject_only) is None


def test_both_writers_render_through_the_single_home():
    import inspect

    from otaman_cli.commands import approve

    src = inspect.getsource(approve)

    assert src.count("_original_proposal(target)") == 2, "approval and rejection"
    assert "**Original proposal**: {target" not in src, "a writer still formats it inline"


# ---------------------------------------------------------------------------
# `otaman check` finds the approval


def test_check_never_matches_a_stem_against_a_SUBJECT():
    """The original defect: the stem lives in the body, the subject carries the title,
    and 0 of 12 live approvals had a stem-shaped string in their subject."""
    import inspect

    from otaman_cli.commands import check

    src = inspect.getsource(check.cmd_check)

    assert 'stem in m.get("subject", "")' not in src, "the broken match is back"


def test_check_reads_the_verdict_KIND_from_the_shared_scanner():
    """Moved here from a per-message field: the verdict now comes from
    `approval_link.decided_stems`, which also scans the ARCHIVE. A rejected proposal
    reported as awaiting approval, or an archived decision not seen at all, are the
    same lie in two different shapes.
    """
    import inspect

    from otaman_cli.commands import check

    src = inspect.getsource(check.cmd_check)

    assert "decided_stems(" in src
    assert 'verdict in ("approved", "dispositioned")' in src
    assert 'verdict == "rejected"' in src, "a rejection must still render as REJECTED"
    assert "archive" in src, "the archive must be passed, or archived decisions vanish"


# ---------------------------------------------------------------------------
# the console queue drops the ghosts


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    return Program("demo", root), active


def _scr(active, stem: str) -> None:
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: deploy-agent\nto: human\npriority: normal\n"
        f"type: spec-change-request\ntimestamp: 2026-10-05T12:00:00+00:00\n"
        f"status: pending\n---\n\n## Subject: please approve\n\nbody\n",
        encoding="utf-8",
    )


def _broadcast(active, kind: str, decides: str) -> None:
    stem = f"20261005T163250-human-to-all-{kind}-x"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\ntype: {kind}\n"
        f"timestamp: 2026-10-05T16:32:50+00:00\nstatus: pending\n---\n\n"
        f"## Subject: {kind}: some-change-title\n\n"
        f"{render_original_proposal(decides)}\n",
        encoding="utf-8",
    )


def test_a_proposal_decided_by_a_broadcast_leaves_the_queue(program):
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    assert len(list_pending_proposals(prog)) == 1, "fixture must start with it pending"

    _broadcast(active, "spec-change-approved", STEM)

    assert (
        list_pending_proposals(prog) == []
        or [p for p in list_pending_proposals(prog) if p.stem == STEM] == []
    )


def test_a_rejection_also_resolves_it(program):
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    _broadcast(active, "spec-change-rejected", STEM)

    assert [p for p in list_pending_proposals(prog) if p.stem == STEM] == []


def test_an_UNDECIDED_proposal_stays(program):
    """The guard against over-filtering: a real pending request must survive, or the
    queue becomes useless in the other direction."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    _broadcast(active, "spec-change-approved", "20260101T000000-someone-else-scr")

    assert [p.stem for p in list_pending_proposals(prog)] == [STEM]


def test_a_broadcast_with_no_back_link_resolves_nothing(program):
    """A hand-minted broadcast links to nothing, so it must not silently clear a queue
    item it cannot name."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    stem = "20261005T163250-human-to-all-spec-change-approved-x"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\n"
        f"type: spec-change-approved\ntimestamp: 2026-10-05T16:32:50+00:00\n"
        f"status: pending\n---\n\n## Subject: approved something\n\nno link\n",
        encoding="utf-8",
    )

    assert [p.stem for p in list_pending_proposals(prog)] == [STEM]


def test_the_human_ack_path_still_resolves_on_its_own(program):
    """The ack check must keep working without a broadcast — it is the older contract
    and `otaman approve` still writes it."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    (active / "acks" / f"{STEM}.human.ack").write_text("approved\n", encoding="utf-8")

    assert [p for p in list_pending_proposals(prog) if p.stem == STEM] == []


def test_both_surfaces_consult_the_same_primitive():
    """One definition of "decided", or the two views disagree again."""
    import inspect

    from otaman_cli.commands import check
    from otaman_cli.console import bus

    assert "approval_link" in inspect.getsource(bus._decided_stems)
    assert "_approval_link" in inspect.getsource(check)


# ---------------------------------------------------------------------------
# dispositions (seam ruled 20261007T193507): decided, but not by approval


def _disposition(active, decides: str, *, sender: str = "spec-agent", verdict: str = "duplicate"):
    stem = f"20261007T200000-{sender}-to-human-disposition-x"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: {sender}\nto: human\npriority: normal\ntype: info\n"
        f"timestamp: 2026-10-07T20:00:00+00:00\nstatus: pending\n---\n\n"
        f"## Subject: Dispositioned\n\n"
        f"{render_original_proposal(decides)}\n"
        f"**Disposition**: {verdict}\n",
        encoding="utf-8",
    )
    return stem


def test_a_disposition_from_spec_agent_decides_the_request(program):
    """An SCR already delivered, duplicated or absorbed needs no human review."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    _disposition(active, STEM)

    assert [p for p in list_pending_proposals(prog) if p.stem == STEM] == []


def test_a_disposition_from_ANY_OTHER_agent_is_ignored(program):
    """The trust boundary. Without the sender check, any agent could mint a `type:
    info` carrying a back-link and silently clear items off the human's mandatory
    review queue — and a vanished review is worse than a visible ghost, because
    nothing shows it happened."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    _disposition(active, STEM, sender="plugin-agent")

    assert [p.stem for p in list_pending_proposals(prog)] == [STEM]


def test_a_back_link_with_no_disposition_line_is_not_a_disposition(program):
    """`type: info` from spec-agent quoting a stem in prose is a conversation, not a
    verdict. Both halves are required."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    stem = "20261007T200000-spec-agent-to-human-chat-x"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: spec-agent\nto: human\npriority: normal\ntype: info\n"
        f"timestamp: 2026-10-07T20:00:00+00:00\nstatus: pending\n---\n\n"
        f"## Subject: about that request\n\n{render_original_proposal(STEM)}\n",
        encoding="utf-8",
    )

    assert [p.stem for p in list_pending_proposals(prog)] == [STEM]


@pytest.mark.parametrize("verdict", ["already-delivered", "duplicate", "absorbed-into omo"])
def test_every_ruled_verdict_form_parses(verdict):
    from otaman_cli.approval_link import parse_disposition

    assert parse_disposition(f"**Disposition**: {verdict}") == verdict


def test_a_back_link_without_a_VERDICT_is_not_a_disposition_predicate_level():
    """Asserted on the predicate directly, because the reader's cheap
    `DISPOSITION_LABEL not in text` pre-filter hides this at the reader level — a
    sabotage that made a bare back-link count as a disposition passed every
    reader-level test. The realistic leak: spec-agent writes an info message that
    mentions "Disposition" in prose AND quotes a stem, the pre-filter admits it, and
    the item silently clears off the human's queue.
    """
    from otaman_cli.approval_link import is_disposition

    assert not is_disposition("spec-agent", render_original_proposal(STEM))
    assert not is_disposition(
        "spec-agent",
        f"{render_original_proposal(STEM)}\n\nNo Disposition has been recorded yet.\n",
    ), "prose mentioning the word is not a verdict line"


def test_a_prose_mention_of_disposition_does_not_clear_the_queue(program):
    """The same leak at the reader level, past the pre-filter."""
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    stem = "20261007T200000-spec-agent-to-human-chat-y"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: spec-agent\nto: human\npriority: normal\ntype: info\n"
        f"timestamp: 2026-10-07T20:00:00+00:00\nstatus: pending\n---\n\n"
        f"## Subject: status\n\n{render_original_proposal(STEM)}\n\n"
        f"No Disposition has been recorded for this yet.\n",
        encoding="utf-8",
    )

    assert [p.stem for p in list_pending_proposals(prog)] == [STEM]


def test_the_sender_rule_is_the_one_spec_agent_declared():
    """One producer: the agent that owns dispositions.yaml. Pinned so a later reader
    cannot widen it casually."""
    from otaman_cli.approval_link import DISPOSITION_SENDER, is_disposition

    assert DISPOSITION_SENDER == "spec-agent"
    body = render_original_proposal(STEM) + "\n**Disposition**: duplicate\n"
    assert is_disposition("spec-agent", body)
    assert not is_disposition("human", body)
    assert not is_disposition(None, body)


# ---------------------------------------------------------------------------
# the ARCHIVE blind spot — a bug in #288/#290 found on 2026-10-08


@pytest.fixture
def bus_dirs(tmp_path):
    active = tmp_path / "bus" / "active"
    archive = tmp_path / "bus" / "archive" / "2026-08"
    (active / "acks").mkdir(parents=True)
    archive.mkdir(parents=True)
    return active, archive


def _verdict_file(d, kind: str, decides: str, *, sender="human"):
    stem = f"20260824T140358-{sender}-to-all-{kind}"
    (d / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: {sender}\nto: all\npriority: normal\ntype: {kind}\n"
        f"timestamp: 2026-08-24T14:03:58+00:00\nstatus: pending\n---\n\n"
        f"## Subject: {kind}: a title with no stem in it\n\n"
        f"{render_original_proposal(decides)}\n",
        encoding="utf-8",
    )


def test_an_ARCHIVED_decision_is_still_a_decision(bus_dirs):
    """The bug. A proposal rejected on 2026-08-24 resurfaced on the human's mandatory
    queue because its rejection had been archived and both readers globbed `active/`
    only — recreating the exact ghost #288/#290 removed. 14 decided broadcasts were
    already archived when this was found, and that count only grows.
    """
    from otaman_cli.approval_link import decided_stems

    active, archive = bus_dirs
    _verdict_file(archive.parent / "2026-08", "spec-change-rejected", STEM)

    assert decided_stems(active, archive.parent).get(STEM) == "rejected"


def test_an_active_only_scan_would_MISS_it(bus_dirs):
    """Pins that the archive argument is load-bearing, not decoration: without it the
    same corpus yields nothing."""
    from otaman_cli.approval_link import decided_stems

    active, archive = bus_dirs
    _verdict_file(archive.parent / "2026-08", "spec-change-approved", STEM)

    assert decided_stems(active) == {}
    assert decided_stems(active, archive.parent) == {STEM: "approved"}


def test_the_verdict_KIND_is_returned_not_just_membership(bus_dirs):
    """`otaman check` renders an approval as READY TO RESUME and a rejection as
    REJECTED. A set would force one of the two readers to re-derive the kind."""
    from otaman_cli.approval_link import decided_stems

    active, archive = bus_dirs
    _verdict_file(active, "spec-change-approved", "stem-a")
    _verdict_file(archive, "spec-change-rejected", "stem-b")

    got = decided_stems(active, archive.parent)

    assert got == {"stem-a": "approved", "stem-b": "rejected"}


def test_a_disposition_is_found_in_the_archive_too(bus_dirs):
    from otaman_cli.approval_link import decided_stems

    active, archive = bus_dirs
    stem = "20261007T212139-spec-agent-to-cli-agent-disposition"
    (archive / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: spec-agent\nto: cli-agent\npriority: normal\ntype: info\n"
        f"timestamp: 2026-10-07T21:21:39+00:00\nstatus: pending\n---\n\n"
        f"## Subject: dispositioned\n\n{render_original_proposal(STEM)}\n"
        f"**Disposition**: already-delivered\n",
        encoding="utf-8",
    )

    assert decided_stems(active, archive.parent).get(STEM) == "dispositioned"


def test_the_disposition_scan_is_scoped_BY_SENDER_not_by_reading_every_file():
    """Cost and correctness in one. Dispositions carry no contractual filename token —
    spec-agent's first emission was slugged from its SUBJECT — but the SENDER is
    contractual, and filenames encode it. Reading all 7,364 active files on a console
    render path is what 2.3's guard forbids; the sender glob is 945.
    """
    import inspect

    from otaman_cli import approval_link

    src = inspect.getsource(approval_link)

    assert '_DISPOSITION_GLOB = f"*{DISPOSITION_SENDER}-to-*.md"' in src
    assert 'rglob("*.md")' not in src, "a scan of every file is back"


def test_a_foreign_sender_in_a_matching_FILENAME_is_still_refused(bus_dirs):
    """The filename is a hint; the frontmatter decides. A file named
    `…spec-agent-to-…` whose `from:` is someone else must not clear the queue."""
    from otaman_cli.approval_link import decided_stems

    active, archive = bus_dirs
    stem = "20261008T000000-spec-agent-to-human-disposition-forged"
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: plugin-agent\nto: human\npriority: normal\ntype: info\n"
        f"timestamp: 2026-10-08T00:00:00+00:00\nstatus: pending\n---\n\n"
        f"## Subject: x\n\n{render_original_proposal(STEM)}\n**Disposition**: duplicate\n",
        encoding="utf-8",
    )

    assert decided_stems(active, archive.parent) == {}


def test_both_readers_consume_the_shared_scanner():
    """One home, or the two surfaces disagree about what is decided — which is how the
    archive blind spot came to exist in both at once."""
    import inspect

    from otaman_cli.commands import check
    from otaman_cli.console import bus

    assert "decided_stems" in inspect.getsource(bus._decided_stems)
    assert "decided_stems(" in inspect.getsource(check.cmd_check)
    # and neither re-globs the verdict patterns itself
    assert "spec-change-approved*.md" not in inspect.getsource(bus._decided_stems)


def test_the_CONSOLE_queue_honours_an_archived_decision(program):
    """Behavioural, not a source grep. A sabotage that dropped the archive argument
    from the console's call passed every assertion in this file — because
    `test_both_readers_consume_the_shared_scanner` checks that the scanner is MENTIONED,
    not that it is wired with the archive. Presence is not wiring, and the console is
    the surface where the ghost actually reached Roman.
    """
    from otaman_cli.console.bus import list_pending_proposals

    prog, active = program
    _scr(active, STEM)
    assert len(list_pending_proposals(prog)) == 1, "fixture must start with it pending"

    archive = active.parent / "archive" / "2026-08"
    archive.mkdir(parents=True)
    stem = "20260824T140358-human-to-all-spec-change-rejected"
    (archive / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\n"
        f"type: spec-change-rejected\ntimestamp: 2026-08-24T14:03:58+00:00\n"
        f"status: pending\n---\n\n## Subject: rejected: a title\n\n"
        f"{render_original_proposal(STEM)}\n",
        encoding="utf-8",
    )

    assert [p for p in list_pending_proposals(prog) if p.stem == STEM] == [], (
        "the console ignored an archived rejection — the ghost is back"
    )
