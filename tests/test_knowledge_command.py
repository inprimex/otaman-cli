"""shared-agent-memory 1.2 — `otaman knowledge add|list|show`.

The directory existed for months and stayed empty; core supplies the shape and
IO, this is the verb. Nothing here re-derives the schema — the tests that matter
are about the operator surface: the anchor refusal, the past-due flag, and the
round-trip guard.

That last one came from the first real `add` I ran. A title of "worktree
sessions need core #73" recorded fine and read back as "worktree sessions need
core": core's renderer writes frontmatter UNQUOTED, so YAML treats `#` as a
comment. The file parsed cleanly, so the entry looked recorded and was quietly
wrong — and PR numbers are exactly what a knowledge title carries.
"""

from __future__ import annotations

import datetime

import pytest

pytest.importorskip("otaman_core.knowledge")

from otaman_core import knowledge as core  # noqa: E402

from otaman_cli.commands.knowledge import cmd_knowledge  # noqa: E402


@pytest.fixture
def program(isolate_bus, monkeypatch):
    root = isolate_bus
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return root


def _entries(root):
    return core.load_entries(root / ".agents" / "knowledge")


# ---------------------------------------------------------------------------
# the anchor rule


def test_an_entry_without_an_anchor_is_refused_naming_the_rule(program, capsys):
    rc = cmd_knowledge(["add", "--title", "a claim", "--body", "x"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "evidence anchor is required" in out
    assert "--anchor" in out, "the refusal must say how to satisfy it"
    assert _entries(program) == [], "refused entry was written anyway"


def test_an_anchored_entry_records(program):
    rc = cmd_knowledge(
        [
            "add",
            "--title",
            "worktree owner resolution",
            "--anchor",
            "owner_paths.py:288",
            "--body",
            "core 73 folded resolve_worktree_main in.",
        ]
    )
    assert rc == 0
    entries = _entries(program)
    assert len(entries) == 1
    assert entries[0].anchor == "owner_paths.py:288"
    # The resolved identity, NOT the env var: the sandbox's platform.yaml owns
    # this cwd, and shared-logic 1.5 makes cwd-ownership authoritative over
    # OTAMAN_AGENT (B1). Asserting "cli-agent" here would be asserting the bug
    # that ruling exists to prevent.
    from otaman_cli.identity import resolve_agent_identity

    assert entries[0].author == resolve_agent_identity(program)
    assert entries[0].author, "author must not be blank"


def test_an_unrecognised_anchor_warns_but_records(program, capsys):
    """Presence is the hard rule; shape is advisory (core's own distinction)."""
    rc = cmd_knowledge(["add", "--title", "t", "--anchor", "vibes", "--body", "x"])
    assert rc == 0
    assert "not a recognised shape" in capsys.readouterr().out
    assert len(_entries(program)) == 1


# ---------------------------------------------------------------------------
# the round-trip guard


def test_a_title_that_does_not_survive_writing_is_refused(program, capsys):
    """The live finding: `#` opens a YAML comment, so the title was truncated
    and the entry still parsed — recorded, and quietly wrong."""
    rc = cmd_knowledge(
        ["add", "--title", "worktree sessions need core #73", "--anchor", "x.py:1", "--body", "b"]
    )
    assert rc == 2
    out = capsys.readouterr().out
    assert "does not survive being written" in out
    assert "#73" in out and "read back" in out, "must show what was lost"
    assert _entries(program) == [], "a mangled entry was left on disk"


def test_the_mangled_file_is_removed_not_left_behind(program):
    cmd_knowledge(["add", "--title", "a # b", "--anchor", "x.py:1", "--body", "b"])
    assert list((program / ".agents" / "knowledge").glob("*.md")) == []


def test_a_rephrased_title_records_fine(program):
    """The refusal is actionable — the suggested rephrasing works."""
    assert (
        cmd_knowledge(
            [
                "add",
                "--title",
                "worktree sessions need core PR 73",
                "--anchor",
                "x.py:1",
                "--body",
                "b",
            ]
        )
        == 0
    )
    assert len(_entries(program)) == 1


@pytest.mark.parametrize("title", ["plain title", "colon: in title", "a #hash", "[bracketed]"])
def test_round_trip_guard_sweeps_yaml_significant_characters(program, title):
    """Whatever the outcome, it is never 'recorded but different'."""
    rc = cmd_knowledge(["add", "--title", title, "--anchor", "x.py:1", "--body", "b"])
    entries = _entries(program)
    if rc == 0:
        assert [e.title for e in entries] == [title]
    else:
        assert entries == []


# ---------------------------------------------------------------------------
# list / show


def test_list_flags_a_past_due_entry(program, capsys):
    long_ago = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    stale = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    cmd_knowledge(
        [
            "add",
            "--title",
            "old fact",
            "--anchor",
            "x.py:1",
            "--body",
            "b",
            "--created",
            long_ago,
            "--review-by",
            stale,
        ]
    )
    capsys.readouterr()
    assert cmd_knowledge(["list"]) == 0
    out = capsys.readouterr().out
    assert "review overdue" in out
    assert "correct or retire" in out


def test_list_past_due_filter_excludes_current_entries(program, capsys):
    cmd_knowledge(["add", "--title", "current", "--anchor", "x.py:1", "--body", "b"])
    capsys.readouterr()
    cmd_knowledge(["list", "--past-due"])
    assert "current" not in capsys.readouterr().out


def test_list_on_an_empty_pile_says_how_to_start(program, capsys):
    assert cmd_knowledge(["list"]) == 0
    out = capsys.readouterr().out
    assert "No knowledge entries" in out
    assert "--anchor" in out


def test_show_renders_one_entry(program, capsys):
    cmd_knowledge(["add", "--title", "the lesson", "--anchor", "x.py:1", "--body", "the body"])
    capsys.readouterr()
    assert cmd_knowledge(["show", "lesson"]) == 0
    out = capsys.readouterr().out
    assert "the body" in out and "x.py:1" in out


def test_show_refuses_an_ambiguous_match_rather_than_guessing(program, capsys):
    for n in ("alpha one", "alpha two"):
        cmd_knowledge(["add", "--title", n, "--anchor", "x.py:1", "--body", "b"])
    capsys.readouterr()
    assert cmd_knowledge(["show", "alpha"]) == 1
    assert "match" in capsys.readouterr().out


def test_list_json_carries_the_past_due_flag(program, capsys):
    import json

    cmd_knowledge(["add", "--title", "t", "--anchor", "x.py:1", "--body", "b"])
    capsys.readouterr()
    cmd_knowledge(["list", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["past_due"] is False
    assert rows[0]["anchor"] == "x.py:1"
