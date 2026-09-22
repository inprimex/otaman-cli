"""Roman's four Artifacts defects on v0.5.9 (spec-agent 20260921T111327 + addendum).

All four are conformance against shipped canon, not new scope:

1. keyboard scroll dead on the capability and lifecycle lenses
2. the selected row is invisible until you click with a mouse
3. closed solutions "render as EMPTY lines" on the value lens
4. Enter on a parent both toggles AND opens the detail

Reproducing them first collapsed four reports into three causes. 1 and 2 are
ONE bug: `_apply_lens` toggled `.display` without moving FOCUS, so hiding the
focused widget dropped focus and nothing restored it — after one visit to the
lifecycle lens the keyboard was dead on EVERY lens, and with nothing focused
Textual draws no cursor at all.

Defect 3 was never emptiness. The rows carried their full text, styled
`grey42` — a FIXED #6c6c6c that vanished into Roman's background.

Plus the windowing canon already requires (interactive-human-console spec.md:209,
"children windowed to a small scrollable set"): the value lens put 188 rows on
screen at open and the capability lens 225.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest
import yaml

_textual = pytest.mark.skipif(
    __import__("importlib").util.find_spec("textual") is None, reason="textual not installed"
)


@pytest.fixture
def program():
    from otaman_cli.console.bus import Program

    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "d", "version": "1.0", "repos": []}), encoding="utf-8"
    )
    return Program(name="d", root=root)


async def _open(app, pilot, program):
    from otaman_cli.console.app import TreeScreen

    app.push_screen(TreeScreen(program))
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return app.screen


async def _cycle_to(app, pilot, screen, lens):
    for _ in range(4):
        if screen._lens == lens:
            return
        await pilot.press("L")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
    raise AssertionError(f"never reached lens {lens}")


# ---------------------------------------------------------------------------
# defects 1 + 2 — one cause: focus must follow the lens


@_textual
@pytest.mark.parametrize(
    ("lens", "expected"),
    [("value", "Tree"), ("capability", "Tree"), ("lifecycle", "DataTable")],
)
def test_each_lens_focuses_the_widget_it_renders_into(program, lens, expected):
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=program.root / "logs")
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            screen = await _open(app, pilot, program)
            await _cycle_to(app, pilot, screen, lens)
            assert app.focused is not None, f"{lens} lens: nothing focused — keyboard is dead"
            assert type(app.focused).__name__ == expected
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_focus_survives_a_full_lens_cycle(program):
    """THE DEFECT. Visiting the lifecycle lens hid the focused Tree, focus was
    dropped, and cycling back never restored it — so the keyboard stayed dead on
    every lens until the human reached for the mouse."""
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=program.root / "logs")
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            screen = await _open(app, pilot, program)
            await _cycle_to(app, pilot, screen, "lifecycle")
            await _cycle_to(app, pilot, screen, "value")
            assert app.focused is not None, "focus lost after returning from the lifecycle lens"
            assert type(app.focused).__name__ == "Tree"
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# defect 4 — Enter opens; the arrows expand and collapse


@_textual
def test_enter_on_a_parent_opens_without_toggling(program):
    """Textual's own docstring: with `auto_expand` True, selecting a non-leaf
    causes "both an expand/collapse event ... as well as a selected event". Our
    banner and canon both say arrows expand/collapse and Enter opens."""
    from textual.widgets import Tree

    from otaman_cli.console.app import OtamanConsole
    from otaman_cli.console.tree import TreeNode

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=program.root / "logs")
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            screen = await _open(app, pilot, program)
            parent = TreeNode(
                kind="change", id="a-change", title="c", status="in-flight", collapsed=True
            )
            parent.children = [
                TreeNode(kind="reference", id="x", title="", ref_kind="capability", ref_id="x")
            ]
            screen._populate([parent])
            await pilot.pause()
            tree = screen.query_one("#artifact-tree", Tree)
            tree.focus()
            await pilot.pause()
            await pilot.press("down")  # off the tree's own root
            await pilot.pause()

            node = tree.root.children[0]
            assert node.is_expanded is False
            depth = len(app.screen_stack)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            assert node.is_expanded is False, "Enter toggled the node as well as opening it"
            assert len(app.screen_stack) > depth, "Enter did not open the detail"
            await app.action_quit()

    asyncio.run(go())


def test_the_tree_disables_textuals_expand_on_select():
    import inspect

    from otaman_cli.console.app import TreeScreen

    assert "auto_expand = False" in inspect.getsource(TreeScreen.on_mount)


# ---------------------------------------------------------------------------
# defect 3 — "empty" rows were unreadable, not absent


def test_closed_rows_still_carry_their_text():
    """Pins the diagnosis: nothing was ever missing from these rows."""
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(
        kind="solution",
        id="SOL-2",
        title="discarded one",
        status="Discarded",
        closed=True,
        grayed=True,
    )
    assert node.display_label() == "SOL-2   discarded | discarded one"


def test_de_emphasis_is_theme_relative_not_a_fixed_grey():
    """`grey42` is #6c6c6c whatever the terminal theme, which is how a rendered
    row became an apparently blank line. `dim` is relative to the theme's own
    foreground, and is the idiom this palette already uses for `Retired`."""
    from otaman_cli.console.palette import GRAY_STYLE

    assert GRAY_STYLE == "dim"
    assert not GRAY_STYLE.startswith("grey")


def test_every_segment_of_a_grayed_row_uses_it_except_its_identity():
    """AMENDED by console-lens-navigation-and-filtering D4.

    This asserted `styles == {"dim"}` — every segment grayed. That was
    tree-view-polish 1.3's rule, and it is what Roman then reported as "closed
    solutions are blank lines": `dim` is relative to the theme's foreground, so
    a wholly-dim row can read as nothing at all in a low-contrast terminal.

    D4 makes "a rendered row is never visually empty — every row shows at least
    its short colored form" normative, which overrides the uniform gray. The
    row's IDENTITY stays legible; everything after it is still gray, so the
    decided-out signal survives without costing the reader the row.
    """
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(
        kind="solution",
        id="SOL-3",
        title="decided out",
        status="Considering",
        closed=True,
        grayed=True,
        priority="P1",
    )
    segs = [(text, style) for text, style in node.row_segments() if text.strip()]
    assert segs[0] == ("SOL-3", "bold"), "the row lost its legible anchor"
    assert {style for _text, style in segs[1:]} == {"dim"}, "the rest must still read as gray"


# ---------------------------------------------------------------------------
# windowing — canon's "small scrollable set"


def _rows_on_screen(roots) -> int:
    total = 0
    for node in roots:
        total += 1
        if not node.collapsed:
            total += _rows_on_screen(node.children)
    return total


def test_outcome_roots_open_collapsed(tmp_path, monkeypatch):
    from otaman_cli.console.bus import Program
    from otaman_cli.console.tree import build_artifact_tree

    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "d", "version": "1.0", "repos": []}), encoding="utf-8"
    )
    strat = tmp_path / "strategy"
    strat.mkdir()
    strat.joinpath("outcomes.yaml").write_text(
        yaml.dump(
            {
                "outcomes": [
                    {
                        # ids and required fields follow the registry schema —
                        # the loaders VALIDATE, so a minimal stub yields no tree
                        "id": f"JTBD-{i}-thing",
                        "status": "Approved",
                        "created": "2026-09-01",
                        "updated": "2026-09-01",
                        "statement": {
                            "as-a": "a",
                            "i-want-to": "b",
                            "incremental-outcome": "c",
                            "so-i-can": "d",
                        },
                    }
                    for i in range(3)
                ]
            }
        ),
        encoding="utf-8",
    )
    strat.joinpath("solutions.yaml").write_text(
        yaml.dump(
            {
                "solutions": [
                    {
                        "id": f"SOL-{i}{j}-option",
                        "outcome-id": f"JTBD-{i}-thing",
                        "status": "Considering",
                        "description": "an option",
                        "created": "2026-09-01",
                        "updated": "2026-09-01",
                    }
                    for i in range(3)
                    for j in range(4)
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    from otaman_cli.yaml_fast import clear_cache

    clear_cache()

    roots = build_artifact_tree(Program(name="d", root=root))
    assert roots, "expected outcome roots"
    assert all(r.collapsed for r in roots if r.kind == "outcome")
    # 3 outcomes with 4 solutions each: 3 rows on screen, not 15
    assert _rows_on_screen(roots) == len(roots)


def test_the_unlinked_group_opens_collapsed_and_names_its_size():
    """It held 59 changes on the live program, every one on screen at open."""
    import inspect

    from otaman_cli.console import tree

    src = inspect.getsource(tree.build_artifact_tree)
    assert "unlinked changes — {len(orphans)}" in src
    assert "collapsed=True" in src


def test_capability_roots_open_collapsed():
    import inspect

    from otaman_cli.console import capability

    assert "collapsed=True" in inspect.getsource(capability.build_capability_tree)
