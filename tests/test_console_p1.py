"""console-ia-consolidation P1 — merging the duplicate surfaces (2.1/2.2/2.3).

2.1 Messages absorbs Decisions: ONE list of everything addressed to the human,
    typed, with decision rows carrying their action keys and the same writes.
    The type-exclusion split — where an item's TYPE decided which of two screens
    could see it — disappears.
2.2 Spec review becomes an action on an authored change row in Artifacts,
    reusing `advance_to_spec_approved`; the standalone browser becomes an alias.
2.3 `d` and `b` keep working as aliases, landing on their new home with a notice
    (D8: removal never rides in the same release as the move).
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus

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


def _msg(program, stem: str, *, to="human", mtype="info", frm="core-agent", subject="s", **extra):
    active, _ = program.bus_paths()
    fields = "".join(f"{k}: {v}\n" for k, v in extra.items())
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: {frm}\nto: {to}\ntype: {mtype}\n"
        f"priority: normal\ntimestamp: 2026-09-16T00:00:00Z\n{fields}---\n\n"
        f"## Subject: {subject}\n\nbody text here\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 2.1 — one list, typed rows


def test_the_one_list_carries_decisions_and_plain_messages(program):
    _msg(program, "m1", mtype="info", subject="just read me")
    _msg(program, "m2", mtype="spec-change-request", subject="decide me")
    rows = bus.list_human_queue(program)
    assert len(rows) == 2
    kinds = {r.subject: r.is_decision for r in rows}
    assert kinds == {"decide me": True, "just read me": False}


def test_is_decision_is_a_property_of_the_item(program):
    """Not of which screen found it — that inversion is what 2.1 removes."""
    _msg(program, "a", mtype="spec-change-request")
    _msg(program, "b", mtype="outcome-proposal")
    _msg(program, "c", mtype="info")
    by_type = {r.msg_type: r.is_decision for r in bus.list_human_queue(program)}
    assert by_type == {"spec-change-request": True, "outcome-proposal": True, "info": False}


def test_decisions_sort_first(program):
    _msg(program, "zzz-plain", mtype="info")
    _msg(program, "aaa-decision", mtype="spec-change-request")
    rows = bus.list_human_queue(program)
    assert rows[0].is_decision is True  # despite sorting last by name


def test_agent_addressed_decision_is_NOT_lost(program):
    """REGRESSION, caught against the live bus: the old decisions lister filtered
    on TYPE ALONE, so an outcome-proposal addressed to a strategic agent still
    reached the human's queue — two do, live. Requiring `to: human` would have
    silently dropped them, and a pending decision vanishing from the human's
    queue is the silent-approval-loss class. Absorbing a surface must not narrow
    it."""
    _msg(program, "to-agent", to="cofounder-agent", mtype="outcome-proposal", subject="strategy")
    rows = bus.list_human_queue(program)
    assert [r.subject for r in rows] == ["strategy"]
    assert rows[0].is_decision is True


def test_cc_copies_are_still_excluded(program):
    _msg(program, "primary", mtype="outcome-proposal", subject="p")
    _msg(program, "cc", mtype="outcome-proposal", subject="p", **{"x-cc": "true"})
    assert len(bus.list_human_queue(program)) == 1


def test_acked_items_are_excluded(program):
    _msg(program, "done", mtype="spec-change-request")
    active, acks = program.bus_paths()
    (acks / "done.human.ack").write_text("approved\n", encoding="utf-8")
    assert bus.list_human_queue(program) == []


def test_messages_to_other_agents_are_excluded(program):
    _msg(program, "not-mine", to="core-agent", mtype="info")
    assert bus.list_human_queue(program) == []


def test_bodies_are_lazy_and_read_on_demand(program):
    """The list needs only subjects; full-reading every row cost ~1.6s on a
    5.4k-message bus. The body is read when a detail screen opens ONE item."""
    _msg(program, "m", mtype="info", subject="subj")
    (row,) = bus.list_human_queue(program)
    assert row.subject == "subj"
    assert row.body == ""  # not carried by the list
    assert "body text here" in bus.read_body(row)  # read from row.path


def test_read_body_prefers_an_already_loaded_body(program):
    """`list_pending_proposals` still eager-loads; those callers are unaffected."""
    item = bus.Proposal(
        stem="s",
        subject="x",
        from_agent="a",
        timestamp="",
        priority="normal",
        path=program.root / "nonexistent.md",
        body="already here",
    )
    assert bus.read_body(item) == "already here"


def test_read_body_tolerates_a_missing_file(program):
    item = bus.Proposal(
        stem="s",
        subject="x",
        from_agent="a",
        timestamp="",
        priority="normal",
        path=program.root / "gone.md",
        body="",
    )
    assert bus.read_body(item) == ""


def test_from_human_flag_survives_the_merge(program):
    """The read view labels the sender with it; Proposal needed the same flag
    InboxMessage had."""
    _msg(program, "h", frm="roman", mtype="info")
    _msg(program, "a", frm="core-agent", mtype="info")
    flags = {r.from_agent: r.from_human for r in bus.list_human_queue(program)}
    assert flags == {"roman": True, "core-agent": False}


# ---------------------------------------------------------------------------
# 2.1 — the screen


@_textual
def test_messages_screen_lists_both_kinds(program):
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen, OtamanConsole

    _msg(program, "m1", mtype="info", subject="read me")
    _msg(program, "m2", mtype="spec-change-request", subject="decide me")

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(InboxScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()  # the list loads OFF the UI
            await pilot.pause()  # thread now (#177)
            lv = app.screen.query_one("#inbox-list", ListView)
            assert len(lv.children) == 2
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_decision_rows_advertise_their_keys(program):
    from otaman_cli.console.app import queue_row_label

    def row(mtype):
        return queue_row_label(
            bus.Proposal(
                stem="s",
                subject="s",
                from_agent="a",
                timestamp="",
                priority="normal",
                path=program.root,
                body="",
                msg_type=mtype,
            )
        )

    assert "[a/A/x/d]" in row("spec-change-request")  # discoverable on the row
    assert "*" in row("outcome-proposal")  # and marked as a decision
    assert "[a/A/x/d]" not in row("info")


@_textual
def test_decision_keys_are_bound_on_the_messages_screen(program):
    from otaman_cli.console.app import InboxScreen

    actions = {b.action for b in InboxScreen.BINDINGS}
    assert {"approve", "approve_auto", "reject", "defer"} <= actions


@_textual
def test_a_plain_row_refuses_the_decision_keys_by_name(program):
    """The dead-end rule from P0: the key says why it does not apply."""
    from textual.widgets import ListView

    from otaman_cli.console.app import InboxScreen, OtamanConsole

    _msg(program, "m1", mtype="info", subject="read me")
    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(InboxScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()  # the list loads OFF the UI
            await pilot.pause()  # thread now (#177)
            app.screen.query_one("#inbox-list", ListView).index = 0
            await pilot.pause()
            type(app).notify = lambda self, msg, **kw: notes.append(str(msg))
            app.screen.action_approve()
            await pilot.pause()
            assert notes and "Not a decision row" in notes[0]
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_proposal_screen_and_messages_share_one_decide_path(program):
    """ "the same writes as today" — a second copy is how drift starts."""
    from otaman_cli.console.app import InboxScreen, ProposalScreen, _DecisionActions

    assert issubclass(ProposalScreen, _DecisionActions)
    assert issubclass(InboxScreen, _DecisionActions)
    # neither screen re-implements the write
    assert "_apply_decision" not in ProposalScreen.__dict__
    assert "_apply_decision" not in InboxScreen.__dict__


# ---------------------------------------------------------------------------
# 2.3 — aliases (D8)


@_textual
def test_d_and_b_are_aliases_that_land_and_notify(program):
    from otaman_cli.console.app import HomeScreen, InboxScreen, OtamanConsole, TreeScreen

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            type(app).notify = lambda self, msg, **kw: notes.append(str(msg))

            await app.screen.run_action("decisions")
            await pilot.pause()
            assert isinstance(app.screen, InboxScreen)
            assert any("`d`" in n and "Messages" in n for n in notes)

            app.pop_screen()
            await pilot.pause()
            notes.clear()
            await app.screen.run_action("review")
            await pilot.pause()
            assert isinstance(app.screen, TreeScreen)
            assert any("`b`" in n and "Artifacts" in n for n in notes)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_retired_keys_are_still_bound(program):
    """D8: they keep WORKING. Removal waits for a clean usage signal and never
    rides in the same release as the move."""
    from otaman_cli.console.app import HomeScreen

    keys = {b.key for b in HomeScreen.BINDINGS}
    assert {"d", "b", "l", "m", "t"} <= keys


# ---------------------------------------------------------------------------
# 2.2 — spec review as an action on an authored row


@_textual
def test_review_key_is_bound_on_the_tree(program):
    from otaman_cli.console.app import TreeScreen

    assert "review" in {b.action for b in TreeScreen.BINDINGS}


@_textual
def test_authored_filter_uses_the_shared_authored_set(program, monkeypatch):
    """The filtered view and `advance_to_spec_approved` must agree by
    construction, so both read `list_authored_changes`."""
    from types import SimpleNamespace

    from otaman_cli.console import artifacts
    from otaman_cli.console.app import _authored_change_roots
    from otaman_cli.console.tree import TreeNode

    monkeypatch.setattr(
        artifacts, "list_authored_changes", lambda p: [SimpleNamespace(name="authored-one")]
    )
    roots = [
        TreeNode(
            kind="outcome",
            id="JTBD-1",
            title="o",
            children=[
                TreeNode(kind="change", id="authored-one", title=""),
                TreeNode(kind="change", id="other", title=""),
            ],
        )
    ]
    got = _authored_change_roots(program, roots)
    assert [n.id for n in got] == ["authored-one"]  # lifted to top level, filtered


@_textual
def test_authored_filter_is_empty_when_nothing_is_authored(program, monkeypatch):
    from otaman_cli.console import artifacts
    from otaman_cli.console.app import _authored_change_roots

    monkeypatch.setattr(artifacts, "list_authored_changes", lambda p: [])
    assert _authored_change_roots(program, []) == []


@_textual
def test_authored_filter_degrades_gracefully(program, monkeypatch):
    from otaman_cli.console import artifacts
    from otaman_cli.console.app import _authored_change_roots

    def boom(p):
        raise RuntimeError("no specs repo")

    monkeypatch.setattr(artifacts, "list_authored_changes", boom)
    assert _authored_change_roots(program, []) == []  # never raises into the TUI


@_textual
def test_review_refuses_a_non_authored_row_by_name(program, monkeypatch):
    """The dead-end rule: a row that cannot be reviewed says so by name."""
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.tree import TreeNode

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
            monkeypatch.setattr(type(app.screen), "_is_authored", lambda self, n: False)
            monkeypatch.setattr(
                type(app.screen),
                "_cursor_node",
                lambda self: TreeNode(kind="change", id="not-authored", title=""),
            )
            app.screen.action_review()
            await pilot.pause()
            assert notes and "not an authored change" in notes[0]
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_review_runs_the_shared_advance_on_an_authored_row(program, monkeypatch):
    """Reuses `advance_to_spec_approved` — identical to `otaman spec approve`,
    the lifecycle key and the old b-screen."""
    from otaman_cli.console import artifacts
    from otaman_cli.console.app import OtamanConsole, ReasonModal, TreeScreen
    from otaman_cli.console.tree import TreeNode

    seen: dict = {}
    monkeypatch.setattr(
        artifacts,
        "advance_to_spec_approved",
        lambda prog, name, reason="": seen.update(name=name, reason=reason) or (True, "ok"),
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(type(app.screen), "_is_authored", lambda self, n: True)
            monkeypatch.setattr(
                type(app.screen),
                "_cursor_node",
                lambda self: TreeNode(kind="change", id="authored-one", title=""),
            )
            app.screen.action_review()
            await pilot.pause()
            assert isinstance(app.screen, ReasonModal)  # asks for a review note
            from types import SimpleNamespace

            app.screen.on_input_submitted(SimpleNamespace(value="looks good"))
            await pilot.pause()
            assert seen == {"name": "authored-one", "reason": "looks good"}
            await app.action_quit()

    asyncio.run(go())
