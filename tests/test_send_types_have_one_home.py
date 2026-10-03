"""Every type core declares valid is CLASSIFIED here — none may be silently absent.

`otaman send` keeps its own narrower allow-list (`MESSAGE_TYPES`): core's `VALID_TYPES`
is the authority on what is a legal message, but most of those are emitted by machinery,
not typed by a person. That curation is legitimate and it is a SECOND PRODUCER of a fact
core owns, which is the shape that drifts — "agrees with core right up to the release
where it doesn't".

It already did. `decision-required` was valid in core and absent from cli's list, and
`cmd_send` hard-rejects an unregistered type, so the emit that every agent's operating
rules make a DUTY before blocking exited 2:

    [!] Unknown message type 'decision-required'.

plugin-agent hit it for real on 2026-10-03 (20261003T074941) while blocked and following
the rule verbatim, and fell back to `question`. The duty named a message the tooling
could not produce — and an agent who hits a refusal may simply not emit, which is the
silent halt the rule exists to prevent. The cost of that class is already measured:
~11h of fleet delivery on 2026-09-25 and a 62-hour frozen verification gate on 09-26.

So the omission is what gets guarded, not the list. A new non-privileged type in core
must be classified as hand-sendable or machine-emitted, and until someone does, this
test fails.
"""

from __future__ import annotations

from otaman_core.validate_message import PRIVILEGED_TYPES, VALID_TYPES

from otaman_cli.commands.bus_messaging import MACHINE_EMITTED_TYPES, MESSAGE_TYPES


def test_every_core_type_is_classified():
    """The identity that makes a silent omission impossible."""
    classified = set(MESSAGE_TYPES) | set(MACHINE_EMITTED_TYPES) | set(PRIVILEGED_TYPES)
    unclassified = set(VALID_TYPES) - classified

    assert not unclassified, (
        "core declares these types and cli classifies none of them — add each to "
        "MESSAGE_TYPES (a person sends it) or MACHINE_EMITTED_TYPES (a verb/daemon "
        f"sends it): {sorted(unclassified)}"
    )


def test_cli_invents_no_type_of_its_own():
    """The other direction: a type cli offers that core rejects is a send that fails
    at the write, after the operator has composed the whole message."""
    invented = (set(MESSAGE_TYPES) | set(MACHINE_EMITTED_TYPES)) - set(VALID_TYPES)

    assert not invented, f"not valid in core: {sorted(invented)}"


def test_the_three_classifications_are_disjoint():
    """A type in two buckets means the classification carries no information — and a
    privileged type in `MESSAGE_TYPES` would be offered by the general send path, which
    is the authority hole `PRIVILEGED_TYPES` exists to close."""
    assert not set(MESSAGE_TYPES) & set(MACHINE_EMITTED_TYPES)
    assert not set(MESSAGE_TYPES) & set(PRIVILEGED_TYPES), (
        "a privileged type must never be hand-sendable"
    )
    assert not set(MACHINE_EMITTED_TYPES) & set(PRIVILEGED_TYPES)


def test_decision_required_is_hand_sendable():
    """The regression that paid for this file: it is the one type an agent MUST be able
    to send by hand, because the duty is to emit it before blocking."""
    assert "decision-required" in MESSAGE_TYPES
    assert "decision-required" not in PRIVILEGED_TYPES, (
        "it ASKS for a decision rather than asserting one — privileging it would put "
        "the anti-silent-block emit behind the TTY gate that only `otaman approve` has"
    )
