"""`otaman blocked clear <ref>` could not clear a dependency wait at all.

The clear-by-stem path was the NINTH surface-local blocked-entry parser: three
regexes of its own (a section splitter, a `**Proposal**` reader, a title reader),
and it matched on the Proposal field ALONE.

Measured on a two-entry fixture before the change:

    clear <proposal-stem>  -> tombstoned, rc 0
    clear <change-slug>    -> "No blocked entry found", rc 0, nothing written

So an `awaiting-dependency` entry — keyed by `**Change**`, carrying no Proposal,
which is the shape `otaman blocked` writes for a wait on another agent — was
unclearable by the verb. The only way to terminate one was to hand-edit the
register, and hand-editing is how this bookkeeping gets corrupted. My own live
entry (`llm-router-1-4-telemetry`, `**Change**: llm-router-backend`) is exactly
that shape, which is how the gap was found.

Now it consumes `otaman_core.blocked_entries.find_by_ref` / `tombstone`: the ref is
proposal-first, change-second, and the tombstone format is the one plugin's
`_auto_tombstone_blocked` writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import otaman_cli.commands.blocked as B

APPROVAL_REF = "20260101T000000-stem-a"
CHANGE_REF = "llm-router-backend"

FIXTURE = (
    "# Blocked — cli-agent\n\n"
    "## Blocked: approval wait\n"
    f"- **Proposal**: {APPROVAL_REF}\n"
    "- **Blocked since**: 2026-01-01T00:00:00Z\n\n"
    "## Blocked: dependency wait\n"
    f"- **Change**: {CHANGE_REF}\n"
    "- **Kind**: awaiting-dependency\n"
    "- **Blocked since**: 2026-10-02T13:27:54Z\n"
)


@pytest.fixture
def program(tmp_path: Path):
    root = tmp_path / "meta"
    (root / ".agents" / "blocked").mkdir(parents=True)
    bf = root / ".agents" / "blocked" / "cli-agent.md"
    bf.write_text(FIXTURE, encoding="utf-8")
    return root, bf


def _live_headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## Blocked:")]


def test_a_dependency_wait_clears_by_its_change_slug(program, capsys):
    """THE GAP: this did nothing at all before, and said so with rc 0."""
    root, bf = program

    rc = B._cmd_blocked_clear_by_stem(root, CHANGE_REF)

    after = bf.read_text(encoding="utf-8")
    assert rc == 0
    assert "<!-- ## Blocked: dependency wait" in after, "the dependency wait was not cleared"
    assert "dependency wait" in capsys.readouterr().out
    # and the other entry is untouched and still LIVE
    assert _live_headers(after) == ["## Blocked: approval wait"]


def test_an_approval_wait_still_clears_by_its_proposal_stem(program):
    """The documented contract, unchanged — proposal is matched first."""
    root, bf = program

    rc = B._cmd_blocked_clear_by_stem(root, APPROVAL_REF)

    after = bf.read_text(encoding="utf-8")
    assert rc == 0
    assert "<!-- ## Blocked: approval wait" in after
    assert _live_headers(after) == ["## Blocked: dependency wait"]


def test_an_unknown_ref_does_not_write_AT_ALL_and_says_so(program, capsys, monkeypatch):
    """No write, not merely no change.

    A byte-equality assertion alone passes when the verb rewrites the file with
    identical content — and on a bus register that is not harmless: the mtime is
    EVIDENCE. The 2026-10-02 forensics dated an archive pass, ruled a window in and
    out, and found 57 "lost" messages entirely from directory and file mtimes. A
    verb that touches a register it did not change corrupts exactly that.
    """
    root, bf = program
    before = bf.read_bytes()
    writes: list[str] = []
    real_write = Path.write_text

    def _recording_write(self, data, *a, **k):
        writes.append(str(self))
        return real_write(self, data, *a, **k)

    monkeypatch.setattr(Path, "write_text", _recording_write)

    rc = B._cmd_blocked_clear_by_stem(root, "no-such-ref")

    assert rc == 0, "no-match is not an error (idempotent clear)"
    assert writes == [], f"an unmatched clear wrote: {writes}"
    assert bf.read_bytes() == before
    assert "No blocked entry found" in capsys.readouterr().out


def test_clearing_one_entry_leaves_the_following_one_readable(program):
    """The failure core's `tombstone` exists to prevent.

    A bare `rstrip()` glues the closing `-->` onto the next entry's header, so it
    is no longer line-leading and the parser cannot see it — clearing one entry
    silently hides every live entry after it (plugin repro 20260921T151120).
    Asserted through the PARSER, not the text, because that is who gets fooled.
    """
    root, bf = program
    from otaman_cli.blocked_gate import blocked_entries

    mod = blocked_entries()
    assert mod is not None

    B._cmd_blocked_clear_by_stem(root, APPROVAL_REF)

    live = [e for e in mod.parse_entries(bf.read_text(encoding="utf-8"))]
    assert [e.title for e in live] == ["dependency wait"], (
        "the entry after the cleared one became invisible to the parser"
    )


def test_a_second_clear_is_idempotent(program):
    """An already-tombstoned entry is left alone — the verb may be re-run."""
    root, bf = program

    B._cmd_blocked_clear_by_stem(root, CHANGE_REF)
    once = bf.read_text(encoding="utf-8")
    rc = B._cmd_blocked_clear_by_stem(root, CHANGE_REF)

    assert rc == 0
    assert bf.read_text(encoding="utf-8") == once, "a repeat clear rewrote the file"


def test_an_old_core_refuses_with_a_remedy_instead_of_crashing(program, capsys, monkeypatch):
    """`blocked_entries` IS the verb — there is no reduced-but-useful mode.

    So a bundle without the shared parser must say what to do, not raise. Same
    stance as the `--list` and `--clear` paths (blocked_gate's docstring).
    """
    root, bf = program
    before = bf.read_bytes()
    import otaman_cli.blocked_gate as gate

    monkeypatch.setattr(gate, "blocked_entries", lambda: None)

    rc = B._cmd_blocked_clear_by_stem(root, CHANGE_REF)

    assert rc == 1
    assert bf.read_bytes() == before
    out = capsys.readouterr()
    assert "newer otaman-core" in out.out + out.err


def test_the_verb_carries_no_entry_regex_of_its_own():
    """The ninth instance, pinned converted: no section/title/field regex here.

    `tests/test_blocked_list_consumes_core.py` holds the general guard and its debt
    register; this is the specific claim — the clear-by-stem path parses nothing.
    """
    import inspect

    src = inspect.getsource(B._cmd_blocked_clear_by_stem)
    assert "re.compile" not in src, "clear-by-stem grew its own regex again"
    assert "find_by_ref" in src and "tombstone" in src
