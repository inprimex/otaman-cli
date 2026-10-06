"""delivery-authorization-envelope 1.2 — decision-required reaches the human.

Core's 1.1 gave the type a schema so an agent can EMIT rather than freeze.
Without this half the type is write-only: the agent emits, nothing surfaces it,
and the human still finds out by noticing a pane that has not moved — the
failure relocated rather than fixed.

So the tests are weighted toward the row being impossible to miss (it reaches
the queue whatever it is addressed to, it sorts above read-only traffic, it is
counted) and toward the answer reaching the agent that is actually blocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.console.bus import Proposal
from otaman_cli.console.decision_required import (
    DECISION_REQUIRED,
    answer_argv,
    answer_subject,
    blocked_refs,
    needs_answer,
)


def _row(**kw):
    base = dict(
        stem="20260930T120000-core-agent-to-human-decision-required",
        subject="Which default lens?",
        from_agent="core-agent",
        timestamp="2026-09-30T12:00:00Z",
        priority="normal",
        path=Path("/tmp/x.md"),
        body="",
        msg_type=DECISION_REQUIRED,
        blocks="task 2.1 of console-reactive-store",
    )
    base.update(kw)
    return Proposal(**base)


# ---------------------------------------------------------------------------
# what kind of row it is


def test_a_decision_required_awaits_the_human():
    assert _row().is_awaiting is True


def test_it_is_not_an_approve_reject_defer_decision():
    """`is_decision` drives the approve/reject/defer keys. Folding this in
    would offer `a` as approve on something that cannot be approved."""
    assert _row().is_decision is False
    assert _row().needs_answer is True


def test_an_ordinary_message_needs_no_answer():
    assert _row(msg_type="info").needs_answer is False
    assert _row(msg_type="info").is_awaiting is False


def test_an_scr_still_awaits_and_is_still_a_decision():
    row = _row(msg_type="spec-change-request")
    assert row.is_decision and row.is_awaiting and not row.needs_answer


def test_needs_answer_is_a_function_of_type_alone():
    assert needs_answer(DECISION_REQUIRED) and not needs_answer("question")


# ---------------------------------------------------------------------------
# reaching the queue at all


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    return Program(name="demo", root=root)


def _emit(program, *, to="human", stem="20260930T120000-core-agent-to-x-decision-required"):
    active = program.root / ".agents" / "bus" / "active"
    (active / f"{stem}.md").write_text(
        "---\n"
        "id: x\n"
        "from: core-agent\n"
        f"to: {to}\n"
        "type: decision-required\n"
        "decision: Which default lens?\n"
        "blocks: task 2.1 of console-reactive-store\n"
        "unblock-condition: an answer\n"
        "timestamp: 2026-09-30T12:00:00Z\n"
        "---\n\n## Subject: Which default lens?\n",
        encoding="utf-8",
    )


def test_a_decision_required_reaches_the_queue(program):
    from otaman_cli.console.bus import list_human_queue

    _emit(program)
    rows = list_human_queue(program)
    assert [r.msg_type for r in rows] == [DECISION_REQUIRED]


def test_it_reaches_the_queue_even_when_misaddressed(program):
    """An agent emitting one is blocked on the human BY DEFINITION. Requiring
    it to also be addressed correctly would let a misaddressed emission freeze
    the agent silently — which is the failure the type exists to remove."""
    from otaman_cli.console.bus import list_human_queue

    _emit(program, to="spec-agent")
    assert len(list_human_queue(program)) == 1


def test_it_carries_what_it_blocks(program):
    from otaman_cli.console.bus import list_human_queue

    _emit(program)
    assert "console-reactive-store" in list_human_queue(program)[0].blocks


def test_it_sorts_above_read_only_traffic(program):
    """A question left below the noise is a question nobody sees."""
    from otaman_cli.console.bus import list_human_queue

    active = program.root / ".agents" / "bus" / "active"
    (active / "20260930T110000-a-to-human-info.md").write_text(
        "---\nid: i\nfrom: a\nto: human\ntype: info\ntimestamp: 2026-09-30T11:00:00Z\n"
        "---\n\n## Subject: fyi\n",
        encoding="utf-8",
    )
    _emit(program)
    assert list_human_queue(program)[0].msg_type == DECISION_REQUIRED


# ---------------------------------------------------------------------------
# what it blocks


def test_blocked_refs_reads_the_change_out_of_a_phrase():
    assert blocked_refs("task 2.1 of console-reactive-store") == {"console-reactive-store"}


def test_blocked_refs_handles_a_bare_id_and_a_list():
    assert blocked_refs("JTBD-118") == {"JTBD-118"}
    assert blocked_refs("a, b; c") == {"a", "b", "c"}


def test_an_unparseable_blocks_field_does_not_hide_the_row():
    """Tolerant on purpose: a `blocks:` the console cannot read must not make
    the question invisible."""
    assert blocked_refs("") == set()
    assert blocked_refs(None) == set()


# ---------------------------------------------------------------------------
# answering reaches the blocked agent


def test_the_answer_routes_to_the_emitting_agent():
    """Not the human, not a broadcast. An answer sent anywhere else leaves the
    waiting agent waiting."""
    argv = answer_argv("core-agent", "Re: Which default lens?", "value")
    assert argv[0] == "send" and argv[1] == "core-agent"


def test_the_answer_carries_the_question_in_its_subject():
    assert answer_subject("Which default lens?") == "Re: Which default lens?"


def test_an_already_prefixed_subject_is_not_doubled():
    assert answer_subject("Re: already") == "Re: already"


def test_an_empty_subject_still_names_something():
    assert "decision-required" in answer_subject("")


def test_the_answer_goes_through_the_shared_verb():
    """`otaman send` — the same verb a human would type (D4). One write path
    means the console cannot drift from the CLI."""
    assert answer_argv("a", "s", "b")[0] == "send"


# ---------------------------------------------------------------------------
# the surfaces


def test_the_read_view_offers_answer_only_for_a_decision_required():
    """Offering it elsewhere would send an agent a message it is not waiting
    for."""
    import inspect

    from otaman_cli.console.app import InboxMessageScreen, _AnswerAction

    # The read view inherits the action from the shared mixin (ccha 1.2); assert the
    # inheritance holds AND that the shared guard is the one being inherited.
    assert issubclass(InboxMessageScreen, _AnswerAction)
    src = inspect.getsource(InboxMessageScreen.action_answer)
    assert "needs_answer" in src
    assert "Only a decision-required can be answered" in src


def test_the_answer_is_journalled():
    """An answer that vanished would leave the agent blocked with nobody
    knowing why — the failure this type exists to remove.

    Asserted on the SHARED `_AnswerAction` mixin since ccha 1.2: the send moved there
    so the grouped view and the read view answer through one implementation, and this
    now covers both screens rather than the read view alone.
    """
    import inspect

    from otaman_cli.console.app import _AnswerAction

    assert "run_decision_action" in inspect.getsource(_AnswerAction._send_answer_for)


def test_the_awaiting_filter_reads_a_projection_not_the_bus():
    """`:a` is evaluated on every filter submission, and 2.3's guard forbids a
    bus scan on a render path."""
    import inspect

    from otaman_cli.console.app import TreeScreen

    src = inspect.getsource(TreeScreen._awaiting_ids)
    assert "blocked_by_decision" in src
    assert "list_human_queue" not in src


def test_the_header_counts_questions_separately_from_decisions():
    """ "3 awaiting your decision" that silently included questions would
    under-describe what the human is looking at.

    cmt 1.2 moved the header's counting into `messages_tree.summary_line` (the
    banner now counts per review class), so this asserts the behaviour where it
    lives instead of grepping `_paint` — both kinds still have to be named, and now
    the assertion would survive the next move.
    """
    from dataclasses import dataclass

    from otaman_core.review_policy import default_policy

    from otaman_cli.console.messages_tree import group_messages, summary_line

    @dataclass(frozen=True)
    class Row:
        stem: str
        msg_type: str
        priority: str = "normal"
        timestamp: str = "2026-10-01T00:00:00Z"

        @property
        def is_decision(self) -> bool:
            return self.msg_type == "spec-change-request"

        @property
        def needs_answer(self) -> bool:
            return self.msg_type == DECISION_REQUIRED

    rows = [
        Row("s1", "spec-change-request"),
        Row("s2", "spec-change-request"),
        Row("q1", DECISION_REQUIRED),
    ]
    line = summary_line(group_messages(rows, default_policy()))
    assert "2 awaiting your decision" in line
    assert "1 awaiting your answer" in line
