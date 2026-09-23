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

from pathlib import Path

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


#: `## Blocked:` mentions that REMAIN in blocked.py, keyed by a distinctive
#: fragment of the line and mapped to why it is still there. A debt register,
#: not an exemption: anything NOT listed fails the guard.
#:
#: Keyed on CONTENT rather than line number, deliberately. The first version
#: used line numbers and broke twice in one session — once when the fix above
#: shifted the file, once from editing a docstring. A register that needs
#: re-syncing after every unrelated edit gets re-synced carelessly, and then it
#: is not a register.
_KNOWN_REMAINING = {
    'if f"## Blocked: {slug}" in existing:': (
        "containment check before writing a new entry — a write path, not a parse"
    ),
    "``^## Blocked:`` regex.": "docstring prose describing the sweep's pattern — not code",
    'r"^(## Blocked: .+?)(?=\\n## Blocked: |\\Z)",': (
        "clear-by-stem: splits the file into sections for rewriting — the NINTH "
        "instance, not yet converted"
    ),
    'title_re = re.compile(r"^## Blocked:\\s*(.+)$", re.MULTILINE)': (
        "clear-by-stem: reads a section title while rewriting — same instance"
    ),
}


def _remaining_sites() -> list[str]:
    """Lines in blocked.py mentioning the entry heading, as stripped text."""
    source = (
        Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "commands" / "blocked.py"
    ).read_text(encoding="utf-8")
    return [
        line.strip()
        for line in source.splitlines()
        if "## Blocked:" in line and not line.strip().startswith("#")
    ]


def test_the_list_path_carries_no_surface_local_entry_regex():
    """The generalized guard spec-agent asked for.

    It found the seventh instance's siblings immediately; the eighth (the
    --clear matching path) is now converted. What remains is registered above.
    """
    unregistered = sorted(set(_remaining_sites()) - set(_KNOWN_REMAINING))
    assert not unregistered, (
        "new surface-local blocked-entry parsing:\n  "
        + "\n  ".join(unregistered)
        + "\nconsume otaman_core.blocked_entries instead"
    )


def test_the_debt_register_has_no_stale_entries():
    """A registered line that no longer exists is stale — drop it, or the
    register quietly widens the guard's blind spot."""
    stale = sorted(set(_KNOWN_REMAINING) - set(_remaining_sites()))
    assert not stale, f"stale debt-register entries: {stale}"
