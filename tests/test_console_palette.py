"""tree-view-polish 1.2 — the one shared status/priority palette + short forms."""

from __future__ import annotations

from otaman_cli.console import palette


def test_short_priority_and_status_are_lowercase_words():
    assert palette.short_priority("P1") == "p1"
    assert palette.short_status("Approved") == "approved"
    assert palette.short_status("In-Progress") == "in-progress"


def test_short_forms_strip_a_leaked_enum_repr():
    # a stray enum repr must never survive into a console surface
    assert palette.short_status("OutcomeStatus.APPROVED") == "approved"
    assert palette.short_priority("Priority.P0") == "p0"


def test_every_priority_value_has_a_distinct_color():
    colors = [palette.priority_style(p) for p in ("P0", "P1", "P2", "P3")]
    assert all(colors) and len(set(colors)) == 4  # one distinct color each


def test_status_style_maps_known_values_only():
    assert palette.status_style("Approved")  # green family
    assert palette.status_style("Discarded")  # red
    assert palette.status_style("nonsense") == ""


def test_accepts_enum_valued_objects():
    class _E:
        value = "P2"

    assert palette.short_priority(_E()) == "p2"
    assert palette.priority_style(_E()) == palette.PRIORITY_STYLE["P2"]
