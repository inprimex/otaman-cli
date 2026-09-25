"""knowledge-v2 2.1 — `amend`, and the v2 rendering in `list` / `show`.

The gap this closes, in plugin-agent's own words (20260923T191139): they
recorded a lesson, Roman's ruling made one paragraph of it stale four hours
later, and the only thing they could do was record a SECOND entry naming the
first. "Two entries where one was amended is how a knowledge base starts
rotting — a reader hitting the lesson first gets the stale paragraph and has no
signal that a correction exists."

So the load-bearing assertion here is not that `amend` writes a file. It is that
a reader landing on the STALE entry is told, at that moment, that a correction
exists — the forward direction, which is the one a rotting base loses.
"""

from __future__ import annotations

import pytest

pytest.importorskip("otaman_core.knowledge")

from otaman_core import knowledge as core  # noqa: E402

from otaman_cli.commands.knowledge import cmd_knowledge  # noqa: E402

#: A partition that is NOT the fallback, derived from core rather than named.
#: These tests originally hardcoded "strategy"; core #82 replaced the seeded
#: three with Roman's fixed eight and removed it, turning cli main red against
#: core main. A sibling's enum VALUES are its to change — what these tests
#: actually care about is "some partition other than the default", so they ask.
OTHER_FUNCTION = next(f for f in core.FUNCTIONS if f != core.FUNCTION_DEVELOPMENT)


@pytest.fixture
def program(isolate_bus, monkeypatch):
    root = isolate_bus
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return root


def _dir(root):
    return root / ".agents" / "knowledge"


def _add(title, **kw):
    argv = [
        "add",
        "--title",
        title,
        "--anchor",
        kw.pop("anchor", "x.py:1"),
        "--body",
        kw.pop("body", "b"),
    ]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", v]
    assert cmd_knowledge(argv) == 0


def _stems(root):
    return [e.stem for e in core.load_entries(_dir(root))]


# ---------------------------------------------------------------------------
# the edge


def test_amend_records_a_correction_and_leaves_the_original_untouched(program):
    """The original's bytes are the record of what was believed. A base whose
    past can be rewritten cannot be trusted about what it said last week."""
    _add("a CI leg that cannot fail teaches nobody", body="make it blocking or delete it")
    stale = _stems(program)[0]
    before = (_dir(program) / f"{stale}.md").read_bytes()

    assert (
        cmd_knowledge(
            [
                "amend",
                stale,
                "--title",
                "whether a leg blocks is a PRODUCT question",
                "--anchor",
                "20260923T190124",
                "--body",
                "macOS is held",
            ]
        )
        == 0
    )

    assert (_dir(program) / f"{stale}.md").read_bytes() == before, "the original was rewritten"
    assert len(_stems(program)) == 2


def test_a_reader_on_the_STALE_entry_is_told_a_correction_exists(program, capsys):
    """plugin's actual complaint. The forward edge is the one that matters —
    the correction pointing back helps only a reader who already found it."""
    _add("the stale lesson", body="outdated advice")
    stale = _stems(program)[0]
    cmd_knowledge(
        ["amend", stale, "--title", "the correction", "--anchor", "y.py:2", "--body", "c"]
    )
    capsys.readouterr()

    assert cmd_knowledge(["show", "stale lesson"]) == 0
    out = capsys.readouterr().out
    assert core.AMENDED_FLAG in out
    assert "superseded by" in out
    assert "the correction" in out, "the reader is not told WHICH entry corrects it"
    assert "read that before acting" in out


def test_the_correction_points_back_too(program, capsys):
    _add("original", body="o")
    stale = _stems(program)[0]
    cmd_knowledge(["amend", stale, "--title", "corrector", "--anchor", "y.py:2", "--body", "c"])
    capsys.readouterr()
    cmd_knowledge(["show", "corrector"])
    out = capsys.readouterr().out
    assert "corrects:" in out and stale in out


def test_amending_a_missing_stem_refuses_without_a_traceback(program, capsys):
    """core raises KnowledgeError for a dangling edge; the operator must see a
    sentence, not a stack."""
    rc = cmd_knowledge(["amend", "no-such-stem", "--title", "t", "--anchor", "y.py:1"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "no-such-stem" in out
    assert "otaman knowledge list" in out, "must say how to find the right stem"
    assert "Traceback" not in out


def test_a_correction_still_needs_its_own_anchor(program, capsys):
    _add("original", body="o")
    stale = _stems(program)[0]
    capsys.readouterr()
    rc = cmd_knowledge(["amend", stale, "--title", "no anchor", "--body", "c"])
    assert rc == 2
    assert "anchor" in capsys.readouterr().out
    assert len(_stems(program)) == 1, "a refused correction was written anyway"


def test_a_correction_inherits_the_type_unless_told_otherwise(program):
    """A correction to a lesson is a lesson; making the operator restate it
    invites a silent reclassification."""
    _add("a lesson", type="lesson", body="o")
    stale = _stems(program)[0]
    cmd_knowledge(
        ["amend", stale, "--title", "still a lesson", "--anchor", "y.py:1", "--body", "c"]
    )
    by_title = {e.title: e for e in core.load_entries(_dir(program))}
    assert by_title["still a lesson"].type == "lesson"


def test_a_legacy_entry_with_no_partition_can_still_be_amended(program):
    """Pre-v2 entries carry function="" which validation REJECTS, so inheriting
    it blindly would make every correction to a legacy entry impossible — and
    gate 4.1's own test subject is a legacy entry."""
    legacy = core.KnowledgeEntry(
        type="lesson",
        author="plugin-agent",
        created="2026-09-23",
        review_by="2026-12-22",
        anchor="x.py:1",
        title="week one lesson",
        body="stale",
    )
    assert legacy.function == "", "fixture is not actually legacy"
    core.write_entry(_dir(program), legacy)

    assert (
        cmd_knowledge(
            ["amend", legacy.stem, "--title", "the correction", "--anchor", "y.py:1", "--body", "c"]
        )
        == 0
    )
    correcting = [e for e in core.load_entries(_dir(program)) if e.supersedes == legacy.stem]
    assert correcting and correcting[0].function in core.FUNCTIONS


# ---------------------------------------------------------------------------
# list / show v2 rendering


def test_list_flags_the_amended_entry(program, capsys):
    _add("original", body="o")
    stale = _stems(program)[0]
    cmd_knowledge(["amend", stale, "--title", "corrector", "--anchor", "y.py:1", "--body", "c"])
    capsys.readouterr()
    cmd_knowledge(["list"])
    out = capsys.readouterr().out
    assert core.AMENDED_FLAG in out
    assert "a correction supersedes them" in out


def test_list_is_active_only_by_default_and_says_so(program, capsys):
    """The index preloads active entries — that is the token scoping. A default
    that hides rows must state itself or a missing entry looks like a bug."""
    _add("an active one", body="o")
    stem = _stems(program)[0]
    core.set_state(_dir(program), stem, core.STATE_DORMANT)
    capsys.readouterr()

    cmd_knowledge(["list"])
    out = capsys.readouterr().out
    # Substance, not exact wording: the message must NAME the active default as
    # the reason the row is absent. (It reads "state active (default)" when the
    # default filtered everything out, and "active only — --all-states…" in the
    # footer when rows remain.)
    assert core.STATE_ACTIVE in out and "default" in out
    assert "an active one" not in out

    cmd_knowledge(["list", "--all-states"])
    assert "an active one" in capsys.readouterr().out


def test_a_budget_never_truncates_silently(program, capsys):
    for i in range(3):
        _add(f"entry {i}", body="b")
    capsys.readouterr()
    cmd_knowledge(["list", "--budget", "1"])
    out = capsys.readouterr().out
    assert "2 more not shown" in out, "a budget hid rows without saying so"


def test_partition_scoping_filters_and_names_the_filter(program, capsys):
    _add("dev thing", body="b")
    capsys.readouterr()
    cmd_knowledge(["list", "--function", OTHER_FUNCTION])
    out = capsys.readouterr().out
    # The partition is named; the active-only default is named alongside it,
    # which is correct — both filters were applied.
    assert f"partition {OTHER_FUNCTION}" in out
    assert "recorded in total" in out


def test_list_renders_through_cores_index_line(program, capsys):
    """D5: one index-line format across every surface. A second format here is
    the drift single-home removes."""
    _add("some title", body="b")
    entry = core.load_entries(_dir(program))[0]
    capsys.readouterr()
    cmd_knowledge(["list"])
    assert core.index_line(entry) in capsys.readouterr().out


def test_show_bumps_accessed_at(program):
    """Reading is the reinforcement signal that keeps an entry out of the decay
    sweep — so `show` has to record it."""
    _add("read me", body="b")
    stem = _stems(program)[0]
    assert core.load_entry_by_stem(_dir(program), stem).accessed_at == ""
    cmd_knowledge(["show", "read me"])
    assert core.load_entry_by_stem(_dir(program), stem).accessed_at != ""


def test_list_json_carries_the_v2_fields(program, capsys):
    import json

    _add("original", body="o")
    stale = _stems(program)[0]
    cmd_knowledge(["amend", stale, "--title", "corrector", "--anchor", "y.py:1", "--body", "c"])
    capsys.readouterr()
    cmd_knowledge(["list", "--json"])
    rows = {r["title"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["original"]["amended"] is True
    assert rows["corrector"]["supersedes"] == stale
    assert rows["corrector"]["state"] == core.STATE_ACTIVE
    assert rows["corrector"]["function"] in core.FUNCTIONS


# ---------------------------------------------------------------------------
# partition derivation


def test_the_partition_is_derived_and_the_fallback_is_announced(program, capsys):
    """No partitions map exists yet (plugin 3.1). Falling back silently would
    misfile the entry into another agent's index; the note is the difference
    between a fallback and a bug."""
    _add("a fact", body="b")
    out = capsys.readouterr().out
    assert "no knowledge partitions declared" in out
    assert core.FUNCTION_DEVELOPMENT in out


def test_a_declared_partition_map_is_used(program, capsys):
    (program / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n"
        "program:\n  processes:\n    knowledge:\n      partitions:\n"
        f"        {OTHER_FUNCTION}: cli-agent\n"
        f"        {core.FUNCTION_DEVELOPMENT}: someone-else\n",
        encoding="utf-8",
    )
    _add("mine", body="b")
    out = capsys.readouterr().out
    assert "no knowledge partitions declared" not in out
    assert core.load_entries(_dir(program))[0].function == OTHER_FUNCTION


def test_an_explicit_function_overrides_the_derivation(program):
    _add("explicit", body="b", function=OTHER_FUNCTION)
    assert core.load_entries(_dir(program))[0].function == OTHER_FUNCTION
