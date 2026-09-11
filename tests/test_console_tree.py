"""console-ux-redesign wave 1, task 1.3 — the linked artifact tree (D2/S4).

Outcomes → solutions → changes join into one adaptive tree: outcome-first when
registries are enabled, simplified (flat changes) otherwise; siblings sort by the
inherited outcome priority; changes read BLOCKED naming the blocker; closed items
hide by default with dormant last. The join logic is unit-tested by faking the
underlying readers (the real registry/lifecycle/status readers are tested
elsewhere); pilots cover Home `t` → tree and the closed-toggle.
"""

from __future__ import annotations

import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

from otaman_cli.console import bus, tree

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _row(name, *, state="in-flight", triage="active", next_actor="cli-agent"):
    return SimpleNamespace(
        name=name, state=state, stage="authored", triage=triage, next_actor=next_actor
    )


def _fake_registries(monkeypatch, outcomes, solutions_for):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: True)
    reg_out = SimpleNamespace(outcomes=outcomes)
    reg_sol = SimpleNamespace(for_outcome=lambda oid: solutions_for.get(oid, []))
    monkeypatch.setattr(tree, "_load_registries", lambda program: (reg_out, reg_sol))


def _outcome(oid, *, status="Approved", priority="P1", chosen=None, incr="do the thing"):
    return SimpleNamespace(
        id=oid,
        status=status,
        priority=priority,
        chosen_solution=chosen,
        statement=SimpleNamespace(incremental_outcome=incr),
    )


def _solution(sid, *, status="Considering", desc="a way"):
    return SimpleNamespace(id=sid, status=status, description=desc)


# ---------------------------------------------------------------------------
# pure helpers


def test_extract_outcome_id_from_free_text():
    assert tree._extract_outcome_id("JTBD-117 lineage (interactive surfaces)") == "JTBD-117"
    assert tree._extract_outcome_id("JTBD-99-102 (pack)") == "JTBD-99-102"
    assert tree._extract_outcome_id("no id here") is None
    assert tree._extract_outcome_id(None) is None


# ---------------------------------------------------------------------------
# row-title clipping (Roman feedback): long titles must not overflow the viewport


def test_display_label_clips_long_title():
    node = tree.TreeNode(kind="outcome", id="JTBD-1", title="x" * 200, status="Approved")
    dl = node.display_label(max_title=48)
    assert "JTBD-1" in dl  # id kept intact
    assert "approved" in dl and "[Approved]" not in dl  # short lowercase status, no enum/brackets
    assert "…" in dl  # clipped
    assert len(dl) < len(node.label)


def test_display_label_short_title_unchanged():
    node = tree.TreeNode(kind="outcome", id="JTBD-2", title="short", status="Done")
    assert node.display_label(max_title=48) == node.label
    assert "…" not in node.display_label()


def test_display_label_no_title_is_label():
    node = tree.TreeNode(kind="change", id="my-change", title="")
    assert node.display_label() == node.label


# ---------------------------------------------------------------------------
# loud fallback notice — registries enabled but the file won't load (Roman req)


def test_fallback_notice_when_enabled_but_load_fails(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: True)
    monkeypatch.setattr(tree, "_load_registries", lambda program: (None, None))
    assert tree.tree_fallback_notice(program) == tree.FALLBACK_NOTICE


def test_no_notice_when_registries_loaded(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: True)
    reg = SimpleNamespace(outcomes=[])
    monkeypatch.setattr(tree, "_load_registries", lambda program: (reg, None))
    assert tree.tree_fallback_notice(program) is None


def test_no_notice_when_registries_disabled(program, monkeypatch):
    # genuine absence (process off / no file) is not a failure → no scary banner
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    assert tree.tree_fallback_notice(program) is None


@_textual
def test_tree_shows_loud_banner_on_load_failure(program, monkeypatch):
    # registries enabled but load fails → flat tree AND a visible red notice.
    monkeypatch.setattr(tree, "registries_enabled", lambda program: True)
    monkeypatch.setattr(tree, "_load_registries", lambda program: (None, None))
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("alpha")])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    from textual.widgets import Static as _Static

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            notice = app.screen.query_one("#tree-notice", _Static)
            assert notice.display is True
            assert "failed to load" in str(notice.render())
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_enter_on_outcome_opens_registry_detail(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    from otaman_cli.console.app import OtamanConsole, RegistryDetailScreen, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            node = tree.TreeNode(kind="outcome", id="JTBD-9", title="a long title" * 10)
            ev = SimpleNamespace(node=SimpleNamespace(data=node))
            app.screen.on_tree_node_selected(ev)
            await pilot.pause()
            assert isinstance(app.screen, RegistryDetailScreen)
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# simplified mode (registries absent)


def test_simplified_flat_changes_when_no_registries(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("alpha"), _row("beta")])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    roots = tree.build_artifact_tree(program)
    assert [n.id for n in roots] == ["alpha", "beta"]
    assert all(n.kind == "change" for n in roots)  # no outcome scaffolding


def test_absorbed_hidden_by_default_dormant_last(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(
        tree,
        "_change_rows",
        lambda program: [_row("a"), _row("gone", triage="absorbed"), _row("z", triage="dormant")],
    )
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    ids = [n.id for n in tree.build_artifact_tree(program)]
    assert ids == ["a", "z"]  # absorbed hidden, dormant sorts last
    ids_all = [n.id for n in tree.build_artifact_tree(program, show_closed=True)]
    assert "gone" in ids_all  # show_closed re-includes it


# ---------------------------------------------------------------------------
# outcome-first linked mode


def test_outcome_first_links_and_priority_sort(program, monkeypatch):
    _fake_registries(
        monkeypatch,
        outcomes=[
            _outcome("JTBD-1", priority="P1", chosen="SOL-1"),
            _outcome("JTBD-2", priority="P0"),
        ],
        solutions_for={"JTBD-1": [_solution("SOL-1"), _solution("SOL-9", status="Discarded")]},
    )
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("impl-1"), _row("orphan-x")])
    monkeypatch.setattr(
        tree,
        "_change_outcome_id",
        lambda program, name: "JTBD-1" if name == "impl-1" else None,
    )
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})

    roots = tree.build_artifact_tree(program)
    # P0 outcome sorts before P1; unlinked group is last
    assert [r.id for r in roots] == ["JTBD-2", "JTBD-1", "(unlinked changes)"]
    jtbd1 = next(r for r in roots if r.id == "JTBD-1")
    kinds = {c.kind for c in jtbd1.children}
    assert "solution" in kinds and "change" in kinds
    chosen = next(c for c in jtbd1.children if c.kind == "solution" and c.id == "SOL-1")
    assert chosen.marker == "★"  # chosen solution flagged
    assert not any(c.id == "SOL-9" for c in jtbd1.children)  # Discarded hidden by default
    assert any(c.id == "impl-1" for c in jtbd1.children)  # change linked under its outcome
    orphan = next(r for r in roots if r.kind == "group")
    assert [c.id for c in orphan.children] == ["orphan-x"]


# ---------------------------------------------------------------------------
# tree-view-polish — short colored forms, ruled column order, chosen graying


class _E:
    """A str-enum-like value whose ``str()`` leaks a repr (like the real enums)."""

    def __init__(self, name, value):
        self.value = value
        self._name = name

    def __str__(self):
        return f"{self._name}.{self.value.upper()}"


def test_rows_use_short_lowercase_forms_no_enum_reprs():
    node = tree.TreeNode(
        kind="outcome",
        id="JTBD-1",
        title="sign in",
        status="Approved",
        priority="P1",
        created="2026-09-01T10:00:00+00:00",
    )
    dl = node.display_label()
    # ruled order: created | p1 | approved | title
    assert dl.index("2026-09-01") < dl.index("p1") < dl.index("approved") < dl.index("sign in")
    assert "P1" not in dl and "Approved" not in dl and "OutcomeStatus" not in dl


def test_solution_row_has_no_created_column():
    node = tree.TreeNode(
        kind="solution",
        id="SOL-1",
        title="a way",
        status="Considering",
        priority="P2",
        created="2026-09-01",  # ignored: solution rows are p<N> | status | title
    )
    texts = [t for t, _ in node.row_segments()]
    assert "considering" in "".join(texts) and "p2" in texts
    assert "2026-09-01" not in "".join(texts)


def test_priority_and_status_carry_palette_colors():
    from otaman_cli.console import palette

    node = tree.TreeNode(kind="outcome", id="JTBD-1", title="x", status="Approved", priority="P0")
    styles = {t: s for t, s in node.row_segments()}
    assert styles["p0"] == palette.PRIORITY_STYLE["P0"]
    assert styles["approved"] == palette.STATUS_STYLE["Approved"]


def test_build_enum_status_renders_short(program, monkeypatch):
    _fake_registries(
        monkeypatch,
        outcomes=[
            _outcome(
                "JTBD-1", status=_E("OutcomeStatus", "Approved"), priority=_E("Priority", "P1")
            )
        ],
        solutions_for={},
    )
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: None)
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    (node,) = tree.build_artifact_tree(program)
    assert node.status == "Approved" and node.priority == "P1"  # values, not reprs
    assert "OutcomeStatus" not in node.label and "p1" in node.label


def test_chosen_solution_grays_and_closes_siblings(program, monkeypatch):
    _fake_registries(
        monkeypatch,
        outcomes=[_outcome("JTBD-1", chosen="SOL-1")],
        solutions_for={
            "JTBD-1": [
                _solution("SOL-1"),
                _solution("SOL-2"),
                _solution("SOL-3"),
            ]
        },
    )
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: None)
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})

    # hide-closed (default): only the chosen solution remains
    (j1,) = tree.build_artifact_tree(program)
    sols = [c for c in j1.children if c.kind == "solution"]
    assert [s.id for s in sols] == ["SOL-1"]

    # show-closed: siblings reappear, grayed
    (j1b,) = tree.build_artifact_tree(program, show_closed=True)
    by_id = {c.id: c for c in j1b.children if c.kind == "solution"}
    assert by_id["SOL-1"].grayed is False
    assert by_id["SOL-2"].grayed is True and by_id["SOL-3"].grayed is True
    # grayed rows render wholly in the gray style
    from otaman_cli.console import palette

    assert all(style == palette.GRAY_STYLE for _, style in by_id["SOL-2"].row_segments() if style)


def test_no_graying_without_a_decided_solution(program, monkeypatch):
    _fake_registries(
        monkeypatch,
        outcomes=[_outcome("JTBD-1", chosen=None)],
        solutions_for={"JTBD-1": [_solution("SOL-1"), _solution("SOL-2")]},
    )
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: None)
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    (j1,) = tree.build_artifact_tree(program)
    assert all(not c.grayed for c in j1.children if c.kind == "solution")


def test_blocked_by_named_on_change(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("blocked-change")])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {"blocked-change": "core-agent"})
    (node,) = tree.build_artifact_tree(program)
    assert node.blocked_by == "core-agent"
    assert "BLOCKED by core-agent" in node.label


def test_done_outcome_hidden_by_default(program, monkeypatch):
    _fake_registries(monkeypatch, outcomes=[_outcome("JTBD-7", status="Done")], solutions_for={})
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: None)
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    assert tree.build_artifact_tree(program) == []
    assert [r.id for r in tree.build_artifact_tree(program, show_closed=True)] == ["JTBD-7"]


# ---------------------------------------------------------------------------
# pilots


@_textual
def test_home_t_opens_tree(program):
    from otaman_cli.console.app import HomeScreen, OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action("tree")
            await pilot.pause()
            assert isinstance(app.screen, TreeScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_tree_populates_and_closed_toggle(program, monkeypatch):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("alpha")])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    from textual.widgets import Tree as _TreeWidget

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            w = app.screen.query_one("#artifact-tree", _TreeWidget)
            assert len(w.root.children) == 1  # the one change rendered
            await app.screen.run_action("toggle_closed")  # must not crash
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, TreeScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_p_toggles_read_in_place_panel(program, monkeypatch):
    # p opens a right-side panel with the browsed node's artifact content (1.4)
    _fake_registries(
        monkeypatch,
        outcomes=[_outcome("JTBD-1", incr="sign in")],
        solutions_for={"JTBD-1": [_solution("SOL-1", desc="the login form")]},
    )
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: None)
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    from textual.widgets import Static as _Static

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            side = app.screen.query_one("#tree-side")
            assert side.display is False  # hidden by default
            await app.screen.run_action("toggle_panel")  # p opens the panel
            await pilot.pause()
            assert app.screen.query_one("#tree-side").display is True
            # browsing (cursor move) updates the open panel with that node's content
            node = tree.TreeNode(kind="change", id="impl-1", title="", status="in-flight")
            app.screen.on_tree_node_highlighted(SimpleNamespace(node=SimpleNamespace(data=node)))
            await pilot.pause()
            body = app.screen.query_one("#tree-side-body", _Static)
            assert "impl-1" in str(body.render()) and "in-flight" in str(body.render())
            await app.screen.run_action("toggle_panel")  # p again closes
            await pilot.pause()
            assert app.screen.query_one("#tree-side").display is False
            await app.action_quit()

    asyncio.run(go())
