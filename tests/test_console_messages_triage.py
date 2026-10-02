"""cmt 1.2 — Messages as the policy-ordered, type-grouped tree.

Roman's console: 978 pending, 7s paint, the four items that needed him buried in
task-complete noise. The surface now groups by TYPE, orders by core's review policy
(core #108), expands what is mandatory, and collapses the pile at the bottom.

The delta's three scenarios are here by name, plus the two defects found building
it: a Rich-markup label that silently ate `[SCR]` and `[mandatory]`, and a repaint
that slammed shut the group the human had just opened.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from otaman_cli.console import messages_tree as mt

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@dataclass(frozen=True)
class Row:
    """A message row double — the screen duck-types rows, so this is enough."""

    stem: str
    msg_type: str = "info"
    priority: str = "normal"
    timestamp: str = "2026-09-30T10:00:00Z"
    subject: str = "a subject"
    from_agent: str = "core-agent"
    path: Path = Path("/tmp/x.md")
    body: str = ""

    @property
    def is_decision(self) -> bool:
        return self.msg_type in ("spec-change-request", "outcome-proposal")

    @property
    def needs_answer(self) -> bool:
        from otaman_cli.console.decision_required import needs_answer

        return needs_answer(self.msg_type)


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return Program(name="demo", root=root)


def _declare(program, text: str) -> None:
    """Rewrite the fixture program's platform.yaml with *text* appended."""
    program.root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n" + text, encoding="utf-8"
    )


# --- the policy, and the line that names it ---------------------------------------


def test_the_band_order_is_cores_vocabulary():
    """rcg 1.4 — a shared vocabulary is asserted against core's constant.

    A band this file names and core does not would silently drop every message in
    it: `group_messages` indexes BAND_ORDER, so an unknown class raises and a
    renamed one buries the rows it classifies.
    """
    from otaman_core.review_policy import CLASSES

    assert mt.BAND_ORDER == CLASSES


def test_an_undeclared_policy_renders_the_default_and_names_itself(program):
    view = mt.load_policy(program.root)
    assert view.policy.source == "shipped-default"
    assert view.error == ""
    assert "shipped default" in view.source_line
    assert "platform.yaml" in view.source_line


def test_a_declared_policy_is_parsed_and_named(program):
    _declare(program, "review-policy:\n  mandatory: [review-request]\n")
    view = mt.load_policy(program.root)
    assert view.policy.source == "program"
    assert "review-request" in view.policy.mandatory
    assert "declared in platform.yaml (review-policy)" in view.source_line


def test_either_spelling_of_the_block_is_read(program):
    """`spec_policy` and `human-roster` live in ONE platform.yaml — both spellings
    are already in use, so reading only one would hand a human silence."""
    _declare(program, "review_policy:\n  mandatory: [review-request]\n")
    view = mt.load_policy(program.root)
    assert view.policy.source == "program"
    assert "(review_policy)" in view.source_line


def test_declaring_both_spellings_is_refused_rather_than_resolved(program):
    _declare(program, "review-policy:\n  mandatory: [a]\nreview_policy:\n  mandatory: [b]\n")
    view = mt.load_policy(program.root)
    assert view.policy.source == "shipped-default"
    assert "conflict" in view.error
    assert "review-policy" in view.source_line and "review_policy" in view.source_line


def test_a_malformed_policy_names_itself_and_keeps_the_default(program):
    _declare(program, "review-policy:\n  bogus: [x]\n")
    view = mt.load_policy(program.root)
    assert view.policy.source == "shipped-default"
    assert "malformed" in view.source_line
    assert "bogus" in view.error


def test_an_unreadable_platform_yaml_is_not_an_undeclared_policy(program):
    """The fail-open family's rule: absent and unreadable mean opposite things."""
    program.root.joinpath("platform.yaml").write_text("project: [unclosed\n", encoding="utf-8")
    view = mt.load_policy(program.root)
    assert view.policy.source == "shipped-default"
    assert "could not be read" in view.source_line
    assert view.error  # distinct from the undeclared case, which carries none
    assert "no review-policy declared" not in view.source_line


def test_a_missing_platform_yaml_is_simply_undeclared(tmp_path):
    view = mt.load_policy(tmp_path / "nowhere")
    assert view.policy.source == "shipped-default"
    assert view.error == ""


# --- the grouping ------------------------------------------------------------------


def _groups(rows, program=None, policy=None):
    if policy is None:
        from otaman_core.review_policy import default_policy

        policy = default_policy()
    return mt.group_messages(rows, policy)


def test_mandatory_groups_come_first_and_render_expanded():
    groups = _groups(
        [
            Row("t1", msg_type="task-complete"),
            Row("q1", msg_type="question"),
            Row("d1", msg_type="decision-required"),
        ]
    )
    assert [g.msg_type for g in groups] == ["decision-required", "question", "task-complete"]
    assert [g.review_class for g in groups] == ["mandatory", "opt-in", "auto-triage"]
    assert [g.collapsed for g in groups] == [False, True, True]


def test_a_group_counts_its_rows_and_names_its_class():
    groups = _groups([Row("a", msg_type="question"), Row("b", msg_type="question")])
    assert groups[0].count == 2
    assert groups[0].label == "question (2)"  # opt-in needs no marker: it is the default
    mandatory = _groups([Row("d", msg_type="decision-required")])[0]
    assert mandatory.label == "decision-required (1)  [mandatory]"


def test_an_urgent_message_cannot_hide_inside_its_types_collapsed_group():
    """`info` is auto-triage; an URGENT info is mandatory. Same type, two bands."""
    groups = _groups(
        [
            Row("i1", msg_type="info"),
            Row("i2", msg_type="info"),
            Row("boom", msg_type="info", priority="urgent"),
        ]
    )
    assert [(g.msg_type, g.review_class, g.count) for g in groups] == [
        ("info", "mandatory", 1),
        ("info", "auto-triage", 2),
    ]
    assert groups[0].collapsed is False
    assert [r.stem for r in groups[0].rows] == ["boom"]


def test_the_freshest_group_leads_its_band_and_rows_are_newest_first():
    groups = _groups(
        [
            Row("old-d", msg_type="decision-required", timestamp="2026-09-01T00:00:00Z"),
            Row("new-s", msg_type="spec-change-request", timestamp="2026-09-30T00:00:00Z"),
            Row("mid-s", msg_type="spec-change-request", timestamp="2026-09-15T00:00:00Z"),
        ]
    )
    assert [g.msg_type for g in groups] == ["spec-change-request", "decision-required"]
    assert [r.stem for r in groups[0].rows] == ["new-s", "mid-s"]


def test_the_summary_counts_per_class_not_per_list():
    groups = _groups(
        [
            Row("d", msg_type="decision-required"),
            Row("q", msg_type="question"),
            Row("t1", msg_type="task-complete"),
            Row("t2", msg_type="task-complete"),
        ]
    )
    # The mandatory count breaks out into its kinds (dae 1.2): a decision-required
    # is a question to ANSWER, and `a` does not apply to it.
    assert mt.summary_line(groups) == (
        "1 need you now (1 awaiting your answer) · 1 to read · 2 auto-triaged"
    )


def test_a_policy_edit_reorders_the_groups():
    from otaman_core.review_policy import parse_review_policy

    rows = [Row("r1", msg_type="review-request"), Row("d1", msg_type="decision-required")]
    declared = parse_review_policy({"mandatory": ["review-request", "decision-required"]})
    groups = _groups(rows, policy=declared)
    assert {g.msg_type for g in groups if g.review_class == "mandatory"} == {
        "review-request",
        "decision-required",
    }
    # and back: undeclared returns review-request to opt-in, collapsed
    plain = {g.msg_type: g for g in _groups(rows)}
    assert plain["review-request"].review_class == "opt-in"
    assert plain["review-request"].collapsed is True


# --- the screen --------------------------------------------------------------------


def _wired(program, rows):
    """A store filled with *rows* plus a loader that never scans."""
    from otaman_cli.console.loader import KIND_MESSAGE, Loader
    from otaman_cli.console.store import ReplaceKind, Store

    class _Inert:
        def start(self, on_change):
            self.on_change = on_change

        def stop(self):
            pass

    store = Store()
    loader = Loader(program, store, lister=lambda _p: list(rows), source=_Inert())
    store.dispatch(ReplaceKind(KIND_MESSAGE, tuple((r.stem, {"proposal": r}) for r in rows)))
    return store, loader


def _screen(app, program, store, loader):
    from otaman_cli.console.app import InboxScreen

    screen = InboxScreen(program, store=store, loader=loader)
    app.push_screen(screen)
    return screen


@_textual
def test_the_decision_is_on_top_of_a_thousand_messages(program):
    """The delta's first scenario, at Roman's measured scale."""
    from otaman_cli.console.app import OtamanConsole

    rows = [Row(f"t{i}", msg_type="task-complete") for i in range(996)]
    rows += [
        Row(f"d{i}", msg_type="decision-required", timestamp="2026-10-01T00:00:00Z")
        for i in range(3)
    ]
    rows.append(Row("scr", msg_type="spec-change-request", timestamp="2026-10-01T00:00:00Z"))
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The four that need him, and nothing else, are on screen.
            visible = screen.visible_rows()
            assert len(visible) == 4
            assert {r.msg_type for r in visible} == {"decision-required", "spec-change-request"}

            tree = screen.query_one("#inbox-tree")
            labels = [str(g.label) for g in tree.root.children]
            # The 996-message pile is ONE collapsed count, at the bottom.
            assert labels[-1] == "task-complete (996)  [auto-triage]"
            assert tree.root.children[-1].is_expanded is False

            # The budget the reactive-store change set (first paint under 500ms at
            # ~1000 pending). Measured at ~2ms, so this bound is 250x headroom and
            # will not flake on a loaded runner.
            started = time.perf_counter()
            screen._paint(rows)
            assert time.perf_counter() - started < 0.5
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_policy_not_volume_decides_what_renders_first(program):
    """The delta's second scenario, including the source line on screen."""
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole

    _declare(program, "review-policy:\n  mandatory: [review-request]\n")
    rows = [Row(f"r{i}", msg_type="review-request") for i in range(5)]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(screen.visible_rows()) == 5
            banner = str(screen.query_one("#mode-banner", Static).render())
            assert "declared in platform.yaml (review-policy)" in banner
            assert "5 need you now" in banner

            # Remove the declaration and refresh: back to opt-in, collapsed, and the
            # banner says the default is in force.
            program.root.joinpath("platform.yaml").write_text(
                "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
            )
            screen.action_refresh()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.visible_rows() == []
            banner = str(screen.query_one("#mode-banner", Static).render())
            assert "shipped default" in banner
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_nothing_mandatory_can_hide(program):
    """The delta's third scenario: a collapsed mandatory group reopens on refresh."""
    from otaman_cli.console.app import OtamanConsole

    rows = [Row("d1", msg_type="decision-required")]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = screen.query_one("#inbox-tree")
            tree.root.children[0].collapse()  # by hand, as a human would
            await pilot.pause()
            assert screen.visible_rows() == []

            screen._apply()  # the next snapshot from the store
            await pilot.pause()
            assert len(screen.visible_rows()) == 1
            assert tree.root.children[0].is_expanded is True
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_pile_the_human_opened_stays_open_across_a_repaint(program):
    """The other half: a repaint must not undo a deliberate expansion.

    The loader polls and every poll repaints. Rebuilding collapse state purely from
    the policy — which is what the mandatory invariant needs — also slammed shut the
    `task-complete` group a second after the human opened it.
    """
    from otaman_cli.console.app import OtamanConsole

    rows = [Row(f"t{i}", msg_type="task-complete") for i in range(3)]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = screen.query_one("#inbox-tree")
            tree.root.children[0].expand()
            await pilot.pause()
            assert len(screen.visible_rows()) == 3

            screen._apply()
            await pilot.pause()
            assert len(screen.visible_rows()) == 3, "the repaint closed what the human opened"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_arrow_contract_expands_and_collapses(program):
    """D1's `→ expands / ← collapses`, on the lens that now has groups to walk."""
    from otaman_cli.console.app import OtamanConsole

    rows = [Row("t1", msg_type="task-complete")]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = screen.query_one("#inbox-tree")
            tree.focus()
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            assert len(screen.visible_rows()) == 1
            await pilot.press("left")
            await pilot.pause()
            assert screen.visible_rows() == []
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_labels_keep_their_bracketed_tags(program):
    """A Tree label is Rich MARKUP; these labels are made of square brackets.

    Passed as plain strings, `[SCR]`, `[normal]`, `[a/A/x/d]` and `[mandatory]` are
    parsed as style tags and render as nothing — the group header printed
    `spec-change-request (1)` with its class silently eaten.
    """
    from otaman_cli.console.app import OtamanConsole

    rows = [Row("s1", msg_type="spec-change-request", subject="decide me")]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            tree = screen.query_one("#inbox-tree")
            group = tree.root.children[0]
            assert "[mandatory]" in str(group.label)
            row_label = str(group.children[0].label)
            assert "[SCR]" in row_label and "[normal]" in row_label and "[a/A/x/d]" in row_label
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_cursor_lands_on_the_first_message_and_survives_a_repaint(program):
    """A Tree has no cursor until one is set, and the action keys read the cursor."""
    from otaman_cli.console.app import OtamanConsole

    rows = [
        Row("d1", msg_type="decision-required", timestamp="2026-10-01T00:00:00Z"),
        Row("d2", msg_type="decision-required", timestamp="2026-09-01T00:00:00Z"),
    ]
    store, loader = _wired(program, rows)

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _screen(app, program, store, loader)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen._highlighted_proposal().stem == "d1"

            screen.focus_row(1)
            assert screen._highlighted_proposal().stem == "d2"
            screen._apply()  # a poll repaints under the human's cursor
            await pilot.pause()
            assert screen._highlighted_proposal().stem == "d2", "the repaint moved the cursor"
            await app.action_quit()

    asyncio.run(go())
