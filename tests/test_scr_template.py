"""The CLI's two SCR doors, wired to the SHARED template.

The template and its refusal now live in `otaman_core.scr_template` (core #63).
They could not stay in otaman-cli: `otaman-cli` DEPENDS ON `otaman-plugin`, so
the plugin's MCP propose path importing cli would have been circular.
plugin-agent proved that with a real wheel install rather than by reading
pyproject — and their pytest pythonpath lists `../otaman-cli/src`, so the
import would have passed their whole suite AND CI before failing on every real
install. Green tests, broken product.

The 26 pure-logic tests moved to core with the module (verified present there
before these were deleted). This file keeps what is genuinely cli's: that BOTH
doors enforce the rule from the same source, and that the deprecated shim still
works for one release.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
from otaman_core.scr_template import SECTIONS, render

FULL = {
    "problem": "564 review-requests land on the human",
    "evidence": "src/otaman_cli/console/inbox.py:45; bus stem 20260916T221457",
    "impact": "87% of the queue; every reader of it",
    "direction": "a write-time recipient rule",
    "scope": "does not change console default filters",
    "routing": "otaman-cli; spec-agent decides",
    "workaround": "n/a because the queue still reads",
}


# ---------------------------------------------------------------------------
# door 1 — `otaman propose`


def _project():
    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "blocked").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    return root


def _wire(monkeypatch, root):
    import otaman_cli.commands.propose_team as PT

    monkeypatch.setattr(PT, "find_project_root", lambda: root)
    monkeypatch.setattr(PT, "resolve_agent_identity", lambda _r: "cli-agent")
    return PT


def _full_argv(title: str) -> list[str]:
    argv = [title]
    for key, value in FULL.items():
        argv += [f"--{key}", value]
    return argv


def test_propose_refuses_a_bare_title(monkeypatch, capsys):
    """The old behaviour filed a body of TODOs. It is now refused."""
    root = _project()
    PT = _wire(monkeypatch, root)
    assert PT.cmd_propose(["a thing"]) == 1
    out = capsys.readouterr().out
    assert "not decision-grade" in out
    assert "--problem" in out  # names how to fix it
    assert not list((root / ".agents" / "bus" / "active").glob("*.md"))


def test_propose_writes_when_every_section_is_filled(monkeypatch):
    root = _project()
    PT = _wire(monkeypatch, root)
    assert PT.cmd_propose(_full_argv("a real request") + ["--evidence-level", "measured"]) == 0
    written = list((root / ".agents" / "bus" / "active").glob("*spec-change-request*.md"))
    assert len(written) == 1
    body = written[0].read_text(encoding="utf-8")
    assert "**Evidence level**: measured" in body
    for section in SECTIONS:
        assert f"### {section.heading}" in body


def test_propose_refuses_an_unknown_evidence_level(monkeypatch, capsys):
    root = _project()
    PT = _wire(monkeypatch, root)
    assert PT.cmd_propose(_full_argv("x") + ["--evidence-level", "super-sure"]) == 1
    assert "unknown evidence level" in capsys.readouterr().out


def test_dash_d_still_fills_the_problem_section(monkeypatch):
    """`-d` predates the sections; what the caller already typed must not be
    dropped on the floor."""
    root = _project()
    PT = _wire(monkeypatch, root)
    argv = ["x", "-d", "the observed problem"]
    for key, value in FULL.items():
        if key != "problem":
            argv += [f"--{key}", value]
    assert PT.cmd_propose(argv) == 0
    body = next((root / ".agents" / "bus" / "active").glob("*spec-change*.md")).read_text()
    assert "the observed problem" in body


# ---------------------------------------------------------------------------
# door 2 — `otaman send --type spec-change-request`


def _send_root():
    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    return root


def _wire_send(monkeypatch, root):
    import otaman_cli.commands.bus_messaging as BM

    active = root / ".agents" / "bus" / "active"
    monkeypatch.setattr(BM, "find_project_root", lambda: root)
    monkeypatch.setattr(BM, "resolve_agent_identity", lambda r, explicit=None: "cli-agent")
    monkeypatch.setattr(BM, "_resolve_bus_paths", lambda r: (active, active / "acks"))
    return BM, active


def test_send_refuses_a_hollow_scr(monkeypatch, capsys):
    """plugin-agent found the identical hole on the MCP side: the refusal lived
    on `propose`, and `send --type spec-change-request` wrote a TODO body
    straight to the bus with exit 0."""
    root = _send_root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        [
            "human",
            "--type",
            "spec-change-request",
            "--subject",
            "hollow",
            "--body",
            "### What needs to change\nTODO: fill later\n",
        ]
    )
    assert rc == 2
    assert "hollow spec-change-request" in capsys.readouterr().out
    assert list(active.glob("*.md")) == []


def test_send_allows_a_legacy_body_with_real_content(monkeypatch):
    """NARROWER than the propose-path rule on purpose: every SCR filed before
    the template uses the old five headings, and refusing those at a shared
    fleet door would break senders over a format change, not over hollowness."""
    root = _send_root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        [
            "human",
            "--type",
            "spec-change-request",
            "--subject",
            "real",
            "--body",
            "### What needs to change\nThe ack verb cannot disambiguate a prefix.\n",
        ]
    )
    assert rc == 0
    assert len(list(active.glob("*.md"))) == 1


def test_send_allows_a_fully_filled_template_body(monkeypatch):
    root = _send_root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        [
            "human",
            "--type",
            "spec-change-request",
            "--subject",
            "filled",
            "--body",
            render("t", sections=FULL),
        ]
    )
    assert rc == 0
    assert len(list(active.glob("*.md"))) == 1


def test_send_refuses_a_template_body_with_unfilled_sections(monkeypatch):
    root = _send_root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        [
            "human",
            "--type",
            "spec-change-request",
            "--subject",
            "partial",
            "--body",
            render("t", sections={"problem": "x"}),
        ]
    )
    assert rc == 2
    assert list(active.glob("*.md")) == []


def test_send_of_other_types_is_untouched(monkeypatch):
    """The rule is about SCRs. An info message may legitimately be terse."""
    root = _send_root()
    BM, active = _wire_send(monkeypatch, root)
    assert BM.cmd_send(["human", "--type", "info", "--subject", "fyi", "--body", "TODO"]) == 0
    assert len(list(active.glob("*.md"))) == 1


# ---------------------------------------------------------------------------
# one source, and the deprecated shim


def test_both_cli_doors_import_from_core():
    """propose and send must enforce the SAME rule from the SAME place."""
    import inspect

    from otaman_cli.commands import bus_messaging, propose_team

    for module in (propose_team, bus_messaging):
        src = inspect.getsource(module)
        assert "from otaman_core.scr_template import" in src, module.__name__
        assert "from otaman_cli.scr_template import" not in src, module.__name__


def test_the_cli_module_re_exports_the_core_one():
    """Kept for ONE release so nothing breaks mid-flight; removal waits for a
    clean usage signal and never lands in the same release as the move (D8)."""
    import otaman_core.scr_template as source

    import otaman_cli.scr_template as shim

    assert shim.render is source.render
    assert shim.validate is source.validate
    assert shim.is_hollow is source.is_hollow
    assert set(shim.__all__) == set(source.__all__)


def test_the_shim_says_it_is_deprecated_and_where_to_go():
    import otaman_cli.scr_template as shim

    doc = shim.__doc__ or ""
    assert "Deprecated" in doc
    assert "otaman_core.scr_template" in doc


def test_the_cli_carries_no_second_implementation():
    """The whole point of 1.1/1.2: ONE source. A second copy is how the five
    blocked-entry parsers happened."""
    import inspect

    import otaman_cli.scr_template as shim

    src = inspect.getsource(shim)
    assert "def render(" not in src
    assert "SECTIONS: tuple" not in src


@pytest.mark.parametrize("name", ["render", "validate", "is_hollow", "completeness_line"])
def test_the_shim_is_the_same_object_not_a_copy(name):
    import otaman_core.scr_template as source

    import otaman_cli.scr_template as shim

    assert getattr(shim, name) is getattr(source, name)
