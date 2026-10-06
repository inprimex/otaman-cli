"""`console-complete-human-actions` 1.1 + 1.2 — the human's acts are pressable.

1.1 ratify: typed-reason prompt, the SAME function as the CLI verb (no second
    implementation), identical record, marker names the key.
1.2 answer:  a decision-required answerable from the grouped message view, the reply
    minted to the asking agent and LINKED to the question.

What was actually wrong, measured on 2026-10-06 before building:

* `cmd_ratify` and `console.lifecycle.ratify_change` each sequenced the same three
  core calls independently — `ratify` → `apply_ratification` → `ratified_at`. They
  AGREED, which is the whole problem: two producers of one rule agree right up to the
  release where they do not, and `ratified_at` is the marker doctor and `spec status`
  read for the month count.
* `y` (ratify) was bound only on `LifecycleScreen`, so the marker on a TREE row named
  the CLI verb `otaman ratify` for want of a local key — a marker naming something
  unpressable where it is displayed.
* `action_answer` existed on the read view and was **bound to no key on any screen**.
  An implementation nothing could invoke, which is why no marker could name a key.
* the answer reply carried no `in-reply-to`, so `response_contract.has_outbound_reply`
  — which matches on exactly that field — could never see the decision as answered.
  A reader with no writer, the inverse of the #286 gap.
"""

from __future__ import annotations

import inspect

import pytest

import otaman_cli.console.app as APP
from otaman_cli.spec_ratify import ratification_fields

# ---------------------------------------------------------------------------
# 1.1 — one ratification write, clause 3: console record == CLI record


def _openspec(stage: str = "complete") -> dict:
    return {"stage": stage, "schema": "spec-driven", "created": "2026-10-01"}


def test_the_console_and_the_cli_mint_the_SAME_record(monkeypatch):
    """Clause 3. Both paths now call `ratification_fields`, so this compares the one
    function against itself — which is the point: there is nothing left to diverge.

    Asserted on the KEY SHAPE rather than on values, because `by`/`at` legitimately
    differ between a CLI run and a console run.
    """
    at = "2026-10-06T15:00:00Z"
    cli = ratification_fields(_openspec(), "c", by="roman", reason="why", at=at)
    console = ratification_fields(_openspec(), "c", by="roman", reason="why", at=at)

    assert set(cli) == set(console), "the two paths write different FIELDS"
    assert cli == console
    assert cli["ratified_at"] == at, "the month-count marker doctor/status read"


def test_ratified_at_is_stamped_by_the_shared_write_not_the_callers():
    """It is not part of core's `apply_ratification` (a monotonic stage floor), and it
    was the field most likely to be added on one path and forgotten on the other."""
    src = inspect.getsource(ratification_fields)

    assert 'updated["ratified_at"] = at' in src


@pytest.mark.parametrize(
    "mod,fn",
    [
        ("otaman_cli.commands.spec", "cmd_ratify"),
        ("otaman_cli.console.lifecycle", "ratify_change"),
    ],
)
def test_neither_path_re_sequences_the_ratification(mod, fn):
    """The 'no second implementation' clause, checked at the call site: each path must
    CALL the shared write, and neither may run `apply_ratification` itself."""
    import importlib

    src = inspect.getsource(getattr(importlib.import_module(mod), fn))

    assert "ratification_fields(" in src, f"{fn} does not consume the shared write"
    assert "apply_ratification(" not in src, f"{fn} still sequences the mutation itself"


# ---------------------------------------------------------------------------
# 1.1 — the marker names a key that is pressable WHERE IT IS SHOWN


def test_the_tree_binds_the_ratify_key_it_names():
    """The marker on a tree row says `y`; `y` has to work on that screen. It was bound
    only on LifecycleScreen, which is why the marker used to name a CLI verb."""
    keys = {b.key for b in APP.TreeScreen.BINDINGS if hasattr(b, "key")}

    assert "y" in keys, "the tree names `y` in its marker but does not bind it"
    assert "v" in keys, "and still binds the authored-advance key"


def test_the_tree_ratify_action_reuses_the_row_classification():
    """One place maps a row to its act (#284's `awaiting_action`). The action reads it
    rather than re-deciding, so the marker and the action cannot disagree."""
    src = inspect.getsource(APP.TreeScreen.action_ratify)

    assert "awaiting_action" in src
    assert "ratify_change(" in src, "it must use the shared console write"


def test_the_tree_ratify_refusal_names_the_act_that_applies():
    """The dead-end rule — the same correction #284 made for `v`."""
    src = inspect.getsource(APP.TreeScreen.action_ratify)

    assert "the action here is" in src


def test_an_empty_reason_is_refused():
    """ "typed-reason prompt (mandatory, non-empty)" — an empty ratify reason is an
    unexplained bypass of the normal approval path."""
    src = inspect.getsource(APP.TreeScreen.action_ratify)

    assert "Ratify needs a reason." in src


# ---------------------------------------------------------------------------
# 1.2 — the answer is reachable, and linked


def test_the_reply_carries_the_in_reply_to_stem():
    from otaman_cli.console.decision_required import answer_argv

    argv = answer_argv("spec-agent", "Re: x", "the answer", in_reply_to="STEM-123")

    assert "--in-reply-to" in argv
    assert argv[argv.index("--in-reply-to") + 1] == "STEM-123"


def test_the_link_is_what_the_existing_reader_matches_on():
    """`response_contract.has_outbound_reply` keys on `in-reply-to`. Without the flag
    the reply reached the agent but the reader could never see it — so this asserts the
    producer and that consumer name the same field."""
    from otaman_cli import response_contract

    src = inspect.getsource(response_contract.has_outbound_reply)

    assert "in-reply-to" in src or "in_reply_to" in src


def test_omitting_the_stem_omits_the_flag_rather_than_sending_empty():
    """An empty `--in-reply-to ""` would write a frontmatter key with no value, which
    the reader would then match against nothing."""
    from otaman_cli.console.decision_required import answer_argv

    assert "--in-reply-to" not in answer_argv("a", "s", "b")
    assert "--in-reply-to" not in answer_argv("a", "s", "b", in_reply_to="   ")


def test_send_accepts_the_flag_and_core_tolerates_the_key():
    """Measured rather than assumed: core's validator returns no errors for a message
    carrying `in-reply-to`, so the flag cannot mint something the bus refuses."""
    from otaman_core.validate_message import validate_message_content

    msg = (
        "---\nid: 20261006T150000-cli-agent-to-spec-agent-x\nfrom: cli-agent\n"
        "to: spec-agent\npriority: normal\ntype: info\n"
        "timestamp: 2026-10-06T15:00:00+00:00\nstatus: pending\n"
        'in-reply-to: "20261006T120000-spec-agent-to-human-decision-required"\n'
        "---\n\n## Subject: Re: x\n\nbody\n"
    )

    errors, _warnings = validate_message_content(msg)

    assert errors == []


def test_the_answer_is_bound_in_BOTH_views_to_the_same_key():
    """ "in the grouped message view" — and the read view's copy was unbound entirely.
    One key, so a marker can name it without qualification."""
    grouped = {b.key for b in APP.InboxScreen.BINDINGS if hasattr(b, "key")}
    read = {b.key for b in APP.InboxMessageScreen.BINDINGS if hasattr(b, "key")}

    assert "w" in grouped, "the grouped view cannot answer"
    assert "w" in read, "the read view's action is still unreachable"


def test_there_is_exactly_ONE_answer_implementation():
    src = inspect.getsource(APP)

    assert src.count("def action_answer(") == 1, "a second answer implementation exists"
    assert src.count("def _send_answer_for(") == 1


def test_both_views_supply_the_target_through_the_shared_mixin():
    assert issubclass(APP.InboxScreen, APP._AnswerAction)
    assert issubclass(APP.InboxMessageScreen, APP._AnswerAction)
    assert APP.InboxScreen._answer_target is not APP._AnswerAction._answer_target
    assert APP.InboxMessageScreen._answer_target is not APP._AnswerAction._answer_target


def test_a_non_decision_message_is_refused_by_name():
    """Answering anything else sends an agent a message it is not waiting for."""
    src = inspect.getsource(APP._AnswerAction.action_answer)

    assert "Only a decision-required can be answered" in src


def test_an_empty_answer_sends_nothing():
    src = inspect.getsource(APP._AnswerAction._send_answer_for)

    assert "an empty answer unblocks nothing" in src


def test_the_answer_is_journalled():
    """An answer that vanished would leave the agent blocked with nobody knowing why."""
    src = inspect.getsource(APP._AnswerAction._send_answer_for)

    assert 'action="answer"' in src


# ---------------------------------------------------------------------------
# both markers name pressable keys (2.1's gate, asserted at the unit level)


def test_every_awaiting_action_the_tree_emits_names_a_key_or_a_route():
    """2.1's clause: "every awaiting-you marker on screen names a pressable key".
    `v` and `y` are tree keys; the decision answer lives on the Messages screen, so it
    names the route to the key rather than pretending it is local."""
    src = inspect.getsource(APP.TreeScreen._awaiting_ids)
    # Only the ASSIGNMENT lines — the docstring legitimately names `otaman ratify` as
    # what the lifecycle source is derived from, and a whole-source grep called that a
    # violation. Same false positive the #281 guard hit before it moved to `ast`.
    assigns = "\n".join(
        ln for ln in src.splitlines() if "awaiting[" in ln or "awaiting.update" in ln or ': "' in ln
    )

    assert '"y"' in assigns, "the ratify marker must name the key"
    assert '"v"' in assigns, "the authored marker must name the key"
    assert '"m then w"' in assigns, "the decision marker must name the route to `w`"
    assert "otaman ratify" not in assigns, "a marker still names a CLI verb, not a key"
