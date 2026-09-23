"""slsh conformance — `otaman blocked --list` consumes core's parser.

spec-agent's gate 2.1 caught this on v0.5.13 as the SEVENTH surface-local entry
parser (20260923T023125). `list_mode` carried an inline regex that predated the
consolidation and was missed while the migrate/sweep paths in the same file were
converted.

It was not merely a duplicate — it LOST rows. The pattern required
`## Blocked: <space><title>`, so a malformed entry (empty or space-less title)
never matched and simply did not appear: a three-entry fixture printed two lines
with no indication a third existed. Core, the console and MCP all show
`[malformed]` for it. One read surface silently disagreeing with the others
about what is in the file is the failure the single-home rule exists to prevent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("otaman_core.blocked_entries")

from otaman_cli.commands.blocked import cmd_blocked  # noqa: E402

#: spec-agent's fixture: both kinds plus the empty-title entry that vanished.
FIXTURE = """# Blocked — cli-agent

## Blocked: change-alpha
- **Blocked since**: 2026-05-28T11:36:53Z
- **Kind**: dependency

## Blocked: change-beta
- **Blocked since**: 2026-05-29T09:00:00Z
- **Proposal**: 20260521T101010-cli-agent-to-human-spec-change-request
- **Kind**: approval

## Blocked:
- **Blocked since**: 2026-05-30T09:00:00Z
"""


@pytest.fixture
def blocked_file(isolate_bus, monkeypatch):
    root = isolate_bus
    directory = root / ".agents" / "blocked"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    from otaman_cli.identity import resolve_agent_identity

    agent = resolve_agent_identity(root) or "test-agent"
    path = directory / f"{agent}.md"
    path.write_text(FIXTURE, encoding="utf-8")
    return path


def test_the_malformed_entry_is_visible_not_dropped(blocked_file, capsys):
    """The reported defect: v0.5.13 printed 2 rows for a 3-entry fixture."""
    assert cmd_blocked(["--list"]) == 0
    out = capsys.readouterr().out
    assert "change-alpha" in out
    assert "change-beta" in out
    assert "[malformed]" in out, "the malformed entry is silently absent again"


def test_all_three_entries_are_listed(blocked_file, capsys):
    cmd_blocked(["--list"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln and not ln.startswith(" ")]
    assert len(lines) == 3, f"expected 3 rows, got {len(lines)}: {lines}"


def test_the_cli_agrees_with_cores_own_reading(blocked_file, capsys):
    """The point of the rule: one file, one answer, whichever surface asks."""
    from otaman_core.blocked_entries import parse_entries

    expected = [e.display_title for e in parse_entries(blocked_file.read_text(encoding="utf-8"))]
    cmd_blocked(["--list"])
    out = capsys.readouterr().out
    for title in expected:
        assert title in out, f"core lists {title!r}; the CLI does not"


def test_since_and_proposal_still_render(blocked_file, capsys):
    cmd_blocked(["--list"])
    out = capsys.readouterr().out
    assert "2026-05-28T11:36:53Z" in out
    assert "20260521T101010-cli-agent-to-human-spec-change-request" in out


def test_tombstoned_entries_stay_hidden(isolate_bus, monkeypatch, capsys):
    """Previously excluded only by ACCIDENT of the `^` anchor against wrapped
    lines — now by core's own `include_tombstoned` default."""
    root = isolate_bus
    directory = root / ".agents" / "blocked"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    from otaman_cli.identity import resolve_agent_identity

    agent = resolve_agent_identity(root) or "test-agent"
    (directory / f"{agent}.md").write_text(
        FIXTURE + "\n<!-- ## Blocked: gone\n- **Blocked since**: 2026-05-01T00:00:00Z\n"
        "cleared 2026-06-01 — resolved -->\n",
        encoding="utf-8",
    )
    cmd_blocked(["--list"])
    assert "gone" not in capsys.readouterr().out


#: `## Blocked:` patterns that REMAIN in blocked.py, each with why it is still
#: here. This is a debt register, not an exemption: a NEW one fails the test.
#:
#: They are all in the two --clear paths, which do destructive REWRITES of the
#: blocked file rather than reads. Converting them is the right end state — core
#: owns `tombstone()` and the sweep path already uses it — but it is not this
#: change: core shipped a tombstone data-loss bug ten days ago (plugin-agent,
#: core #66), so rewriting a destructive path blind, in the PR that fixes a
#: read path, trades a visible bug for a risk of an invisible one.
#:
#: Reported to spec-agent as found-and-not-fixed rather than left silent.
_KNOWN_REMAINING = {
    172: "containment check before writing a new entry (write path, not a parse)",
    196: "docstring prose describing the old matching rule — not code",
    444: "docstring prose describing the sweep's own pattern — not code",
    218: "clear: locate the exact-titled section to remove",
    229: "clear: enumerate titles for the partial-match fallback — a real parse, 8th instance",
    255: "clear: remove the matched section",
    467: "clear-by-stem: split the file into sections for rewriting",
    474: "clear-by-stem: read a section's title while rewriting",
}


def test_the_list_path_carries_no_surface_local_entry_regex():
    """The generalized guard spec-agent asked for.

    A check for `## Blocked:` parsing outside core would have caught the seventh
    instance and will catch the ninth. The known remaining sites are registered
    above with reasons; anything NEW fails here.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "commands" / "blocked.py"
    ).read_text(encoding="utf-8")

    found = {
        lineno
        for lineno, line in enumerate(source.splitlines(), 1)
        if "## Blocked:" in line and not line.strip().startswith("#")
    }
    unregistered = sorted(found - set(_KNOWN_REMAINING))
    assert not unregistered, (
        "new surface-local blocked-entry parsing at line(s) "
        f"{unregistered} — consume otaman_core.blocked_entries instead"
    )


def test_the_debt_register_has_no_stale_entries():
    """A registered line that no longer matches is stale — drop it, or the
    register quietly widens the guard's blind spot."""
    from pathlib import Path

    lines = (
        (Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "commands" / "blocked.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    stale = [
        lineno
        for lineno in _KNOWN_REMAINING
        if lineno > len(lines) or "## Blocked:" not in lines[lineno - 1]
    ]
    assert not stale, f"stale debt-register entries: {stale}"
