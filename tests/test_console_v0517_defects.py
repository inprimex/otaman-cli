"""Roman's v0.5.17 console drive — three defects and the store's lifetime.

Reported by spec-agent, 20261001T135403. Each one had already shipped as an
intention: the strip advertised arrow keys, the tree read an outcome link, the
`:a` filter existed, and the store was specified process-wide. None of the four
was true of the running program.

* **arrows** — Textual's `Tree` binds `space` and `shift+left/right`. It binds
  plain `→`/`←` to NOTHING, so D1's ruling (`→` expands, `←` collapses) was never
  implemented and every lens strip has advertised it since console-lens 1.1.
* **outcome-id** — the linkage read only the free-text `outcome:` sentence, never
  the `outcome-id:` stamp that is the back-link convention. Measured on the live
  program before the fix: **72 changes, 0 linked**, 26 of them stamped.
* **`:a`** — shipped in 1.4 and named nowhere on screen.
* **the store** — `InboxScreen` built its own on every mount, so leaving Messages
  threw it away. Roman at 978 pending: 7s first entry, 6s on RETURN.
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


def _outcome(oid, *, priority="P1", status="Approved", chosen=None):
    return SimpleNamespace(
        id=oid,
        priority=priority,
        status=status,
        created="2026-01-01",
        chosen_solution=chosen,
        statement=SimpleNamespace(incremental_outcome=f"outcome {oid}"),
    )


def _registries(monkeypatch, outcomes, solutions_for=None):
    monkeypatch.setattr(tree, "registries_enabled", lambda program: True)
    reg_out = SimpleNamespace(outcomes=outcomes)
    reg_sol = SimpleNamespace(for_outcome=lambda oid: (solutions_for or {}).get(oid, []))
    monkeypatch.setattr(tree, "_load_registries", lambda program: (reg_out, reg_sol))


# ===========================================================================
# Defect 1 — the arrow keys D1 ruled and nothing bound.


@_textual
def test_textual_binds_no_plain_arrows_which_is_why_the_strip_was_a_lie():
    """The root cause, pinned. If a future Textual binds them itself this fails,
    and the right response is to delete our bindings, not to widen them."""
    from textual.widgets import Tree

    keys = {b.key for b in Tree.BINDINGS}
    assert "space" in keys and "shift+left" in keys
    assert "left" not in keys and "right" not in keys


@_textual
def test_the_artifact_tree_binds_right_to_expand_and_left_to_collapse():
    from otaman_cli.console.app import ArtifactTree

    bound = {b.key: b.action for b in ArtifactTree.BINDINGS}
    assert bound["right"] == "expand_cursor"
    assert bound["left"] == "collapse_cursor"


@_textual
def test_the_parent_bindings_survive_the_subclass():
    """Up/down/enter must still work — a subclass that REPLACED BINDINGS would
    pass the test above and break the keys that were never broken."""
    from otaman_cli.console.app import ArtifactTree

    merged = {}
    for klass in reversed(ArtifactTree.__mro__):
        for b in getattr(klass, "BINDINGS", []) or []:
            merged[getattr(b, "key", None)] = getattr(b, "action", None)
    assert merged["up"] == "cursor_up"
    assert merged["down"] == "cursor_down"
    assert merged["enter"] == "select_cursor"


@_textual
@pytest.mark.parametrize("lens", ["value", "capability"])
def test_arrows_expand_and_collapse_on_the_lens_roman_found_dead(program, monkeypatch, lens):
    """The two lenses named in the report, driven through the real key path."""
    _registries(monkeypatch, outcomes=[_outcome("JTBD-1")])
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("alpha")])
    monkeypatch.setattr(tree, "_change_outcome_id", lambda program, name: "JTBD-1")
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    # The capability lens is a different builder over the same widget; feed it a
    # collapsed parent of its own so both lenses are driven for real rather than
    # one of them being asserted against an empty tree.
    import otaman_cli.console.capability as capability

    monkeypatch.setattr(
        capability,
        "build_capability_tree",
        lambda program: [
            tree.TreeNode(
                kind="capability",
                id="CAP-1",
                title="a capability",
                collapsed=True,
                children=[tree.TreeNode(kind="change", id="alpha", title="")],
            )
        ],
    )
    monkeypatch.setattr(capability, "dispositions_group", lambda program: None)
    from textual.widgets import Tree

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    seen = {}

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(program, lens=lens))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            widget = app.screen.query_one("#artifact-tree", Tree)
            # Walk down to a row that CAN expand and is not already expanded —
            # the cursor starts on the tree root, which is expanded. Outcome rows
            # open collapsed by design (188 rows on the live program otherwise).
            for _ in range(10):
                node = widget.cursor_node
                if node is not None and node.allow_expand and not node.is_expanded:
                    break
                await pilot.press("down")
            else:  # pragma: no cover - the fixture always provides one
                raise AssertionError("no collapsed expandable row to drive")
            seen["before"] = bool(widget.cursor_node.is_expanded)
            await pilot.press("right")
            seen["after_right"] = bool(widget.cursor_node.is_expanded)
            await pilot.press("left")
            seen["after_left"] = bool(widget.cursor_node.is_expanded)
            await app.action_quit()

    asyncio.run(go())
    assert seen["before"] is False
    assert seen["after_right"] is True, "→ did not expand"
    assert seen["after_left"] is False, "← did not collapse"


# ===========================================================================
# Defect 2 — the outcome-id stamp.


def test_the_stamp_is_read_in_preference_to_the_prose(program, monkeypatch, tmp_path):
    """`outcome-id:` is the exact id; `outcome:` is a sentence that merely tends
    to open with one. The stamp wins when both are present."""
    changes = tmp_path / "changes"
    (changes / "ch").mkdir(parents=True)
    (changes / "ch" / ".openspec.yaml").write_text(
        'outcome-id: JTBD-118-interactive-spec-editing\noutcome: "JTBD-7 something else"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("otaman_cli.console.lifecycle._specs_changes_dir", lambda program: changes)
    assert tree._change_outcome_id(program, "ch") == "JTBD-118-interactive-spec-editing"


def test_prose_is_still_read_when_there_is_no_stamp(program, monkeypatch, tmp_path):
    """6 live changes carry only the sentence — dropping the fallback would trade
    one set of missing links for another."""
    changes = tmp_path / "changes"
    (changes / "ch").mkdir(parents=True)
    (changes / "ch" / ".openspec.yaml").write_text(
        'outcome: "JTBD-108 lineage (surface consolidation)"\n', encoding="utf-8"
    )
    monkeypatch.setattr("otaman_cli.console.lifecycle._specs_changes_dir", lambda program: changes)
    assert tree._change_outcome_id(program, "ch") == "JTBD-108"


def test_a_full_stamp_resolves_case_insensitively():
    """The prose scraper upper-cases what it finds, which destroys a slug id."""
    ids = {"JTBD-118-interactive-spec-editing"}
    assert tree._resolve_outcome("JTBD-118-interactive-spec-editing", ids) == (
        "JTBD-118-interactive-spec-editing"
    )
    assert tree._resolve_outcome("JTBD-118-INTERACTIVE-SPEC-EDITING", ids) == (
        "JTBD-118-interactive-spec-editing"
    )


def test_a_bare_number_resolves_to_the_slug_on_its_boundary():
    ids = {"JTBD-1-first", "JTBD-118-interactive-spec-editing"}
    assert tree._resolve_outcome("JTBD-118", ids) == "JTBD-118-interactive-spec-editing"
    assert tree._resolve_outcome("JTBD-1", ids) == "JTBD-1-first"


def test_an_ambiguous_bare_number_resolves_to_nothing():
    """Two candidates mean the id does not identify one outcome. Nesting under a
    guess is worse than a visibly unresolved row."""
    ids = {"JTBD-7-alpha", "JTBD-7-beta"}
    assert tree._resolve_outcome("JTBD-7", ids) is None


def test_an_unknown_id_resolves_to_nothing():
    assert tree._resolve_outcome("JTBD-999", {"JTBD-1-a"}) is None
    assert tree._resolve_outcome("n/a because this is a retrofit", {"JTBD-1-a"}) is None
    assert tree._resolve_outcome(None, {"JTBD-1-a"}) is None


def test_a_stamped_change_nests_under_its_outcome(program, monkeypatch):
    """The defect itself: a stamped change rendered under 'unlinked changes'."""
    _registries(monkeypatch, outcomes=[_outcome("JTBD-118-interactive-spec-editing")])
    monkeypatch.setattr(tree, "_change_rows", lambda program: [_row("console-reactive-store")])
    monkeypatch.setattr(
        tree, "_change_outcome_id", lambda program, name: "JTBD-118-interactive-spec-editing"
    )
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})

    roots = tree.build_artifact_tree(program)
    outcome = next(r for r in roots if r.kind == "outcome")
    assert [c.id for c in outcome.children] == ["console-reactive-store"]
    assert not any(r.kind == "group" for r in roots), "it is linked, so no group at all"


def test_an_id_that_names_no_outcome_is_grouped_apart_from_the_genuine_orphans(
    program, monkeypatch
):
    """4 live changes name an outcome the registry does not have — two JTBDs
    approved but not yet registered, one with prose in the id field, one that
    scraped `JTBD-99-102` out of a range. Burying them among 40 real orphans
    hides the fixable case inside the expected one."""
    _registries(monkeypatch, outcomes=[_outcome("JTBD-1-known")])
    monkeypatch.setattr(
        tree, "_change_rows", lambda program: [_row("dangles"), _row("truly-unlinked")]
    )
    monkeypatch.setattr(
        tree,
        "_change_outcome_id",
        lambda program, name: "JTBD-999-not-in-the-registry" if name == "dangles" else None,
    )
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})

    roots = tree.build_artifact_tree(program)
    groups = {r.id: [c.id for c in r.children] for r in roots if r.kind == "group"}
    unlinked = next(k for k in groups if k.startswith("(unlinked"))
    dangling = next(k for k in groups if "does not resolve" in k)
    assert groups[unlinked] == ["truly-unlinked"]
    assert groups[dangling] == ["dangles"]
    assert "1" in dangling, "the group must say how many it hides"


# ===========================================================================
# Finding 3 — the filter that shipped and could not be found.


@_textual
def test_the_awaiting_count_names_the_filter_that_selects_it(program, monkeypatch):
    """Roman asked for a filter that had shipped in 1.4. A count that says how
    many are waiting but not how to see them is the number without the door."""
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole, TreeScreen

    monkeypatch.setattr(tree, "registries_enabled", lambda program: False)
    monkeypatch.setattr(tree, "_change_rows", lambda program: [])
    monkeypatch.setattr(tree, "_blocked_map", lambda program: {})
    seen = {}

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = TreeScreen(program)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            screen._awaiting_count = 3
            screen._apply_lens()
            await pilot.pause()
            seen["banner"] = str(app.screen.query_one("#mode-banner", Static).render())
            seen["placeholder"] = app.screen.query_one("#tree-command").placeholder
            await app.action_quit()

    asyncio.run(go())
    assert "3 awaiting you (:a)" in seen["banner"]
    assert ":a" in seen["placeholder"]
