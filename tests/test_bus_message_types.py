"""A message's TYPE is frontmatter, never its filename.

Prompted by plugin-agent (20260926T095116), who hit the same class in their
dispatch sweep — matching `*task-complete*.md` — and said plainly that any sweep
matching on filename has the same gap. Two sites in this repo did.

The live bus is the fixture that matters: 75 files match
`*spec-change-approved*`, but only 70 carry `type: spec-change-approved`. The
other 5 are `type: info` — messages ABOUT an approval, whose subject put the
phrase in the slug. Each became a phantom "approved but never authored" row
telling spec-agent to author a change nobody approved.
"""

from __future__ import annotations

import pytest

from otaman_cli.bus_message_types import messages_of_type


def _msg(d, name, msg_type, subject="s"):
    p = d / name
    p.write_text(
        f"---\nid: x\nfrom: a\nto: b\ntype: {msg_type}\n---\n\n## Subject: {subject}\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def bus(tmp_path):
    d = tmp_path / "active"
    d.mkdir()
    return d


def test_a_message_discussing_an_approval_is_not_an_approval(bus):
    """The live false hit, reduced: an `info` whose subject names the type."""
    _msg(bus, "20260608T194325-plugin-agent-to-spec-agent-spec-change-approved-ce.md", "info")
    assert messages_of_type(bus, "spec-change-approved") == []


def test_a_real_approval_is_found(bus):
    p = _msg(bus, "20260921T150534-human-to-all-spec-change-approved.md", "spec-change-approved")
    assert messages_of_type(bus, "spec-change-approved") == [p]


def test_an_approval_whose_filename_hides_the_type_is_still_found(bus):
    """The latent direction — zero instances live today, but another producer
    (the MCP bus server) need not embed the type in the name."""
    p = _msg(bus, "20260921T150534-some-unrelated-slug.md", "spec-change-approved")
    assert messages_of_type(bus, "spec-change-approved") == [p]


def test_several_types_can_be_requested(bus):
    a = _msg(bus, "1-a.md", "spec-change-approved")
    r = _msg(bus, "2-b.md", "spec-change-rejected")
    _msg(bus, "3-c.md", "info")
    found = messages_of_type(bus, "spec-change-approved", "spec-change-rejected")
    assert found == [a, r]


def test_results_stay_in_filename_order(bus):
    """Filenames lead with a compact timestamp, and callers read them as
    chronological — so the order is part of the contract."""
    later = _msg(bus, "20260925T120000-x.md", "spec-change-approved")
    earlier = _msg(bus, "20260921T120000-x.md", "spec-change-approved")
    assert messages_of_type(bus, "spec-change-approved") == [earlier, later]


def test_a_file_with_no_frontmatter_is_not_a_message(bus):
    (bus / "README.md").write_text("# Notes\n\nnot a message\n", encoding="utf-8")
    assert messages_of_type(bus, "spec-change-approved") == []


def test_a_type_appearing_only_in_the_body_does_not_count(bus):
    """Quoting a type inside a message must not make the message that type.

    The frontmatter deliberately has NO `type:` here. An earlier version of
    this test put `type: info` in the frontmatter and passed even with the
    end-of-frontmatter break removed — the early return fired on line 2 and the
    body was never reached, so it proved nothing about the boundary it named.
    """
    p = bus / "20260921T1-quote.md"
    # The quoted line starts at column 0, which is the shape that actually
    # tests the boundary: a body line merely CONTAINING "type:" is stopped by
    # startswith, so a prose version of this test proved nothing. Messages
    # quoting a YAML block (contract-change messages do this routinely) put a
    # bare `type:` at the start of a body line for real.
    p.write_text(
        "---\nid: x\nfrom: a\n---\n\nThe message it refers to said:\n\n"
        "type: spec-change-approved\n",
        encoding="utf-8",
    )
    assert messages_of_type(bus, "spec-change-approved") == []


def test_no_types_requested_returns_nothing_rather_than_everything(bus):
    """A caller that resolved its type list to empty must not get the whole bus."""
    _msg(bus, "1-a.md", "spec-change-approved")
    assert messages_of_type(bus) == []
    assert messages_of_type(bus, "") == []


def test_a_missing_directory_is_empty_not_an_error(bus):
    assert messages_of_type(bus.parent / "nope", "spec-change-approved") == []


def test_recursive_reaches_the_archive(bus):
    archived = bus.parent / "active" / "archive"
    archived.mkdir()
    p = _msg(archived, "20260101T1-old.md", "spec-change-approved")
    assert messages_of_type(bus, "spec-change-approved") == []
    assert p in messages_of_type(bus, "spec-change-approved", recursive=True)


def test_the_lifecycle_report_no_longer_invents_an_approval(tmp_path):
    """End to end on the site that was biting: an `info` message about an
    approval must not produce an APPROVED_UNAUTHORED row."""
    from otaman_cli.lifecycle import _approved_titles

    d = tmp_path / "active"
    d.mkdir()
    _msg(
        d,
        "20260608T194325-plugin-agent-to-spec-agent-spec-change-approved-ce.md",
        "info",
        subject="Approved: something someone else approved",
    )
    assert _approved_titles(d) == []

    _msg(
        d,
        "20260921T150534-human-to-all-spec-change-approved.md",
        "spec-change-approved",
        subject="Approved: a real one",
    )
    titles = _approved_titles(d)
    assert len(titles) == 1 and "a real one" in titles[0][0]
