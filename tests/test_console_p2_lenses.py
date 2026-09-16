"""console-ia-consolidation P2-A — the lens mechanism and one-parent rendering.

3.1 The lifecycle table becomes a LENS of Artifacts; one key cycles
    value → capability → lifecycle; the Roman-defined column set is preserved
    VERBATIM by being shared, not copied; `l` is an alias landing there.
3.3 D4 — a node appears exactly once per lens under its single structural
    parent; every other relation is a REFERENCE line that navigates and never
    expands in place.

Why D4 is forced rather than tasteful (measured in the change): one change
carries deltas for 6 capabilities and one capability was shaped by 9 changes.
Drawing a node under every relation makes collapse state meaningless and every
count a lie.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.tree import (
    LENS_CAPABILITY,
    LENS_LIFECYCLE,
    LENS_VALUE,
    LENSES,
    TreeNode,
    dedupe_one_parent,
    next_lens,
)

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


# ---------------------------------------------------------------------------
# 3.1 — the lens cycle


def test_one_key_cycles_all_three_lenses():
    assert next_lens(LENS_VALUE) == LENS_CAPABILITY
    assert next_lens(LENS_CAPABILITY) == LENS_LIFECYCLE
    assert next_lens(LENS_LIFECYCLE) == LENS_VALUE  # wraps


def test_unknown_lens_falls_back_to_value():
    assert next_lens("nonsense") == LENS_VALUE


def test_the_three_lenses_are_the_specced_set():
    assert LENSES == (LENS_VALUE, LENS_CAPABILITY, LENS_LIFECYCLE)


def test_columns_are_shared_not_copied():
    """ "Preserving the column set verbatim" is structural here: the standalone
    screen and the lens read the SAME tuple, so they cannot drift."""
    from otaman_cli.console.app import LifecycleScreen
    from otaman_cli.console.lifecycle import LIFECYCLE_COLUMNS

    assert LifecycleScreen._COLUMNS is LIFECYCLE_COLUMNS
    assert LIFECYCLE_COLUMNS == (
        "triage",
        "change",
        "stage",
        "state",
        "tasks",
        "days",
        "next actor",
        "last touch",
        "nudged",
    )


def test_row_cells_match_the_column_count():
    from types import SimpleNamespace

    from otaman_cli.console.lifecycle import LIFECYCLE_COLUMNS, lifecycle_row_cells

    row = SimpleNamespace(
        name="c",
        stage="authored",
        state="in-flight",
        tasks_done=1,
        tasks_total=3,
        age="2d",
        next_actor="cli-agent",
        last_touch="2026-09-16",
        last_nudged="",
        triage="active",
        delivery=None,
    )
    assert len(lifecycle_row_cells(row)) == len(LIFECYCLE_COLUMNS)


def test_auto_delivery_is_marked_in_the_change_cell():
    from types import SimpleNamespace

    from otaman_cli.console.lifecycle import lifecycle_row_cells

    row = SimpleNamespace(
        name="c",
        stage="",
        state="s",
        tasks_done=0,
        tasks_total=0,
        age="",
        next_actor="",
        last_touch="",
        last_nudged="",
        triage="active",
        delivery="auto",
    )
    assert "[auto]" in lifecycle_row_cells(row)[1]


# ---------------------------------------------------------------------------
# 3.3 — one parent per node (D4)


def test_a_node_is_never_drawn_twice():
    dup = TreeNode(kind="change", id="shared", title="")
    roots = [
        TreeNode(kind="outcome", id="A", title="", children=[dup]),
        TreeNode(
            kind="outcome",
            id="B",
            title="",
            children=[TreeNode(kind="change", id="shared", title="")],
        ),
    ]
    out = dedupe_one_parent(roots)
    ids = []

    def walk(ns):
        for n in ns:
            ids.append((n.kind, n.id))
            walk(n.children)

    walk(out)
    assert ids.count(("change", "shared")) == 1  # first (structural) parent keeps it


def test_the_first_occurrence_is_the_one_kept():
    roots = [
        TreeNode(
            kind="outcome",
            id="first",
            title="",
            children=[TreeNode(kind="change", id="c", title="keeper")],
        ),
        TreeNode(
            kind="outcome",
            id="second",
            title="",
            children=[TreeNode(kind="change", id="c", title="dropped")],
        ),
    ]
    out = dedupe_one_parent(roots)
    assert out[0].children[0].title == "keeper"
    assert out[1].children == []


def test_references_are_exempt_from_dedupe():
    """A reference is the sanctioned way a second relation appears — it carries
    no children, so it cannot double-count anything."""
    roots = [
        TreeNode(kind="change", id="c", title=""),
        TreeNode(
            kind="outcome",
            id="o",
            title="",
            children=[
                TreeNode(kind="reference", id="c", title="", ref_kind="change", ref_id="c"),
                TreeNode(kind="reference", id="c", title="", ref_kind="change", ref_id="c"),
            ],
        ),
    ]
    out = dedupe_one_parent(roots)
    assert len(out[1].children) == 2  # both references survive


def test_dedupe_is_recursive():
    roots = [
        TreeNode(
            kind="outcome",
            id="o",
            title="",
            children=[
                TreeNode(
                    kind="solution",
                    id="s",
                    title="",
                    children=[
                        TreeNode(kind="change", id="c", title=""),
                        TreeNode(kind="change", id="c", title=""),
                    ],
                )
            ],
        )
    ]
    out = dedupe_one_parent(roots)
    assert len(out[0].children[0].children) == 1


def test_different_kinds_sharing_an_id_both_survive():
    """The key is (kind, id): an outcome and a change may legitimately share a
    name without either being a duplicate."""
    roots = [TreeNode(kind="outcome", id="x", title=""), TreeNode(kind="change", id="x", title="")]
    assert len(dedupe_one_parent(roots)) == 2


def test_empty_input():
    assert dedupe_one_parent([]) == []


def test_the_builder_enforces_the_invariant(program, monkeypatch):
    """Applied at the BUILDER so no caller has to remember it."""
    from otaman_cli.console import tree as tree_mod

    monkeypatch.setattr(tree_mod, "registries_enabled", lambda p: False)
    monkeypatch.setattr(
        tree_mod,
        "_change_rows",
        lambda p: [
            type(
                "R",
                (),
                {"name": "dup", "state": "s", "stage": "", "triage": "active", "next_actor": ""},
            )(),
            type(
                "R",
                (),
                {"name": "dup", "state": "s", "stage": "", "triage": "active", "next_actor": ""},
            )(),
        ],
    )
    monkeypatch.setattr(tree_mod, "_blocked_map", lambda p: {})
    roots = tree_mod.build_artifact_tree(program)
    assert [r.id for r in roots] == ["dup"]  # the second is dropped


# ---------------------------------------------------------------------------
# 3.3 — references render as pointers and never expand


def test_a_reference_renders_as_a_pointer_line():
    ref = TreeNode(
        kind="reference", id="some-change", title="", ref_kind="change", ref_id="some-change"
    )
    text = ref.display_label()
    assert text.startswith("→")
    assert "some-change" in text


def test_a_reference_carries_its_target():
    ref = TreeNode(kind="reference", id="c", title="", ref_kind="change", ref_id="c")
    assert (ref.ref_kind, ref.ref_id) == ("change", "c")


@_textual
def test_a_reference_is_added_as_a_leaf_even_with_children(program):
    """Belt and braces: the renderer refuses to expand a reference, so a
    malformed reference carrying children still cannot grow a second subtree."""
    from textual.widgets import Tree as _T

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    ref = TreeNode(
        kind="reference",
        id="r",
        title="",
        ref_kind="change",
        ref_id="r",
        children=[TreeNode(kind="change", id="should-not-appear", title="")],
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = app.screen.query_one("#artifact-tree", _T)
            before = len(list(tree.root.children))
            app.screen._add(tree.root, ref, 0)
            await pilot.pause()
            added = list(tree.root.children)[before:]
            assert len(added) == 1
            assert not added[0].children  # no expansion
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_selecting_a_reference_navigates_to_its_target(program, monkeypatch):
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    seen: list = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(
                type(app.screen), "_navigate_to", lambda self, k, i: seen.append((k, i))
            )
            from types import SimpleNamespace

            node = TreeNode(kind="reference", id="c", title="", ref_kind="change", ref_id="c")
            app.screen.on_tree_node_selected(SimpleNamespace(node=SimpleNamespace(data=node)))
            await pilot.pause()
            assert seen == [("change", "c")]
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_navigate_to_is_graceful_for_an_unknown_kind(program):
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            type(app).notify = lambda self, msg, **kw: notes.append(str(msg))
            app.screen._navigate_to("capability", "some-cap")
            await pilot.pause()
            assert notes and "no detail view yet" in notes[0]
            app.screen._navigate_to("", "")  # must not raise
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# 3.1 — the screen


@_textual
def test_lens_key_is_bound(program):
    from otaman_cli.console.app import TreeScreen

    assert "cycle_lens" in {b.action for b in TreeScreen.BINDINGS}


@_textual
def test_cycling_swaps_tree_for_table_and_back(program):
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            s = app.screen
            assert s._lens == LENS_VALUE
            assert s.query_one("#artifact-lifecycle", DataTable).display is False
            assert s.query_one("#tree-row").display is True

            await s.run_action("cycle_lens")  # -> capability
            await pilot.pause()
            assert s._lens == LENS_CAPABILITY
            assert s.query_one("#tree-row").display is True

            await s.run_action("cycle_lens")  # -> lifecycle
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert s._lens == LENS_LIFECYCLE
            assert s.query_one("#artifact-lifecycle", DataTable).display is True
            assert s.query_one("#tree-row").display is False
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_banner_names_the_active_lens(program):
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            banner = str(app.screen.query_one("#mode-banner", Static).render())
            assert "value lens" in banner  # never guessed
            await app.screen.run_action("cycle_lens")
            await pilot.pause()
            assert "capability lens" in str(app.screen.query_one("#mode-banner", Static).render())
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_lifecycle_lens_columns_come_from_the_shared_set(program):
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.lifecycle import LIFECYCLE_COLUMNS

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=LENS_LIFECYCLE))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one("#artifact-lifecycle", DataTable)
            labels = [str(c.label) for c in table.columns.values()]
            assert labels == list(LIFECYCLE_COLUMNS)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_opening_directly_in_a_lens(program):
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=LENS_LIFECYCLE))
            await pilot.pause()
            assert app.screen._lens == LENS_LIFECYCLE
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_l_aliases_to_the_lifecycle_lens(program):
    """3.1 — `l` keeps working and lands on Artifacts in the lifecycle lens."""
    from otaman_cli.console.app import HomeScreen, OtamanConsole, TreeScreen

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            type(app).notify = lambda self, msg, **kw: notes.append(str(msg))
            await app.screen.run_action("lifecycle")
            await pilot.pause()
            assert isinstance(app.screen, TreeScreen)
            assert app.screen._lens == LENS_LIFECYCLE
            assert any("`l`" in n and "lifecycle lens" in n for n in notes)
            await app.action_quit()

    asyncio.run(go())
