"""console-lens-navigation-and-filtering 1.1 — D1 arrow/Enter separation.

D1 ruled (Roman, 2026-09-21): `→` expands, `←` collapses, Enter opens detail
ONLY, and the side preview stays on `p`. "Applies to all three lenses" is the
part that had holes.

Two defects this pins, both found by reading the screen rather than the tree
builder — collapse-by-default and `auto_expand = False` were already in place
from the conformance defects, so the remaining gaps were on the OTHER side of
the same contract:

1. The advertised key strip lost the arrow semantics. `compose()` writes a
   banner naming `→ expand · ← collapse`, and then `_apply_lens()` — which runs
   on mount and on every lens switch — overwrites it with a strip that omits
   both. The advertised strip IS the discoverable surface (gate 6.1 F1), so a
   ruling nobody can see is not delivered.

2. Enter was DEAD in the lifecycle lens. That lens renders a DataTable, and
   TreeScreen only handled `on_tree_node_selected`; there was no
   `on_data_table_row_selected` at all. Enter opening detail is exactly the
   behaviour D1 makes uniform, and one of the three lenses did nothing.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.tree import LENS_CAPABILITY, LENS_LIFECYCLE, LENS_VALUE

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


def _banner(app) -> str:
    from textual.widgets import Static

    return str(app.screen.query_one("#mode-banner", Static).render())


@_textual
@pytest.mark.parametrize("lens", [LENS_VALUE, LENS_CAPABILITY])
def test_tree_lenses_advertise_arrow_expansion(program, lens):
    """The ruled arrow semantics must survive `_apply_lens`, on every tree lens."""
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=lens))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = _banner(app)
            assert "→" in text and "expand" in text, f"no expand hint in: {text!r}"
            assert "←" in text and "collapse" in text, f"no collapse hint in: {text!r}"
            assert "enter open" in text
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_lifecycle_lens_does_not_advertise_expansion(program):
    """It is a TABLE — promising expansion there would be a lie.

    The strip is a budget, not a constant: the lens that cannot expand says so
    by omission rather than by advertising a key that does nothing.
    """
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=LENS_LIFECYCLE))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = _banner(app)
            assert "expand" not in text, f"table lens advertises expansion: {text!r}"
            assert "enter open" in text, "…but Enter still opens detail here"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_enter_opens_detail_in_the_lifecycle_lens(program, monkeypatch):
    """The dead key: a DataTable row selection must open the change detail."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    opened: list = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=LENS_LIFECYCLE))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            screen = app.screen
            assert hasattr(screen, "on_data_table_row_selected"), (
                "the lifecycle lens has no Enter handler at all — D1 says Enter "
                "opens detail on all three lenses"
            )
            screen._rows = [SimpleNamespace(name="some-change")]
            monkeypatch.setattr(
                type(screen), "_open_change", lambda self, n: opened.append(n), raising=False
            )
            screen.on_data_table_row_selected(SimpleNamespace(cursor_row=0))
            await pilot.pause()
            assert opened == ["some-change"]
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_enter_on_an_out_of_range_row_is_ignored(program):
    """A stale cursor after a refresh must not raise into the TUI."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=LENS_LIFECYCLE))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            screen._rows = []
            screen.on_data_table_row_selected(SimpleNamespace(cursor_row=7))  # must not raise
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# 1.2 — D4: a rendered row is never visually empty


def test_a_row_with_no_id_and_no_title_still_renders_something():
    """D4, the literal case: `id or title` was the whole first segment.

    Both empty produced `('', 'bold')` — a blank line in the tree. The builder
    should never emit one, but D4 is deliberately enforced at RENDER so no
    future builder can reintroduce it.
    """
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(kind="solution", id="", title="", status="")
    assert node.display_label().strip(), "rendered an empty row"


def test_a_closed_row_keeps_a_legible_anchor():
    """D4's "at least its short colored form" — the defect Roman reported.

    A decided-out sibling rendered WHOLLY in `dim`. `dim` is relative to the
    theme's foreground, and in a low-contrast terminal the entire row reads as
    blank. The decided-out signal is kept for the row's detail, but its
    identity stays legible: a reader must be able to see that a row is there.
    """
    from otaman_cli.console.palette import GRAY_STYLE
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(kind="solution", id="SOL-1", title="an option", status="Proposed", grayed=True)
    segs = node.row_segments()
    assert segs, "grayed row rendered nothing"
    assert segs[0][0] == "SOL-1"
    assert GRAY_STYLE not in segs[0][1], "the row's own identity was dimmed to illegibility"
    # …and the rest still reads as decided-out.
    assert any(GRAY_STYLE in style for _text, style in segs[1:])


def test_every_node_kind_renders_a_non_empty_row():
    """The general rule, swept over the kinds the builders actually emit."""
    from otaman_cli.console.tree import TreeNode

    for kind in ("outcome", "solution", "change", "group", "capability", "registry", "disposition"):
        node = TreeNode(kind=kind, id="", title="", status="")
        assert node.display_label().strip(), f"{kind} rendered an empty row"


# ---------------------------------------------------------------------------
# 1.2 — orientation header and context line


def test_every_lens_states_the_question_it_answers():
    """A lens is a question; which one was documented only in source comments."""
    from otaman_cli.console.tree import LENSES, lens_orientation

    for lens in LENSES:
        text = lens_orientation(lens)
        assert text, f"{lens} has no orientation line"
        assert len(text) < 110, f"{lens} orientation is too long for a header: {text!r}"
    # The three must be distinguishable — a shared line orients nobody.
    assert len({lens_orientation(x) for x in LENSES}) == len(LENSES)


@_textual
@pytest.mark.parametrize("lens", [LENS_VALUE, LENS_CAPABILITY, LENS_LIFECYCLE])
def test_the_orientation_line_reaches_the_banner(program, lens):
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.tree import lens_orientation

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=lens))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert lens_orientation(lens) in _banner(app)
            await app.action_quit()

    asyncio.run(go())


def test_context_line_carries_description_refs_and_state():
    from otaman_cli.console.tree import TreeNode, context_line

    node = TreeNode(
        kind="change",
        id="some-change",
        title="Do the thing",
        next_actor="cli-agent",
        blocked_by="other-change",
    )
    line = context_line(node, refs=["JTBD-1", "SOL-2"])
    for expected in ("Do the thing", "JTBD-1", "SOL-2", "other-change", "cli-agent"):
        assert expected in line


def test_context_line_is_empty_rather_than_blank_when_there_is_nothing_to_say():
    """D4 applies here too — callers skip the line instead of drawing an empty one."""
    from otaman_cli.console.tree import TreeNode, context_line

    assert context_line(TreeNode(kind="group", id="g", title="")) == ""
    # A title identical to the id is not context, it is the row repeated.
    assert context_line(TreeNode(kind="change", id="x", title="x")) == ""


def test_context_line_does_not_repeat_a_ref_it_already_shows():
    from otaman_cli.console.tree import TreeNode, context_line

    line = context_line(TreeNode(kind="change", id="c", title="JTBD-1"), refs=["JTBD-1"])
    assert line.count("JTBD-1") == 1


# ---------------------------------------------------------------------------
# 1.3 — ':' command mode wired into the screen


def _screen_state(app):
    from textual.widgets import Input, Static

    return (
        app.screen.query_one("#tree-command", Input),
        app.screen.query_one("#tree-filter", Static),
    )


@_textual
def test_colon_opens_the_command_line(program):
    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            cmd, _active = _screen_state(app)
            assert cmd.display is False, "the command line must not cost a row until used"
            app.screen.action_command_mode()
            await pilot.pause()
            assert cmd.display is True and cmd.has_focus
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_submitted_filter_is_stored_and_displayed(program):
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            cmd, active = _screen_state(app)
            app.screen.on_input_submitted(
                SimpleNamespace(input=SimpleNamespace(id="tree-command"), value=":p1")
            )
            await pilot.pause()
            assert app.screen._query.describe() == "P1"
            assert active.display is True
            assert "P1" in str(active.render())
            assert cmd.display is False, "the command line closes after submitting"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_bare_view_command_does_not_clear_the_active_filter(program):
    """D2: `:e+` acts WITHIN the filter — that is why it lives in the grammar."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.filter_grammar import VIEW_EXPAND_ALL

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            submit = lambda v: app.screen.on_input_submitted(  # noqa: E731
                SimpleNamespace(input=SimpleNamespace(id="tree-command"), value=v)
            )
            submit(":p1")
            await pilot.pause()
            submit(":e+")
            await pilot.pause()
            assert app.screen._query.describe() == "P1", "the view command cleared the filter"
            assert app.screen._pending_view in (None, VIEW_EXPAND_ALL)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_an_unparseable_line_warns_and_leaves_the_view_alone(program):
    """The error is the feedback; emptying the screen on a typo teaches nothing."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            before = app.screen._query
            app.screen.on_input_submitted(
                SimpleNamespace(input=SimpleNamespace(id="tree-command"), value=":nonsense")
            )
            await pilot.pause()
            assert app.screen._query is before, "a broken line changed the active filter"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_escape_clears_the_filter_before_it_leaves_the_screen(program):
    """Otherwise the key that gets you out of a filter also throws away the view."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            screen.on_input_submitted(
                SimpleNamespace(input=SimpleNamespace(id="tree-command"), value=":p1")
            )
            await pilot.pause()
            assert not screen._query.is_empty

            screen.action_clear_filter()  # first Esc: clears the filter
            await pilot.pause()
            assert screen._query.is_empty
            assert app.screen is screen, "the first Esc left the screen"

            screen.action_clear_filter()  # second Esc: leaves
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_filter_prunes_but_keeps_the_path_to_a_match(program, monkeypatch):
    """A parent whose child matches must survive, or `:p1` empties the tree."""
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.filter_grammar import parse
    from otaman_cli.console.tree import TreeNode

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            screen._query = parse("p1")
            roots = [
                TreeNode(
                    kind="outcome",
                    id="JTBD-1",
                    title="",
                    children=[
                        TreeNode(kind="change", id="hit", title="", priority="P1"),
                        TreeNode(kind="change", id="miss", title="", priority="P3"),
                    ],
                ),
                TreeNode(
                    kind="outcome",
                    id="JTBD-2",
                    title="",
                    children=[TreeNode(kind="change", id="no", title="", priority="P2")],
                ),
            ]
            kept = screen._filtered(roots)
            assert [r.id for r in kept] == ["JTBD-1"], "the matching branch did not survive alone"
            assert [c.id for c in kept[0].children] == ["hit"], "non-matching children survived"
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# 1.4 — awaiting-you (D3)


def test_an_awaiting_row_carries_a_visible_token_not_just_a_colour():
    """The 1.2 lesson applied: a row that says "yours" only by being a slightly
    different shade says it to nobody."""
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(kind="change", id="c", title="t", awaiting=True)
    text = node.display_label()
    assert "you" in text
    # …and a non-awaiting row is unchanged.
    assert "you" not in TreeNode(kind="change", id="c", title="t").display_label()


def test_the_awaiting_marker_survives_a_grayed_row():
    """A decided-out row can still be waiting on you; the token must not vanish."""
    from otaman_cli.console.tree import TreeNode

    node = TreeNode(kind="change", id="c", title="t", awaiting=True, grayed=True)
    assert "you" in node.display_label()


@_textual
def test_stamping_marks_only_the_awaiting_ids_and_counts_them(program):
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.tree import TreeNode

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            roots = [
                TreeNode(
                    kind="outcome",
                    id="JTBD-1",
                    title="",
                    children=[
                        TreeNode(kind="change", id="mine", title=""),
                        TreeNode(kind="change", id="theirs", title=""),
                    ],
                )
            ]
            count = app.screen._stamp_awaiting(roots, {"mine"})
            assert count == 1
            by_id = {c.id: c for c in roots[0].children}
            assert by_id["mine"].awaiting is True
            assert by_id["theirs"].awaiting is False
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_awaiting_count_is_about_the_program_not_the_active_filter(program):
    """`:p1` must not change "3 awaiting you" — the header answers a different
    question from the filter, and a count that moved with it would be lying
    about which one."""
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.tree import TreeNode

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            roots = [
                TreeNode(kind="change", id="a", title="", priority="P1", awaiting=False),
                TreeNode(kind="change", id="b", title="", priority="P3", awaiting=False),
            ]
            # Both awaiting; only one survives a P1 filter.
            assert app.screen._stamp_awaiting(roots, {"a", "b"}) == 2
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_awaiting_ids_union_the_two_existing_sources(program, monkeypatch):
    """D3: derived from next-actor AND authored-changes — no new registry."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            from otaman_cli.console import artifacts
            from otaman_cli.console import lifecycle as clifecycle

            monkeypatch.setattr(
                clifecycle,
                "derive_lifecycle_rows",
                lambda program: [
                    SimpleNamespace(name="ratify-me", next_actor="human (otaman ratify ratify-me)"),
                    SimpleNamespace(name="not-mine", next_actor="spec-agent (archive the change)"),
                ],
            )
            monkeypatch.setattr(
                artifacts,
                "list_authored_changes",
                lambda program: [SimpleNamespace(name="approve-me")],
            )
            assert app.screen._awaiting_ids() == {"ratify-me", "approve-me"}
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_broken_lifecycle_derivation_does_not_empty_the_other_half(program, monkeypatch):
    """One unavailable source must not silently zero the awaiting set."""
    from types import SimpleNamespace

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            from otaman_cli.console import artifacts
            from otaman_cli.console import lifecycle as clifecycle

            def boom(program):
                raise RuntimeError("no specs repo")

            monkeypatch.setattr(clifecycle, "derive_lifecycle_rows", boom)
            monkeypatch.setattr(
                artifacts,
                "list_authored_changes",
                lambda program: [SimpleNamespace(name="approve-me")],
            )
            assert app.screen._awaiting_ids() == {"approve-me"}
            await app.action_quit()

    asyncio.run(go())
