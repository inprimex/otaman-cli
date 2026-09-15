"""blocked-entry-lifecycle 1.1/1.3/1.6 — the one blocked-entry parser + `complete`.

Entries were parsed by ad-hoc regexes in five places, each with its own idea of
what an entry is. That is why `complete`'s clearer matched the CHANGE NAME inside
the entry TITLE while `propose` titles entries by proposal title — so it could
never fire, and deploy-agent's file grew to 16 entries of which 5/5 sampled were
already archived.

These pin the three properties that were previously broken or absent:
ref-based matching, `--tasks` clearing, and approval waits surviving task
completion.
"""

from __future__ import annotations

import pytest

from otaman_cli.blocked_entries import (
    KIND_APPROVAL,
    KIND_DEPENDENCY,
    find_by_ref,
    parse_entries,
    render_entry,
    tombstone,
)

APPROVAL_ENTRY = """## Blocked: blocked-entry lifecycle: what terminates an entry
- **Proposal**: 20260913T144246-cli-agent-to-human-spec-change-request
- **Blocked since**: 2026-09-13T14:42:46Z
- **Depends on**: spec-change-approved + spec-change notification
"""

DEPENDENCY_ENTRY = """## Blocked: waiting on the policy engine
- **Change**: policy-engine
- **Kind**: awaiting-dependency
- **Blocked since**: 2026-09-01T00:00:00Z
"""


# ---------------------------------------------------------------------------
# parsing


def test_parses_title_and_fields():
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert entry.title.startswith("blocked-entry lifecycle")
    assert entry.proposal == "20260913T144246-cli-agent-to-human-spec-change-request"
    assert entry.get("blocked since") == "2026-09-13T14:42:46Z"


def test_field_lookup_is_case_insensitive():
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert entry.get("Proposal") == entry.get("proposal") == entry.proposal


def test_parses_multiple_entries():
    entries = parse_entries(APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY)
    assert len(entries) == 2
    assert [e.kind for e in entries] == [KIND_APPROVAL, KIND_DEPENDENCY]


def test_empty_and_garbage_text():
    assert parse_entries("") == []
    assert parse_entries("no entries here\njust prose\n") == []


# ---------------------------------------------------------------------------
# kind inference — legacy entries are READ, not rejected (1.1 is additive)


def test_declared_kind_wins():
    (entry,) = parse_entries(DEPENDENCY_ENTRY)
    assert entry.kind == KIND_DEPENDENCY


def test_legacy_entry_with_proposal_infers_approval():
    """The live legacy shape: no Kind field, but a Proposal ref and text that
    literally says it waits on human approval."""
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert "kind" not in entry.fields
    assert entry.kind == KIND_APPROVAL


def test_legacy_entry_without_a_proposal_infers_dependency():
    (entry,) = parse_entries("## Blocked: something\n- **Blocked by**: human\n")
    assert entry.kind == KIND_DEPENDENCY


def test_unknown_kind_value_falls_back_to_inference():
    (entry,) = parse_entries("## Blocked: x\n- **Proposal**: stem-1\n- **Kind**: nonsense\n")
    assert entry.kind == KIND_APPROVAL


# ---------------------------------------------------------------------------
# the stable ref


def test_ref_prefers_proposal():
    (entry,) = parse_entries("## Blocked: x\n- **Proposal**: stem-1\n- **Change**: ch\n")
    assert entry.ref == "stem-1" and entry.has_ref is True


def test_ref_falls_back_to_change():
    (entry,) = parse_entries(DEPENDENCY_ENTRY)
    assert entry.ref == "policy-engine"


def test_entry_without_a_ref():
    (entry,) = parse_entries("## Blocked: refless\n- **Blocked by**: human\n")
    assert entry.ref == "" and entry.has_ref is False


def test_find_by_ref_is_exact_not_substring():
    """A substring match over free text is how an unrelated entry gets cleared by
    someone else's completion — the failure mode this replaces."""
    text = "## Blocked: a\n- **Change**: policy-engine\n\n## Blocked: b\n- **Change**: policy\n"
    assert [e.title for e in find_by_ref(text, "policy")] == ["b"]
    assert [e.title for e in find_by_ref(text, "policy-engine")] == ["a"]


def test_find_by_ref_filters_by_kind():
    text = APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY
    assert find_by_ref(text, "policy-engine", kinds=(KIND_APPROVAL,)) == []
    assert len(find_by_ref(text, "policy-engine", kinds=(KIND_DEPENDENCY,))) == 1


def test_find_by_ref_empty_ref_matches_nothing():
    assert find_by_ref(APPROVAL_ENTRY, "") == []
    assert find_by_ref(APPROVAL_ENTRY, "   ") == []


# ---------------------------------------------------------------------------
# tombstones — plugin's format, matched not redefined


def test_tombstone_wraps_and_is_excluded_from_live_reads():
    entries = parse_entries(DEPENDENCY_ENTRY)
    out = tombstone(DEPENDENCY_ENTRY, entries, reason="completed policy-engine", today="2026-09-16")
    assert "<!-- ## Blocked:" in out
    assert "cleared 2026-09-16 — completed policy-engine -->" in out
    assert parse_entries(out) == []  # no longer live


def test_tombstoned_entries_visible_when_asked():
    out = tombstone(
        DEPENDENCY_ENTRY, parse_entries(DEPENDENCY_ENTRY), reason="x", today="2026-09-16"
    )
    (entry,) = parse_entries(out, include_tombstoned=True)
    assert entry.tombstoned is True and entry.cleared_reason == "x"


def test_tombstone_is_idempotent():
    once = tombstone(
        DEPENDENCY_ENTRY, parse_entries(DEPENDENCY_ENTRY), reason="x", today="2026-09-16"
    )
    twice = tombstone(
        once, parse_entries(once, include_tombstoned=True), reason="x", today="2026-09-16"
    )
    assert twice == once


def test_tombstone_leaves_siblings_alone():
    text = APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY
    targets = find_by_ref(text, "policy-engine")
    out = tombstone(text, targets, reason="done", today="2026-09-16")
    live = parse_entries(out)
    assert [e.kind for e in live] == [KIND_APPROVAL]  # the approval wait survives


# ---------------------------------------------------------------------------
# rendering (1.1 — additive Kind)


def test_render_includes_kind_and_keeps_existing_fields():
    text = render_entry(
        "my task", kind=KIND_DEPENDENCY, since="2026-09-16T00:00:00Z", extra={"Blocked by": "human"}
    )
    assert text.startswith("## Blocked: my task")
    assert "- **Kind**: awaiting-dependency" in text
    assert "- **Blocked since**: 2026-09-16T00:00:00Z" in text
    assert "- **Blocked by**: human" in text


def test_render_round_trips_through_the_parser():
    text = render_entry("t", kind=KIND_APPROVAL, ref="stem-9", since="2026-09-16T00:00:00Z")
    (entry,) = parse_entries(text)
    assert entry.title == "t" and entry.kind == KIND_APPROVAL and entry.ref == "stem-9"


def test_render_defaults_kind_from_the_ref_label():
    assert "awaiting-approval" in render_entry("t", ref="s", ref_label="Proposal")
    assert "awaiting-dependency" in render_entry("t", ref="c", ref_label="Change")


# ---------------------------------------------------------------------------
# 1.3 — `complete` clears DEPENDENCY waits, by ref, under --tasks, never approvals


@pytest.fixture
def blocked_file(tmp_path):
    root = tmp_path / "meta"
    d = root / ".agents" / "blocked"
    d.mkdir(parents=True)
    f = d / "cli-agent.md"
    f.write_text(APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY, encoding="utf-8")
    return root, f


def test_complete_clears_the_dependency_wait_by_ref(blocked_file, capsys):
    from otaman_cli.commands.complete import _clear_dependency_waits

    root, f = blocked_file
    assert _clear_dependency_waits(root, "cli-agent", "policy-engine") == 1
    capsys.readouterr()
    live = parse_entries(f.read_text(encoding="utf-8"))
    assert [e.kind for e in live] == [KIND_APPROVAL]


def test_complete_NEVER_clears_an_approval_wait(blocked_file, capsys):
    """The ruling: an entry waiting on a human decision is terminated by that
    decision, never by somebody shipping code. Clearing it here would re-create
    the original bug in reverse — a genuinely blocked item vanishing silently."""
    from otaman_cli.commands.complete import _clear_dependency_waits

    root, f = blocked_file
    # the approval entry's ref IS this proposal stem — still must not clear
    stem = "20260913T144246-cli-agent-to-human-spec-change-request"
    assert _clear_dependency_waits(root, "cli-agent", stem) == 0
    capsys.readouterr()
    assert len(parse_entries(f.read_text(encoding="utf-8"))) == 2


def test_complete_does_not_clear_an_unrelated_change(blocked_file, capsys):
    from otaman_cli.commands.complete import _clear_dependency_waits

    root, f = blocked_file
    assert _clear_dependency_waits(root, "cli-agent", "some-other-change") == 0
    capsys.readouterr()
    assert len(parse_entries(f.read_text(encoding="utf-8"))) == 2


def test_complete_tombstones_rather_than_deletes(blocked_file, capsys):
    from otaman_cli.commands.complete import _clear_dependency_waits

    root, f = blocked_file
    _clear_dependency_waits(root, "cli-agent", "policy-engine")
    capsys.readouterr()
    text = f.read_text(encoding="utf-8")
    assert "cleared" in text and "completed policy-engine" in text
    assert "waiting on the policy engine" in text  # the record survives


def test_complete_is_a_noop_without_a_blocked_file(tmp_path, capsys):
    from otaman_cli.commands.complete import _clear_dependency_waits

    root = tmp_path / "meta"
    root.mkdir()
    assert _clear_dependency_waits(root, "cli-agent", "anything") == 0
    capsys.readouterr()


def test_complete_clear_is_idempotent(blocked_file, capsys):
    from otaman_cli.commands.complete import _clear_dependency_waits

    root, f = blocked_file
    assert _clear_dependency_waits(root, "cli-agent", "policy-engine") == 1
    assert _clear_dependency_waits(root, "cli-agent", "policy-engine") == 0
    capsys.readouterr()


def test_title_substring_no_longer_clears_anything(tmp_path, capsys):
    """REGRESSION for the original defect's mirror image: matching is by stable
    ref now, so a change name that merely APPEARS in a title clears nothing."""
    from otaman_cli.commands.complete import _clear_dependency_waits

    root = tmp_path / "meta"
    d = root / ".agents" / "blocked"
    d.mkdir(parents=True)
    f = d / "cli-agent.md"
    f.write_text(
        "## Blocked: something about policy-engine in the title\n- **Blocked by**: human\n",
        encoding="utf-8",
    )
    assert _clear_dependency_waits(root, "cli-agent", "policy-engine") == 0
    capsys.readouterr()
    assert len(parse_entries(f.read_text(encoding="utf-8"))) == 1
