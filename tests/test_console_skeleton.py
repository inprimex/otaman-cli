"""interactive-human-console 1.1 — console skeleton (picker + pending list).

Data-layer tests (program discovery, pending proposals) run without Textual;
the app tests drive Textual's `App.run_test()` pilot via asyncio.run (no
pytest-asyncio needed) and skip cleanly if the `console` extra is absent.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from otaman_cli.console import bus
from otaman_cli.console.launch import run_console

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None


@pytest.fixture
def ws(tmp_path):
    """A dedicated program search root, isolated from the autouse isolate_bus
    sandbox that also plants a platform.yaml elsewhere under tmp_path."""
    d = tmp_path / "ws"
    d.mkdir()
    return d


def _make_program(root: Path, name: str) -> Path:
    # Full program shape (project+version+repos) + a bus — what canonical
    # discovery requires (5.1 picker finding).
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text(
        f"project: {name}\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return root


def _stage_proposal(
    program_root: Path, stem: str, *, subject="add widget", acked=False, kind="spec-change-request"
):
    active = program_root / ".agents" / "bus" / "active"
    (active / f"{stem}.md").write_text(
        f"---\nfrom: core-agent\nto: human\npriority: high\ntype: {kind}\n"
        f"timestamp: 2026-01-01T00:00:00Z\nstatus: pending\n---\n\n"
        f"## Subject: Spec change request: {subject}\n\nbody\n",
        encoding="utf-8",
    )
    if acked:
        (active / "acks" / f"{stem}.human.ack").write_text("approved\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# bus.py — discovery + pending detection (Textual-free)


def test_discover_programs_finds_and_sorts(ws):
    _make_program(ws / "beta", "beta")
    _make_program(ws / "alpha", "alpha")
    (ws / "not-a-program").mkdir()  # no platform.yaml → ignored
    progs = bus.discover_programs(ws)
    assert [p.name for p in progs] == ["alpha", "beta"]


def test_discover_skips_heavy_dirs(ws):
    _make_program(ws / "app", "app")
    # a platform.yaml buried under node_modules must NOT be discovered
    nm = ws / "app" / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "platform.yaml").write_text("project: junk\n", encoding="utf-8")
    progs = bus.discover_programs(ws)
    assert [p.name for p in progs] == ["app"]


def _full_program(root: Path, name: str, *, bus: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if bus:
        (root / ".agents").mkdir(exist_ok=True)
    (root / "platform.yaml").write_text(
        f"project: {name}\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return root


def test_discover_requires_full_shape_and_bus(ws):
    # 5.1 finding #1: canonical discovery, not "trust any platform.yaml".
    _full_program(ws / "real", "real")  # full shape + bus → discovered
    # project-only (org-level shape) → excluded
    org = ws / "org"
    (org / ".agents").mkdir(parents=True)
    (org / "platform.yaml").write_text("project: org\nmodels: {}\n", encoding="utf-8")
    # full shape but NO bus (repo-local platform.yaml) → excluded
    _full_program(ws / "repo-local", "repo-local", bus=False)
    assert [p.name for p in bus.discover_programs(ws)] == ["real"]


def test_discover_skips_fixture_and_launcher_dirs(ws):
    _full_program(ws / "real", "real")
    _full_program(ws / "plugin" / "examples" / "example-platform", "example-platform")
    _full_program(ws / "real" / "launcher", "otaman-dev")  # nested copy under launcher/
    assert [p.name for p in bus.discover_programs(ws)] == ["real"]


def test_discover_excludes_nested_and_dedupes_by_identity(ws):
    _full_program(ws / "prog", "otaman-dev")  # the real one (shallowest)
    _full_program(ws / "prog" / "sub" / "copy", "otaman-dev")  # nested copy, same identity
    _full_program(ws / "elsewhere" / "dupe", "otaman-dev")  # same identity, separate tree
    progs = bus.discover_programs(ws)
    assert [p.name for p in progs] == ["otaman-dev"]  # exactly one 'otaman-dev'
    assert progs[0].root == (ws / "prog").resolve()  # the shallowest/canonical root won


def _canonical_program(base: Path, org: str, program: str, project: str) -> Path:
    """Build base/orgs/<org>/programs/<program>/<program>-meta with full shape."""
    meta = base / "orgs" / org / "programs" / program / f"{program}-meta"
    _full_program(meta, project)
    return meta


def test_discover_finds_canonical_layout_from_home_base(ws):
    # 5.1 finding #3: the meta sits 5 levels below the base ($HOME), past the
    # bounded walk's max_depth — canonical enumeration must still find it.
    meta = _canonical_program(ws, "otaman-dev", "otaman-dev", "otaman-dev")
    progs = bus.discover_programs(ws, max_depth=4)
    assert [p.name for p in progs] == ["otaman-dev"]
    assert progs[0].root == meta.resolve()


def test_discover_canonical_is_cross_program_from_inside_a_program(ws):
    # Launched from inside one program, the picker still enumerates every
    # program under the shared base (the picker's purpose is cross-program).
    _canonical_program(ws, "otaman-dev", "alpha", "alpha")
    _canonical_program(ws, "otaman-dev", "beta", "beta")
    inside = ws / "orgs" / "otaman-dev" / "programs" / "alpha" / "alpha-meta"
    progs = bus.discover_programs(inside)
    assert [p.name for p in progs] == ["alpha", "beta"]


def test_discover_unions_cwd_marker_chain(tmp_path, monkeypatch):
    # A one-off program outside any orgs/ base: the walk + canonical layout
    # find nothing, but the cwd marker chain resolves it (finding #3). The
    # core marker resolver refuses tmp markers pointing outside $HOME, so we
    # stub it to return the meta the chain would resolve — this exercises the
    # union + the full-shape gate, which is the console-side logic under test.
    meta = tmp_path / "meta"
    _full_program(meta, "oneoff")
    empty = tmp_path / "empty"
    empty.mkdir()

    import otaman_cli.identity as _identity

    monkeypatch.setattr(_identity, "find_project_root", lambda start=None: meta)
    # Without cwd the marker chain is never consulted (pure filesystem read).
    assert bus.discover_programs(empty) == []
    # With cwd the resolved program surfaces.
    progs = bus.discover_programs(empty, cwd=tmp_path / "repo")
    assert [p.name for p in progs] == ["oneoff"]
    assert progs[0].root == meta.resolve()


def test_discover_cwd_union_survives_marker_resolution_error(tmp_path, monkeypatch):
    # A broken/unsafe marker makes find_project_root raise — the picker must
    # not crash; it just yields no cwd-union candidate.
    import otaman_cli.identity as _identity

    def _boom(start=None):
        raise RuntimeError("unsafe marker")

    monkeypatch.setattr(_identity, "find_project_root", _boom)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert bus.discover_programs(empty, cwd=tmp_path / "repo") == []


def test_list_pending_proposals(ws):
    prog_root = _make_program(ws / "p", "p")
    _stage_proposal(prog_root, "20260101T000000-a-to-human-spec-change-request", subject="one")
    _stage_proposal(
        prog_root, "20260102T000000-b-to-human-spec-change-request", subject="two", acked=True
    )
    _stage_proposal(prog_root, "20260103T000000-c-to-human-info", subject="three", kind="info")
    program = bus.discover_programs(ws)[0]
    pending = bus.list_pending_proposals(program)
    # only the un-acked spec-change-request
    assert [p.subject for p in pending] == ["Spec change request: one"]
    assert pending[0].from_agent == "core-agent" and pending[0].priority == "high"


def test_list_pending_empty_when_no_bus(tmp_path):
    program = bus.Program(name="x", root=tmp_path / "nope")
    assert bus.list_pending_proposals(program) == []


def test_scan_prefilter_is_frontmatter_only(ws):
    # 5.1 finding #5: the fast-path substring prefilter reads the FRONTMATTER
    # head only, so an info message that merely mentions "spec-change-request"
    # in its BODY must not be mistaken for a pending proposal.
    prog_root = _make_program(ws / "p", "p")
    active = prog_root / ".agents" / "bus" / "active"
    (active / "20260101T000000-a-to-human-info.md").write_text(
        "---\nfrom: a\nto: human\npriority: normal\ntype: info\n"
        "timestamp: t\nstatus: pending\n---\n\n"
        "## Subject: talking about spec-change-request handling\n\n"
        "the body mentions spec-change-request several times\n",
        encoding="utf-8",
    )
    _stage_proposal(prog_root, "20260102T000000-b-to-human-spec-change-request", subject="real")
    program = bus.discover_programs(ws)[0]
    pending = bus.list_pending_proposals(program)
    assert [p.subject for p in pending] == ["Spec change request: real"]


def test_frontmatter_head_is_bounded(tmp_path):
    # A huge body must not defeat frontmatter extraction (bounded head read).
    f = tmp_path / "m.md"
    f.write_text("---\ntype: info\nfrom: a\n---\n\n" + ("x" * 200_000), encoding="utf-8")
    fm = bus._frontmatter_head(f)
    assert fm is not None and "type: info" in fm
    assert bus._frontmatter_head(tmp_path / "absent.md") is None


# ---------------------------------------------------------------------------
# launch.py — the extra gate + context resolution


def test_run_console_prints_install_hint_when_textual_absent(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "textual", None)  # `import textual` → ImportError
    rc = run_console([])
    assert rc == 2
    assert "console' extra" in capsys.readouterr().out


def test_run_console_builds_without_running(ws):
    pytest.importorskip("textual")
    _make_program(ws / "p", "p")
    # _run=False builds the app + discovers programs but does not enter the loop
    assert run_console(["--path", str(ws)], _run=False) == 0


def test_main_dispatches_i_flag(monkeypatch):
    import otaman_cli.main as m

    called = {}

    def _stub(argv, **kw):
        called["argv"] = argv
        return 0

    monkeypatch.setattr("otaman_cli.console.launch.run_console", _stub)
    monkeypatch.setattr(sys, "argv", ["otaman", "-i", "--path", "/tmp/x"])
    assert m.main() == 0
    assert called["argv"] == ["--path", "/tmp/x"]


# ---------------------------------------------------------------------------
# app.py — Textual pilot (skips if the extra is absent)

pytestmark_textual = pytest.mark.skipif(
    not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)"
)


@pytestmark_textual
def test_picker_mounts_and_lists_programs(ws):
    _make_program(ws / "alpha", "alpha")
    _make_program(ws / "beta", "beta")
    from otaman_cli.console.app import OtamanConsole, ProgramPickerScreen, _ProgramItem

    progs = bus.discover_programs(ws)

    async def go():
        app = OtamanConsole(progs, search_root=ws)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ProgramPickerScreen)
            lv = app.screen.query_one("#program-list")
            items = [c for c in lv.children if isinstance(c, _ProgramItem)]
            assert len(items) == 2
            await app.action_quit()

    asyncio.run(go())


@pytestmark_textual
def test_selecting_program_shows_pending_list(ws):
    prog_root = _make_program(ws / "p", "p")
    _stage_proposal(prog_root, "20260101T000000-a-to-human-spec-change-request", subject="widget")
    from otaman_cli.console.app import OtamanConsole, PendingListScreen, _ProposalItem

    progs = bus.discover_programs(ws)

    async def go():
        app = OtamanConsole(progs, search_root=ws)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(progs[0]))
            await pilot.pause()
            assert isinstance(app.screen, PendingListScreen)
            await app.workers.wait_for_complete()  # paint-then-fill async load
            await pilot.pause()
            lv = app.screen.query_one("#pending-list")
            items = [c for c in lv.children if isinstance(c, _ProposalItem)]
            assert len(items) == 1
            await app.action_quit()

    asyncio.run(go())


@pytestmark_textual
def test_empty_program_shows_no_pending(ws):
    _make_program(ws / "p", "p")
    from otaman_cli.console.app import OtamanConsole, PendingListScreen, _ProposalItem

    progs = bus.discover_programs(ws)

    async def go():
        app = OtamanConsole(progs, search_root=ws)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(progs[0]))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            lv = app.screen.query_one("#pending-list")
            assert not [c for c in lv.children if isinstance(c, _ProposalItem)]
            await app.action_quit()

    asyncio.run(go())
