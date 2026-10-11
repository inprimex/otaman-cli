"""Two SCR-pane row defects Roman raised via deploy-agent (20261011T093956).

1. No identifier on any row, so a stem cited in a bus message
   (`20261009T121501`) could not be mapped to a row in the surface that
   displays it.
2. The row was clipped to terminal width AFTER `— from X [a/A/x/d]` was
   appended, so long titles lost the sender AND the action keys while short
   ones kept both — the verbose proposals, which are the ones a reader most
   needs help with, lost the affordance.

Verbatim tails from his paste, each a row that lost its suffix:

    ...fragment clearing becomes opt          <- no sender, no keys
    ...dual-channel v0.3.0, test-tena         <- no sender, no keys
    ...from cli-agent   [a/                   <- keys half-rendered

The third defect in that report — a mandatory queue that is ~93%
already-approved items — is deliberately NOT addressed here. deploy-agent's own
reading is that the fix is sender-side lifecycle resolution, not a renderer
filter, which would hide the defect rather than fix it.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.console import bus
from otaman_cli.console.app import queue_row_label, row_citation_id

_STEM = "20261009T121501-deploy-agent-to-human-release-bookkeeping"


def _p(
    subject: str,
    *,
    mtype: str = "spec-change-request",
    stem: str = _STEM,
    gate_suffix: str = "",
) -> bus.Proposal:
    return bus.Proposal(
        stem=stem,
        subject=subject,
        from_agent="deploy-agent",
        timestamp="2026-10-09T12:15:01Z",
        priority="normal",
        path=Path("/nonexistent"),
        body="",
        msg_type=mtype,
        gate_suffix=gate_suffix,
    )


# ---------------------------------------------------------------------------
# 1. the row carries the id an agent would cite


def test_the_row_carries_the_timestamp_stem_agents_cite():
    assert "20261009T121501" in queue_row_label(_p("short title"))


def test_citation_id_is_the_stem_up_to_the_first_dash():
    assert row_citation_id(_STEM) == "20261009T121501"
    assert row_citation_id("") == ""


def test_a_stemless_row_does_not_render_a_stray_separator():
    """Defensive: the fixtures in older tests pass stem='s'. An empty stem must
    not leave dangling padding where the id would have been."""
    label = queue_row_label(_p("t", stem=""))
    assert "[SCR] [normal] t" in label


# ---------------------------------------------------------------------------
# 2. truncation takes the subject, never the suffix


def _long() -> str:
    return (
        "Release bookkeeping: fragment clearing becomes opt-in per repo and the "
        "manifest names every consumed file so a re-cut cannot silently drop one"
    )


def test_a_long_subject_keeps_the_sender_and_the_action_keys():
    """The inversion Roman hit: the keys are the affordance, so they survive."""
    label = queue_row_label(_p(_long()), width=100)

    assert len(label) <= 100
    assert "from deploy-agent" in label, "provenance must survive truncation"
    assert "[a/A/x/d]" in label, "the action keys must survive truncation"
    assert "…" in label, "and the reader is told the title was cut"


def test_the_subject_is_what_shrinks():
    label = queue_row_label(_p(_long()), width=100)
    assert _long() not in label
    assert label.startswith("* [SCR] [normal] 20261009T121501")


def test_the_gate_suffix_also_survives():
    """The JTBD-57 gate annotation rides the same suffix and is equally the
    reason a reviewer picks a row."""
    p = _p(_long(), gate_suffix="  [gate 72/100 adequate]")
    label = queue_row_label(p, width=110)
    assert "gate 72/100 adequate" in label
    assert "[a/A/x/d]" in label


def test_width_zero_means_do_not_truncate():
    """0 is the 'no layout yet' case — same convention as the artifact tree."""
    label = queue_row_label(_p(_long()), width=0)
    assert _long() in label
    assert "…" not in label


def test_a_short_subject_is_untouched_by_a_generous_width():
    label = queue_row_label(_p("tiny"), width=200)
    assert "tiny" in label
    assert "…" not in label


def test_an_absurdly_narrow_width_still_keeps_the_keys():
    """When nothing fits, the suffix wins — a row with no keys is a row the
    human cannot act on, while a row with no title is merely terse."""
    label = queue_row_label(_p(_long()), width=20)
    assert "[a/A/x/d]" in label
    assert "from deploy-agent" in label


def test_a_plain_message_row_truncates_without_inventing_keys():
    label = queue_row_label(_p(_long(), mtype="info"), width=90)
    assert "[a/A/x/d]" not in label
    assert "from deploy-agent" in label
    assert len(label) <= 90
