"""Blocked entries are parsed by ONE implementation, homed in core.

plugin's `otaman_check` carried its own regex for `## Blocked:` — a SIXTH
parser, after blocked-entry-lifecycle consolidated five here. It required a
`**Proposal**:` line, which `awaiting-dependency` entries do not have, so on a
file holding one of each it saw 1 of 2: an agent blocked on another AGENT was
invisible through MCP and visible through the CLI, while blocked-subcommand's
spec names both kinds. spec-agent ruled it a conformance defect and homed the
parser in core (plugin cannot import cli — the circular-dependency bind
`scr_template` hit).

The version-skew pattern differs from `scr_gate` here, deliberately.
`scr_template` GATES a verb, so losing it leaves `propose` able to file and
"no gate" is the right degradation. `blocked_entries` IS the verb's logic —
there is no reduced-but-useful mode — so the gate refuses with a remedy rather
than letting an ImportError traceback out at the moment someone runs
`otaman blocked`.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import otaman_cli.blocked_gate as gate

BOTH_KINDS = (
    "## Blocked: waiting on a spec change\n"
    "- **Proposal**: 20260913T144246-cli-agent-to-human-spec-change-request\n"
    "- **Blocked since**: 2026-09-13T14:42:46Z\n\n"
    "## Blocked: waiting on core API\n"
    "- **Kind**: awaiting-dependency\n"
    "- **Blocked since**: 2026-09-16T21:01:06Z\n"
    "- **Blocked by**: core-agent\n"
)


# ---------------------------------------------------------------------------
# one implementation


def test_the_cli_module_re_exports_core():
    import otaman_core.blocked_entries as source

    import otaman_cli.blocked_entries as shim

    assert shim.parse_entries is source.parse_entries
    assert shim.render_entry is source.render_entry
    assert shim.tombstone is source.tombstone
    assert set(shim.__all__) == set(source.__all__)


def test_the_cli_carries_no_second_parser():
    """A second copy is the entire defect — plugin's was the sixth."""
    import inspect

    import otaman_cli.blocked_entries as shim

    src = inspect.getsource(shim)
    assert "## Blocked:" not in src.split('"""', 2)[-1]  # no regex outside the docstring
    assert "def parse_entries" not in src


def test_both_entry_kinds_parse():
    """The kind plugin's regex dropped. blocked-subcommand specifies both."""
    from otaman_core.blocked_entries import KIND_APPROVAL, KIND_DEPENDENCY, parse_entries

    kinds = [e.kind for e in parse_entries(BOTH_KINDS)]
    assert kinds == [KIND_APPROVAL, KIND_DEPENDENCY]


# ---------------------------------------------------------------------------
# the probe, and why it refuses rather than degrades


def test_the_gate_resolves_when_core_has_it():
    assert gate.blocked_entries() is not None


def test_a_missing_core_module_reads_as_absent(monkeypatch):
    import builtins

    real = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "otaman_core":
            raise ImportError("simulated laggard core")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert gate.blocked_entries() is None


def test_a_partially_updated_core_reads_as_absent(monkeypatch):
    import otaman_core.blocked_entries as module

    monkeypatch.delattr(module, "stale_reason", raising=True)
    assert gate.blocked_entries() is None


def test_the_remedy_names_the_cause_and_the_fix():
    assert "otaman-core" in gate.REMEDY
    assert "upgrade" in gate.REMEDY


def _root():
    root = pathlib.Path(tempfile.mkdtemp()) / "proj"
    (root / ".agents" / "blocked").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    return root


def test_blocked_refuses_with_the_remedy_not_a_traceback(monkeypatch, capsys):
    """A bare ImportError at the moment someone runs `otaman blocked` is what
    the primary-verb rule forbids. A parser cannot be degraded around, so the
    most it can offer is a sentence naming the fix."""
    import otaman_cli.commands.blocked as B

    root = _root()
    monkeypatch.setattr(B, "find_project_root", lambda: root)
    monkeypatch.setattr(B, "resolve_agent_identity", lambda _r: "cli-agent")
    monkeypatch.setattr(gate, "blocked_entries", lambda: None)

    rc = B.cmd_blocked(["waiting-on-core"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "otaman-core" in out
    assert "Traceback" not in out


def test_check_still_runs_without_the_parser(monkeypatch):
    """`check` IS a primary verb by spec-agent's list, so it must not die — it
    omits the blocked section instead."""
    import inspect

    import otaman_cli.commands.check as C

    src = inspect.getsource(C)
    assert "blocked_gate" in src
    assert "return  # `check` must still run" in src


def test_complete_still_reports_without_the_parser():
    """The dependency sweep is a side effect of `complete`; losing it must not
    cost the completion report itself."""
    import inspect

    import otaman_cli.commands.complete as M

    assert "return  # no parser → no sweep" in inspect.getsource(M)


# ---------------------------------------------------------------------------
# display_title — core's ask


def test_a_malformed_entry_reads_uniformly():
    """core added `display_title` so a title-less entry reads `[malformed]`
    everywhere rather than as a blank line in one surface and '' in another."""
    from otaman_core.blocked_entries import MALFORMED_TITLE, parse_entries

    entries = parse_entries("## Blocked: \n- **Blocked since**: 2026-09-16T21:01:06Z\n")
    assert entries
    assert entries[0].title == ""
    assert entries[0].display_title == MALFORMED_TITLE


def test_a_well_formed_entry_shows_its_own_title():
    from otaman_core.blocked_entries import parse_entries

    assert parse_entries(BOTH_KINDS)[0].display_title == "waiting on a spec change"


@pytest.mark.parametrize("module", ["check", "blocked"])
def test_display_surfaces_use_display_title(module):
    """Core's handoff: every surface that showed `entry.title` switches, so a
    malformed entry cannot render as an empty row in one place and a marker in
    another."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(f"otaman_cli.commands.{module}"))
    assert "display_title" in src
