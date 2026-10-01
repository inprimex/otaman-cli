"""`otaman blocked <slug>` could only ever write an entry its own reader calls stale.

Found by using it. Registering

    otaman blocked "llm-router-backend 1.4 ..." --blocked-by core-agent

and then running `otaman check` printed:

    [X] BLOCKED TASKS: 0 live, 1 stale
      * [stale] llm-router-backend 1.4 ...
          no stable ref recorded — cannot be resolved to a proposal or change

The read surface was right. `blocked_entries.stale_reason` returns exactly that
for any entry with no ref, and `render_entry` has taken `ref`/`ref_label` the
whole time — the write path simply never passed one. So every entry this verb has
ever written was born stale, and the only way to get a live one was to hand-edit
the file the command exists to stop people hand-editing.

`--change` supplies the ref. It is validated by the SAME predicate `otaman
complete` uses, because an entry referencing a change that does not exist is
stale for the other reason ("change X not found"), which reads like an archived
change rather than a typo.
"""

from __future__ import annotations

import pytest

pytest.importorskip("otaman_core.blocked_entries")

from otaman_core.blocked_entries import parse_entries, stale_reason  # noqa: E402

from otaman_cli.commands.blocked import cmd_blocked  # noqa: E402


@pytest.fixture
def program(isolate_bus, monkeypatch):
    """A sandbox program root with a specs repo holding one real change."""
    root = isolate_bus
    (root / ".agents" / "blocked").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nspecs:\n  path: ./specs\nrepos: []\n",
        encoding="utf-8",
    )
    (root / "specs" / "openspec" / "changes" / "real-change").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def _entries(root):
    """Every entry in the sandbox, whichever agent the command resolved to.

    Deliberately NOT re-deriving the agent name: the command resolves it from the
    sandbox (`unknown-agent` here) and a test that guesses a different name reads
    an empty file and calls it "nothing was written" — which is what this helper
    did first, and it looked exactly like the bug under test.
    """
    directory = root / ".agents" / "blocked"
    out = []
    for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        out.extend(parse_entries(path.read_text(encoding="utf-8")))
    return out


def test_a_change_ref_is_written_and_the_entry_is_not_stale(program, capsys):
    """THE DEFECT. Without `--change` there was no way to write a live entry."""
    assert cmd_blocked(["waiting on real-change", "--change", "real-change"]) == 0
    capsys.readouterr()

    entries = _entries(program)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.ref == "real-change"
    assert entry.has_ref
    # core's own verdict, asked rather than reimplemented — the surface that
    # called the old entries stale is the one that has to call this one live.
    assert stale_reason(entry, known_refs={"real-change"}) == ""


def test_without_a_ref_the_entry_is_still_written_and_the_staleness_is_stated(program, capsys):
    """Not every wait has a change slug — a human decision or an upstream release
    is a real block. So this is a warning, not a refusal. But learning from
    `--list` later that the entry is unresolvable teaches nothing about how to
    avoid it, so the write says so at the moment of writing."""
    assert cmd_blocked(["waiting on a human"]) == 0
    out = capsys.readouterr().out

    entries = _entries(program)
    assert len(entries) == 1
    assert not entries[0].has_ref
    assert "no --change ref" in out
    assert "stale" in out
    # And the warning is true, which is the part worth pinning.
    assert stale_reason(entries[0], known_refs=set()) != ""


def test_a_change_that_does_not_exist_is_refused_with_a_near_miss(program, capsys):
    """A typo'd ref would be stale for the OTHER reason, which reads like an
    archived change. Refused at the point the human can still fix it."""
    assert cmd_blocked(["typo wait", "--change", "reel-change"]) == 1
    out = capsys.readouterr().out

    assert "is not a change" in out
    assert "real-change" in out, "the near-miss suggestion did not name the real change"
    assert _entries(program) == [], "a refused registration must write nothing"


def test_the_status_record_carries_the_change_the_block_waits_on(program, capsys):
    """`otaman status` is being asked WHICH change this block waits on, and the
    record said `change: null` while the entry beside it named one."""
    assert cmd_blocked(["waiting on real-change", "--change", "real-change"]) == 0
    capsys.readouterr()

    status_dir = program / ".agents" / "status"
    files = sorted(status_dir.glob("*.yaml")) if status_dir.is_dir() else []
    if not files:  # presence not enabled in this sandbox — nothing to assert
        pytest.skip("agent presence is not enabled here")
    body = files[0].read_text(encoding="utf-8")
    assert "change: real-change" in body
    assert "state: blocked" in body


def test_the_ref_predicate_is_the_one_complete_uses(program):
    """Shared, not re-derived: two verbs disagreeing about what a change is would
    make one of them refuse what the other accepts."""
    import otaman_cli.commands.blocked as blocked_mod

    src = (blocked_mod.__file__ or "").replace(".pyc", ".py")
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "from otaman_cli.commands.complete import check_change_exists" in body


def test_an_unverifiable_specs_repo_warns_and_proceeds(isolate_bus, monkeypatch, capsys):
    """`check_change_exists` cannot run without a readable specs repo. Losing the
    check there is stated and accepted — refusing would make the verb unusable
    wherever the specs sibling is not checked out."""
    root = isolate_bus
    (root / ".agents" / "blocked").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)

    assert cmd_blocked(["wait", "--change", "anything-at-all"]) == 0
    out = capsys.readouterr().out
    assert "could not verify the change name" in out
    entries = _entries(root)
    assert len(entries) == 1 and entries[0].ref == "anything-at-all"


def test_the_usage_text_says_what_change_is_for(capsys):
    assert cmd_blocked(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--change" in out
