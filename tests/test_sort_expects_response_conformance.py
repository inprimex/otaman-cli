"""The tiebreak step that never fired, and the inversion it left behind.

`inter-agent-request-response-contract` design.md Q4 specifies, within a
priority band:

    1. expects-response: true before false/absent
    2. response-effort ascending: XS -> S -> M -> L -> XL
    3. timestamp ascending

and its type table assigns `XS` to `info`/`fyi` with the rationale "No response
expected; **sort last**", and to `spec-change-approved`/`task-complete` as "Ack
and proceed".

Step 1 relies on `expects-response:`, which is populated by NOTHING — measured
0 of 7,472 messages in this program's raw bus corpus carry the key. With step 1
inert for every message ever sent, step 2's ASCENDING effort decided the order,
which puts `XS` FIRST. So the announcements the table wanted last sorted to the
top of their band, above the `task-assignment` (M) and `question` (S) items a
reader has to act on — the inverse of the stated rationale.

Deriving "expects a response" from the type conforms the code to that
rationale. It is NOT the separate policy question of whether a `high` broadcast
should outrank a `normal` direct message (raised 20261008T182152, unanswered,
deliberately untouched here).
"""

from __future__ import annotations

from otaman_cli.response_contract import (
    NO_RESPONSE_TYPES,
    expects_a_response,
    make_sort_key,
)


def _m(mtype: str, *, priority: str = "normal", ts: str = "2026-10-01T00:00:00Z", **kw):
    return {"type": mtype, "priority": priority, "timestamp": ts, **kw}


def _order(msgs: list[dict]) -> list[str]:
    return [m["type"] for m in sorted(msgs, key=make_sort_key)]


# ---------------------------------------------------------------------------
# the derivation


def test_actionable_types_expect_a_response():
    for t in ("question", "task-assignment", "spec-change-request", "contract-change"):
        assert expects_a_response(_m(t)) is True, t


def test_announcement_types_do_not():
    for t in sorted(NO_RESPONSE_TYPES):
        assert expects_a_response(_m(t)) is False, t


def test_an_explicit_field_wins_in_both_directions():
    """A sender who says false on a question is believed, and vice versa —
    the type is a fallback for an absent key, not an override."""
    assert expects_a_response(_m("question", expects_response=False)) is False
    assert expects_a_response(_m("info", expects_response=True)) is True


def test_an_unknown_type_is_assumed_to_want_a_reply():
    """Same spirit as resolve_response_effort's M fallback: an unrecognized
    kind must not sort last by accident."""
    assert expects_a_response(_m("some-new-type")) is True


# ---------------------------------------------------------------------------
# the ordering it fixes


def test_an_assignment_outranks_approvals_in_the_same_band():
    """The bug, at the level it bit: a dispatch buried under announcements."""
    msgs = [
        _m("spec-change-approved"),
        _m("spec-change-approved"),
        _m("info"),
        _m("task-assignment"),
    ]
    assert _order(msgs)[0] == "task-assignment"


def test_announcements_sort_last_as_the_table_says():
    msgs = [_m("info"), _m("question"), _m("task-complete"), _m("task-assignment")]
    order = _order(msgs)
    assert order[:2] == ["question", "task-assignment"], order
    assert set(order[2:]) == {"info", "task-complete"}


def test_effort_still_orders_within_the_responders():
    """Step 3 is unchanged — among items that DO expect a reply, the cheap
    ones still come first."""
    msgs = [_m("spec-change-request"), _m("question"), _m("task-assignment")]
    assert _order(msgs) == ["question", "task-assignment", "spec-change-request"]


def test_priority_still_dominates():
    """Step 2 is a TIEBREAK — it must not reorder across priority bands. An
    urgent announcement still beats a normal assignment, which is correct and
    is the half that needs a ruling, not a fix."""
    msgs = [_m("task-assignment", priority="normal"), _m("info", priority="urgent")]
    assert _order(msgs) == ["info", "task-assignment"]


def test_timestamp_still_breaks_a_full_tie():
    newer = _m("question", ts="2026-10-02T00:00:00Z")
    older = _m("question", ts="2026-10-01T00:00:00Z")
    assert [m["timestamp"] for m in sorted([newer, older], key=make_sort_key)] == [
        "2026-10-01T00:00:00Z",
        "2026-10-02T00:00:00Z",
    ]
