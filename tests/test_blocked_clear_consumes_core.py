"""Eighth parser — the `--clear` paths consume core (spec-agent GO 20260923T093222).

#196 fixed the SEVENTH surface-local entry parser (the list path). The guard it
shipped immediately found more in the two `--clear` paths, and I registered them
as debt rather than converting them in the same PR: clear REWRITES the file,
which is a different risk class from listing, and core had shipped a tombstone
data-loss bug ten days earlier.

spec-agent GO'd this as its own conformance change and supplied the fixture and
scenarios below. The scenarios are theirs; the failure mode they are aimed at is
a rewrite that eats a neighbour — the tombstoned entry or the malformed one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("otaman_core.blocked_entries")

from otaman_cli.commands.blocked import cmd_blocked  # noqa: E402

#: spec-agent's fixture, verbatim: five entries, four scenarios.
FIXTURE = """## Blocked: alpha waiting on approval
- **Proposal**: 20260901T000001-spec-agent-to-human-spec-change-request
- **Blocked since**: 2026-09-01T00:00:01Z

## Blocked: beta waiting on core task 3.2
- **Blocked since**: 2026-09-01T00:00:02Z
- **Depends on**: core-agent task 3.2

## Blocked: beta waiting on core task 9.9
- **Blocked since**: 2026-09-01T00:00:03Z
- **Depends on**: core-agent task 9.9

<!-- ## Blocked: gamma already cleared
- **Proposal**: 20260901T000004-spec-agent-to-human-spec-change-request
- **Blocked since**: 2026-09-01T00:00:04Z
cleared 2026-09-02 -->

## Blocked:
- **Blocked since**: 2026-09-01T00:00:05Z
"""

#: The tombstoned block, which must survive any clear byte-identical.
GAMMA_BLOCK = """<!-- ## Blocked: gamma already cleared
- **Proposal**: 20260901T000004-spec-agent-to-human-spec-change-request
- **Blocked since**: 2026-09-01T00:00:04Z
cleared 2026-09-02 -->"""


@pytest.fixture
def blocked(isolate_bus, monkeypatch):
    root = isolate_bus
    directory = root / ".agents" / "blocked"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    from otaman_cli.identity import resolve_agent_identity

    agent = resolve_agent_identity(root) or "test-agent"
    path = directory / f"{agent}.md"
    path.write_text(FIXTURE, encoding="utf-8")
    return path


def _titles(path):
    from otaman_core.blocked_entries import parse_entries

    return [e.display_title for e in parse_entries(path.read_text(encoding="utf-8"))]


# ---------------------------------------------------------------------------
# (a) and (b) — the two ways to name one entry


def test_clear_by_exact_title_removes_exactly_that_entry(blocked):
    assert cmd_blocked(["--clear", "alpha waiting on approval"]) == 0
    remaining = _titles(blocked)
    assert "alpha waiting on approval" not in remaining
    assert "beta waiting on core task 3.2" in remaining
    assert "beta waiting on core task 9.9" in remaining


#: The proposal stem in spec-agent's fixture, in full.
ALPHA_STEM = "20260901T000001-spec-agent-to-human-spec-change-request"


def test_clear_by_proposal_stem_removes_the_same_entry(blocked):
    assert cmd_blocked(["--clear", ALPHA_STEM]) == 0
    assert "alpha waiting on approval" not in _titles(blocked)


def test_a_stem_PREFIX_does_not_clear_and_that_is_deliberate(blocked, capsys):
    """A conflict between scenario (b) and core's own rule, surfaced not resolved.

    spec-agent's scenario (b) passes `20260901T000001` — a PREFIX of the stem.
    core's `find_by_ref` is exact by explicit design: "a stable id is exact; a
    substring match over free text is how an unrelated entry gets cleared by
    someone else's completion."

    Implementing prefix matching here would re-create the local matcher this
    change exists to remove AND overrule a documented core decision, so the
    behaviour is exact and the conflict is reported to spec-agent rather than
    decided by me. This test pins TODAY's behaviour; if the ruling goes the
    other way it flips, deliberately and visibly.
    """
    before = blocked.read_text(encoding="utf-8")
    assert cmd_blocked(["--clear", "20260901T000001"]) == 0
    assert blocked.read_text(encoding="utf-8") == before, "a prefix cleared an entry"
    assert "No blocked task found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (c) — ambiguity refuses, clears nothing


def test_an_ambiguous_partial_match_refuses_and_clears_nothing(blocked, capsys):
    before = blocked.read_text(encoding="utf-8")
    rc = cmd_blocked(["--clear", "beta"])
    assert rc != 0, "ambiguity must refuse, not guess"
    out = capsys.readouterr().out
    assert "beta waiting on core task 3.2" in out
    assert "beta waiting on core task 9.9" in out
    assert blocked.read_text(encoding="utf-8") == before, "a refused clear rewrote the file"


# ---------------------------------------------------------------------------
# (d) — the neighbours a rewrite must not eat


@pytest.mark.parametrize("value", ["alpha waiting on approval", ALPHA_STEM])
def test_the_tombstoned_entry_survives_any_clear_byte_identical(blocked, value):
    """The core #66 shape: clearing one entry must not disturb a tombstoned one."""
    assert cmd_blocked(["--clear", value]) == 0
    assert GAMMA_BLOCK in blocked.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["alpha waiting on approval", ALPHA_STEM])
def test_the_malformed_entry_survives_and_stays_visible(blocked, value):
    """It has no title to match on, so a rewrite keyed on titles is exactly
    where it gets swallowed."""
    assert cmd_blocked(["--clear", value]) == 0
    assert "[malformed]" in _titles(blocked)


def test_every_untouched_entry_survives_a_clear(blocked):
    before = set(_titles(blocked))
    cmd_blocked(["--clear", "alpha waiting on approval"])
    after = set(_titles(blocked))
    target = {"alpha waiting on approval"}
    lost = before - after
    assert lost == target, f"a clear removed more than its target: also lost {lost - target}"


# ---------------------------------------------------------------------------
# idempotence — spec-agent's addition


def test_clearing_twice_is_a_stated_no_op(blocked, capsys):
    """Not an error, and not a silent success — the second run says nothing
    matched, which is the truthful answer."""
    assert cmd_blocked(["--clear", "alpha waiting on approval"]) == 0
    capsys.readouterr()
    rc = cmd_blocked(["--clear", "alpha waiting on approval"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No blocked task found" in out, f"second clear said: {out!r}"


def test_clearing_something_absent_says_so(blocked, capsys):
    assert cmd_blocked(["--clear", "never-existed"]) == 0
    assert "No blocked task found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# conformance


def test_the_clear_paths_no_longer_hand_parse_entries():
    """The eighth instance, closed. The debt register in
    test_blocked_list_consumes_core.py shrinks to the write-side helpers."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "commands" / "blocked.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("def _cmd_blocked_clear(") :]
    body = body[: body.index("\ndef _cmd_blocked_migrate")]
    assert "## Blocked:" not in body, (
        "the clear path still hand-parses entries instead of consuming core"
    )
