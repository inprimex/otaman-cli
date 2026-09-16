"""console-ia-consolidation P2-B — the capability lens and proposal provenance.

3.2 Capability roots come from `openspec/specs/`, each listing its requirement
    count, the changes that shaped it as REFERENCE lines, and any open delta.
3.4 D5 — an SCR is an edge with three fates, none of them a tree level: pending
    → Messages, approved → provenance on the change it minted, unminted → the
    dispositions ledger as one collapsed group.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.capability import (
    build_capability_tree,
    changes_by_capability,
    dispositions_group,
    load_dispositions,
    provenance_lines,
    requirement_count,
)

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    """A program whose specs repo has capabilities, changes and a ledger."""
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\nspecs:\n  path: ../specs\n", encoding="utf-8"
    )
    openspec = tmp_path / "specs" / "openspec"
    (openspec / "specs").mkdir(parents=True)
    (openspec / "changes" / "archive").mkdir(parents=True)
    return bus.Program(name="demo", root=root), openspec


def _capability(openspec, name: str, requirements: int = 0):
    d = openspec / "specs" / name
    d.mkdir(parents=True, exist_ok=True)
    body = f"# {name} Specification\n\n## Requirements\n\n"
    body += "".join(f"### Requirement: r{i} SHALL work\n\ntext\n\n" for i in range(requirements))
    (d / "spec.md").write_text(body, encoding="utf-8")


def _change(openspec, name: str, caps: list[str], *, archived: bool = False, openspec_yaml=""):
    base = openspec / "changes" / ("archive" if archived else "")
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".openspec.yaml").write_text(openspec_yaml or "stage: authored\n", encoding="utf-8")
    for c in caps:
        (d / "specs" / c).mkdir(parents=True, exist_ok=True)
        (d / "specs" / c / "spec.md").write_text("## ADDED Requirements\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# 3.2 — requirement counts


def test_requirement_count(program):
    _, openspec = program
    _capability(openspec, "cap-a", requirements=3)
    assert requirement_count(openspec / "specs" / "cap-a" / "spec.md") == 3


def test_requirement_count_zero_and_missing(program):
    _, openspec = program
    _capability(openspec, "empty", requirements=0)
    assert requirement_count(openspec / "specs" / "empty" / "spec.md") == 0
    assert requirement_count(openspec / "specs" / "nope" / "spec.md") == 0  # no crash


def test_capability_root_reports_its_count(program):
    prog, openspec = program
    _capability(openspec, "cap-a", requirements=2)
    (root,) = build_capability_tree(prog)
    assert root.kind == "capability" and root.id == "cap-a"
    assert "2 requirements" in root.title


def test_singular_requirement_wording(program):
    prog, openspec = program
    _capability(openspec, "one", requirements=1)
    (root,) = build_capability_tree(prog)
    assert "1 requirement" in root.title and "requirements" not in root.title


# ---------------------------------------------------------------------------
# 3.2 — the changes that shaped a capability


def test_changes_are_joined_by_their_own_deltas(program):
    """The delta IS the edge — `specs/<cap>/` inside a change dir — so the join
    needs no separate index to drift from."""
    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "open-one", ["cap-a"])
    _change(openspec, "2026-01-01-old-one", ["cap-a"], archived=True)
    shaped = changes_by_capability(openspec / "changes")
    assert sorted(shaped["cap-a"]) == [("2026-01-01-old-one", False), ("open-one", True)]


def test_shaping_changes_render_as_references_not_children(program):
    """D4: a change's structural home is the value spine; here it is a pointer."""
    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "2026-01-01-shaper", ["cap-a"], archived=True)
    (root,) = build_capability_tree(prog)
    (ref,) = root.children
    assert ref.kind == "reference"
    assert ref.ref_kind == "change" and ref.ref_id == "2026-01-01-shaper"
    assert ref.children == []  # a reference carries nothing to expand


def test_open_deltas_are_marked_and_counted(program):
    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "still-moving", ["cap-a"])
    (root,) = build_capability_tree(prog)
    assert "1 open delta" in root.title
    assert "[open delta]" in root.children[0].id


def test_open_deltas_sort_before_history(program):
    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "2026-01-01-aaa-archived", ["cap-a"], archived=True)
    _change(openspec, "zzz-open", ["cap-a"])
    (root,) = build_capability_tree(prog)
    assert "zzz-open" in root.children[0].id  # live part first, despite the name


def test_a_capability_shaped_twice_keeps_both_distinguishable(program):
    """REGRESSION: stripping the archive date prefix collapsed two DISTINCT
    changes into one indistinguishable label — `agent-credential-access` was
    genuinely shaped twice on the live repo (2026-08-26 and 2026-09-04) and both
    rendered as the same line, with an ambiguous navigation target."""
    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "2026-08-26-cap-a", ["cap-a"], archived=True)
    _change(openspec, "2026-09-04-cap-a", ["cap-a"], archived=True)
    (root,) = build_capability_tree(prog)
    labels = [c.id for c in root.children]
    assert labels == ["2026-08-26-cap-a", "2026-09-04-cap-a"]
    assert len(set(c.ref_id for c in root.children)) == 2  # two distinct targets


def test_a_capability_with_no_changes_still_renders(program):
    prog, openspec = program
    _capability(openspec, "untouched", requirements=1)
    (root,) = build_capability_tree(prog)
    assert root.children == [] and "open delta" not in root.title


def test_no_specs_dir_means_no_lens(tmp_path):
    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("project: p\nversion: '1.0'\n", encoding="utf-8")
    assert build_capability_tree(bus.Program(name="p", root=root)) == []


def test_changes_dir_without_specs_subdirs_is_skipped(program):
    prog, openspec = program
    _capability(openspec, "cap-a")
    (openspec / "changes" / "no-deltas").mkdir(parents=True)
    assert changes_by_capability(openspec / "changes") == {}


# ---------------------------------------------------------------------------
# 3.2 — the lens is reachable through the builder


def test_the_capability_lens_is_built_by_the_shared_builder(program):
    from otaman_cli.console.tree import LENS_CAPABILITY, build_artifact_tree

    prog, openspec = program
    _capability(openspec, "cap-a", requirements=1)
    roots = build_artifact_tree(prog, lens=LENS_CAPABILITY)
    assert [r.id for r in roots if r.kind == "capability"] == ["cap-a"]


def test_one_parent_holds_in_the_capability_lens(program):
    """D4 applies per LENS: the dedupe runs here too."""
    from otaman_cli.console.tree import LENS_CAPABILITY, build_artifact_tree

    prog, openspec = program
    _capability(openspec, "cap-a")
    _capability(openspec, "cap-b")
    _change(openspec, "touches-both", ["cap-a", "cap-b"])
    roots = build_artifact_tree(prog, lens=LENS_CAPABILITY)
    seen = [(n.kind, n.id) for n in roots]
    assert len(seen) == len(set(seen))
    # the change references BOTH capabilities — the many-to-many case D4 exists for
    assert all(len(r.children) == 1 for r in roots if r.kind == "capability")


# ---------------------------------------------------------------------------
# 3.4 — provenance, not a tree level


def test_provenance_comes_from_the_changes_own_record(program):
    lines = provenance_lines(
        {"requested_by": "spec-agent (SCR 2026...)", "approved_by": 'roman ("go")'}
    )
    assert lines == ["requested by: spec-agent (SCR 2026...)", 'approved by: roman ("go")']


def test_provenance_is_empty_when_unrecorded(program):
    assert provenance_lines({}) == []
    assert provenance_lines({"requested_by": "   "}) == []
    assert provenance_lines({"requested_by": None}) == []


def test_change_detail_exposes_provenance(program):
    from otaman_cli.console.lifecycle import change_detail

    prog, openspec = program
    _change(
        openspec,
        "minted",
        [],
        openspec_yaml="stage: authored\nrequested_by: spec-agent SCR-1\napproved_by: roman\n",
    )
    d = change_detail(prog, "minted")
    assert d.get("provenance") == ["requested by: spec-agent SCR-1", "approved by: roman"]


def test_archived_changes_resolve_so_references_are_not_dead_ends(program):
    """REGRESSION: `change_detail` was active-only, which made every capability
    reference to an archived change point nowhere — and most changes are
    archived."""
    from otaman_cli.console.lifecycle import change_detail

    prog, openspec = program
    _change(openspec, "2026-01-01-gone", [], archived=True, openspec_yaml="stage: archived\n")
    assert change_detail(prog, "2026-01-01-gone").get("name") == "2026-01-01-gone"
    assert change_detail(prog, "gone").get("name") == "gone"  # bare slug resolves too


def test_bare_slug_picks_the_most_recent_shaping(program):
    from otaman_cli.console.lifecycle import change_detail

    prog, openspec = program
    _change(openspec, "2026-01-01-c", [], archived=True, openspec_yaml="stage: archived\n")
    _change(openspec, "2026-06-01-c", [], archived=True, openspec_yaml="triage: active\n")
    d = change_detail(prog, "c")
    assert d.get("triage") == "active"  # the newer one


# ---------------------------------------------------------------------------
# 3.4 — the dispositions ledger


def test_ledger_renders_as_one_collapsed_group(program):
    prog, openspec = program
    (openspec / "dispositions.yaml").write_text(
        "- approval: a1\n  title: absorbed thing\n  disposition: absorbed\n"
        "  absorbed-into: other-change\n"
        "- approval: a2\n  title: withdrawn thing\n  disposition: withdrawn\n",
        encoding="utf-8",
    )
    group = dispositions_group(prog)
    assert group is not None
    assert group.kind == "group" and group.closed is True  # history, not live work
    assert group.title == "2"
    labels = [c.id for c in group.children]
    assert "[absorbed] absorbed thing → other-change" in labels
    assert "[withdrawn] withdrawn thing" in labels


def test_no_ledger_means_no_group(program):
    prog, _ = program
    assert dispositions_group(prog) is None


def test_unparseable_ledger_is_tolerated(program):
    prog, openspec = program
    (openspec / "dispositions.yaml").write_text("a: b: c\n", encoding="utf-8")
    assert load_dispositions(prog) == []
    assert dispositions_group(prog) is None


def test_malformed_ledger_rows_are_skipped(program):
    prog, openspec = program
    (openspec / "dispositions.yaml").write_text(
        "- just a string\n- approval: a1\n  disposition: absorbed\n", encoding="utf-8"
    )
    assert len(load_dispositions(prog)) == 1


def test_the_ledger_is_appended_to_the_capability_lens(program):
    from otaman_cli.console.tree import LENS_CAPABILITY, build_artifact_tree

    prog, openspec = program
    _capability(openspec, "cap-a")
    (openspec / "dispositions.yaml").write_text(
        "- approval: a1\n  title: t\n  disposition: absorbed\n", encoding="utf-8"
    )
    roots = build_artifact_tree(prog, lens=LENS_CAPABILITY)
    assert roots[-1].kind == "group" and "dispositions" in roots[-1].id


def test_no_proposal_is_ever_a_tree_level(program):
    """D5 stated as a test: nothing in any lens has kind 'proposal'."""
    from otaman_cli.console.tree import LENSES, build_artifact_tree

    prog, openspec = program
    _capability(openspec, "cap-a")
    _change(openspec, "c", ["cap-a"])

    def kinds_in(roots) -> set[str]:
        found: set[str] = set()

        def walk(ns):
            for n in ns:
                found.add(n.kind)
                walk(n.children)

        walk(roots)
        return found

    for lens in LENSES:
        if lens == "lifecycle":
            continue  # a table, not a tree
        assert "proposal" not in kinds_in(build_artifact_tree(prog, lens=lens)), lens


# ---------------------------------------------------------------------------
# the screen


@_textual
def test_activating_a_capability_opens_its_spec(program):
    from otaman_cli.console.app import CapabilityDetailScreen, OtamanConsole, TreeScreen
    from otaman_cli.console.tree import LENS_CAPABILITY, TreeNode

    prog, openspec = program
    _capability(openspec, "cap-a", requirements=2)

    async def go():
        app = OtamanConsole([prog], search_root=prog.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(prog, lens=LENS_CAPABILITY))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            from types import SimpleNamespace

            node = TreeNode(kind="capability", id="cap-a", title="")
            app.screen.on_tree_node_selected(SimpleNamespace(node=SimpleNamespace(data=node)))
            await pilot.pause()
            assert isinstance(app.screen, CapabilityDetailScreen)
            assert app.screen.capability == "cap-a"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_disposition_row_says_it_minted_nothing(program):
    from otaman_cli.console.app import OtamanConsole, TreeScreen
    from otaman_cli.console.tree import TreeNode

    prog, _ = program
    notes: list[str] = []

    async def go():
        app = OtamanConsole([prog], search_root=prog.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(TreeScreen(prog))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            type(app).notify = lambda self, msg, **kw: notes.append(str(msg))
            from types import SimpleNamespace

            node = TreeNode(kind="disposition", id="[absorbed] t", title="")
            app.screen.on_tree_node_selected(SimpleNamespace(node=SimpleNamespace(data=node)))
            await pilot.pause()
            assert notes and "no change was minted" in notes[0]
            await app.action_quit()

    asyncio.run(go())
