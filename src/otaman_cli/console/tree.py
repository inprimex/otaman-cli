"""Textual-free linked artifact tree (console-ux-redesign wave 1, task 1.3 / D2).

Outcomes/JTBDs, their solutions, and the spec changes that implement them join
into ONE tree instead of four disconnected lists (S4). Roots are OUTCOME-FIRST
where the program enables the outcomes registry, and a simplified changes-rooted
structure otherwise (the adaptive-and-honest contract, D2). Siblings sort by the
priority inherited from the linked outcome; a change reads BLOCKED naming the
blocker (from live agent presence). Closed items (Done/Retired outcomes,
Discarded solutions, absorbed changes) are hidden by default with dormant last.

Everything here is a read-only join over existing readers (registries loaders,
the lifecycle derivation, the status backend). The links are deliberately
lenient — the change→outcome value is free text with the id as a prefix, and
SCR→change is fuzzy — so every join tolerates misses and degrades to a flat
changes tree rather than failing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from otaman_cli.console.bus import Program

#: outcome ids appear in change .openspec.yaml `outcome:` as a free-text prefix.
_JTBD_RE = re.compile(r"JTBD-\d+(?:-[a-z0-9-]+)?", re.IGNORECASE)

_CLOSED_OUTCOME = {"Done", "Retired"}
_CLOSED_SOLUTION = {"Discarded"}
#: priority sort — P0 first; unknown/none sinks to the bottom.
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass
class TreeNode:
    kind: str  # "outcome" | "solution" | "change" | "group"
    id: str
    title: str
    status: str = ""
    priority: str | None = None
    created: str = ""  # outcome created stamp (date column, tree-view-polish 1.1)
    blocked_by: str | None = None
    next_actor: str | None = None
    closed: bool = False
    #: Render this branch COLLAPSED. Distinct from ``closed``, which HIDES a node
    #: until `f`: a collapsed node is visible, just not expanded. They were
    #: conflated — `dispositions_group` set ``closed=True`` and commented it
    #: "collapsed by default", so it rendered expanded (every branch is added
    #: with ``expand=True``) and would have vanished the moment anything
    #: filtered it. D7's sideways growth needs the real thing.
    collapsed: bool = False
    dormant: bool = False
    grayed: bool = False  # decided-out sibling solution (tree-view-polish 1.3)
    #: This row is waiting on the human at the console (1.4/D3). Derived from
    #: the EXISTING next-actor computation and the authored-changes set — the
    #: same sets `v` and `otaman ratify` act on — so the marker, the header
    #: count and `:a` cannot disagree with the action sitting beside them.
    awaiting: bool = False
    marker: str = ""  # e.g. ★ for the chosen solution
    pm_sync_id: str | None = None  # linked issue/ticket id (S10)
    children: list[TreeNode] = field(default_factory=list)
    #: For ``kind == "reference"`` (D4): the node this line NAVIGATES to. A
    #: reference never expands in place and never carries children — it is how a
    #: second relation is shown without drawing the node twice.
    ref_kind: str = ""
    ref_id: str = ""

    _HUGE = 1 << 30  # "no clip" sentinel for the full label

    def row_segments(self, max_title: int = 48) -> list[tuple[str, str]]:
        """The row as ``(text, rich-style)`` segments (tree-view-polish 1.1/1.2).

        Row shape is ``id   created | p<N> | <status> | title`` for outcomes and
        ``id   p<N> | <status> | title`` for solutions — identity columns first,
        the free-text title last and clipped to *max_title* so long titles never
        overflow (Roman feedback; overflow hijacks ←/→ into horizontal scroll).
        Enum values render as short lowercase words each in its palette color;
        a grayed (decided-out) row renders wholly in the gray style. Styles come
        from the one shared palette — no per-screen colors."""
        from otaman_cli.console.palette import (
            GRAY_STYLE,
            priority_style,
            short_priority,
            short_status,
            status_style,
        )

        def st(style: str) -> str:
            return GRAY_STYLE if self.grayed else style

        if self.kind == "reference":
            # A reference LINE, not a node: prefixed so it reads as a pointer,
            # and it never carries children to expand (D4).
            return [("→ ", st("")), (self.id or self.title, st("italic"))]
        # D4 — a rendered row is never visually EMPTY. Two ways it could be:
        #
        # 1. `id` and `title` both blank made the first segment `("", "bold")`,
        #    i.e. a blank line in the tree. The builders should not emit such a
        #    node, but the rule is enforced HERE so no future builder can
        #    reintroduce it: the kind is always known, so it is always sayable.
        # 2. A decided-out sibling rendered WHOLLY in `dim`. `dim` is relative
        #    to the theme's foreground, and in a low-contrast terminal the whole
        #    row reads as blank — which is exactly the "closed solutions are
        #    blank lines" Roman reported. The row's own IDENTITY stays legible;
        #    everything after it still reads as decided-out, so the signal is
        #    kept without costing the reader the row.
        anchor = self.id or self.title or f"({self.kind or 'item'})"
        segs: list[tuple[str, str]] = [(anchor, "bold")]
        cols: list[tuple[str, str]] = []
        if self.kind == "outcome" and self.created:
            cols.append((str(self.created)[:10], st("")))
        if self.priority:
            cols.append((short_priority(self.priority), st(priority_style(self.priority))))
        if self.status:
            cols.append((short_status(self.status), st(status_style(self.status))))
        title = self.title if (self.title and self.title != self.id) else ""
        if title and len(title) > max_title:
            title = title[: max_title - 1].rstrip() + "…"
        if title:
            cols.append((title, st("")))
        if cols:
            segs.append(("   ", ""))
            for i, seg in enumerate(cols):
                if i:
                    segs.append((" | ", st("")))
                segs.append(seg)
        tail: list[tuple[str, str]] = []
        if self.awaiting:
            # Its own visible token, not a color: "awaiting you" is the one
            # thing a reader scans for, and a row that says it only by being a
            # slightly different shade says it to nobody (the same lesson as
            # the dim-row defect in 1.2).
            tail.append(("◀ you", "bold yellow" if not self.grayed else GRAY_STYLE))
        if self.marker:
            tail.append((self.marker, st("")))
        if self.pm_sync_id:
            tail.append((f"#{self.pm_sync_id}", st("")))
        if self.blocked_by:
            tail.append(
                (f"BLOCKED by {self.blocked_by}", st("red") if not self.grayed else GRAY_STYLE)
            )
        for seg in tail:
            segs.extend([("   ", ""), seg])
        return segs

    @property
    def label(self) -> str:
        return "".join(t for t, _ in self.row_segments(max_title=self._HUGE)).rstrip()

    def display_label(self, max_title: int = 48) -> str:
        """The plain-text row with the title clipped to *max_title* (see
        :meth:`row_segments`)."""
        return "".join(t for t, _ in self.row_segments(max_title=max_title)).rstrip()


#: The three arrangements of the SAME objects (console-ia-consolidation D2).
#: Only the arrangement differs — a lens is not a filter and not a new dataset.
LENS_VALUE = "value"  # outcomes → solutions → changes: why, and where is it?
LENS_CAPABILITY = "capability"  # capability specs ← the changes that shaped them
LENS_LIFECYCLE = "lifecycle"  # the flat change table: what moves, what is stuck?

LENSES = (LENS_VALUE, LENS_CAPABILITY, LENS_LIFECYCLE)

#: Human-facing names, used in the banner so the current lens is never guessed.
LENS_LABEL = {
    LENS_VALUE: "value",
    LENS_CAPABILITY: "capability",
    LENS_LIFECYCLE: "lifecycle",
}

#: What each lens ANSWERS (1.2 orientation header). The three arrangements were
#: documented only as source comments beside the constants above — invisible to
#: the person looking at the screen, who has to infer from the shape what
#: question they are looking at the answer to. A lens is a question; saying
#: which one costs one line.
LENS_ORIENTATION = {
    LENS_VALUE: "why we are doing this, and where it stands — outcomes → solutions → changes",
    LENS_CAPABILITY: "what the system does, and what shaped it — specs ← the changes that hit them",
    LENS_LIFECYCLE: "what is moving and what is stuck — every change, flat",
}


def lens_orientation(lens: str) -> str:
    """The one-line "what question does this lens answer" for *lens*."""
    return LENS_ORIENTATION.get(lens, "")


def context_line(node: TreeNode, *, refs: list[str] | None = None) -> str:
    """The per-row context line (1.2): what this row IS, and what it points at.

    The row itself is identity columns plus a clipped title — deliberately
    narrow, because a wide row hijacks ←/→ into horizontal scroll. That leaves
    nowhere for the description or the JTBD/SOL/ADR/SCR references, which is
    what a reader needs to decide whether this is the row they want.

    Returns "" when there is genuinely nothing to add, so callers can skip the
    line rather than render an empty one (D4 applies here too: no empty rows).
    """
    parts: list[str] = []
    title = (node.title or "").strip()
    # Only when the row CLIPPED it — repeating a fully-visible title is noise.
    if title and title != node.id:
        parts.append(title)
    for ref in refs or []:
        ref = str(ref).strip()
        if ref and ref not in parts:
            parts.append(ref)
    if node.blocked_by:
        parts.append(f"blocked by {node.blocked_by}")
    if node.next_actor:
        parts.append(f"next: {node.next_actor}")
    return " · ".join(parts)


def next_lens(current: str) -> str:
    """The next lens in the cycle — one key toggles through all three (3.1)."""
    try:
        return LENSES[(LENSES.index(current) + 1) % len(LENSES)]
    except ValueError:
        return LENS_VALUE


def dedupe_one_parent(roots: list[TreeNode]) -> list[TreeNode]:
    """Enforce D4: a node appears exactly ONCE per lens, under one parent.

    Walks depth-first and drops any node whose (kind, id) has already been drawn,
    keeping the first — structural — occurrence. Reference lines are exempt:
    they are the sanctioned way a second relation shows up, and they navigate
    rather than expand, so they carry no children to double-count.

    Why it matters concretely: change ↔ capability is many-to-many (measured: 6
    capabilities on one change, 9 changes on one capability). Drawing a node
    under every relation would make collapse state meaningless and every count
    a lie — the same node collapsed in one place and expanded in another, summing
    to more artifacts than exist.
    """
    seen: set[tuple[str, str]] = set()

    def walk(nodes: list[TreeNode]) -> list[TreeNode]:
        kept: list[TreeNode] = []
        for n in nodes:
            if n.kind == "reference":
                kept.append(n)
                continue
            key = (n.kind, n.id)
            if key in seen:
                continue
            seen.add(key)
            n.children = walk(n.children)
            kept.append(n)
        return kept

    return walk(list(roots))


def _extract_outcome_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    m = _JTBD_RE.search(raw)
    return m.group(0).upper() if m else None


def _priority_rank(priority: str | None) -> int:
    return _PRIORITY_RANK.get(priority or "", 99)


def _enum_value(x: object) -> str:
    """The canonical value string for a status/priority — the enum ``.value``, so
    a row never leaks a raw ``OutcomeStatus.APPROVED`` repr (tree-view-polish)."""
    return "" if x is None else str(getattr(x, "value", x))


def registries_enabled(program: Program) -> bool:
    """True when the program enables the outcomes registry AND its file resolves —
    the gate for outcome-first vs. the simplified changes-rooted tree (D2)."""
    try:
        from otaman_cli.registries.loader import resolve_registry_path
        from otaman_cli.registries.platform_ext import load_program_extensions

        ext = load_program_extensions(program.root / "platform.yaml")
        if not getattr(ext.processes.outcomes, "enabled", False):
            return False
        path = resolve_registry_path(program.root, "outcomes")
        return bool(path and path.is_file())
    except Exception:  # noqa: BLE001 - any failure → treat registries as absent
        return False


FALLBACK_NOTICE = (
    "outcomes registry failed to load — showing changes only; "
    "run `otaman outcome list` for the error"
)


def tree_fallback_notice(program: Program) -> str | None:
    """The loud one-liner for the tree header when the program ENABLES the outcomes
    registry but the file won't load/validate, so the flat changes-only fallback is
    never silent (cofounder-agent's Roman requirement; kin to the silent-approval-
    loss lesson, JTBD-125). None when registries are off/absent or loaded fine —
    a missing file is a genuine absence, not a failure."""
    if not registries_enabled(program):
        return None
    outcomes, _ = _load_registries(program)
    return None if outcomes is not None else FALLBACK_NOTICE


def _load_registries(program: Program):
    """(outcomes, solutions) validated registries, or (None, None) when absent."""
    try:
        from otaman_cli.registries.loader import resolve_registry_path
        from otaman_cli.registries.outcomes import load_outcomes
        from otaman_cli.registries.solutions import load_solutions

        op = resolve_registry_path(program.root, "outcomes")
        sp = resolve_registry_path(program.root, "solutions")
        outcomes = load_outcomes(op) if op and op.is_file() else None
        solutions = load_solutions(sp) if sp and sp.is_file() else None
        return outcomes, solutions
    except Exception:  # noqa: BLE001
        return None, None


def _blocked_map(program: Program) -> dict[str, str]:
    """change-name -> blocking agent, from live presence (state == blocked)."""
    try:
        from otaman_cli.status import State, get_backend, is_agent_presence_enabled

        if not is_agent_presence_enabled(program.root):
            return {}
        out: dict[str, str] = {}
        for r in get_backend(program.root).read_all():
            if r.state == State.BLOCKED and r.change and r.blocked_by:
                out[r.change] = r.blocked_by
        return out
    except Exception:  # noqa: BLE001
        return {}


def _change_rows(program: Program):
    try:
        from otaman_cli.console.lifecycle import _specs_changes_dir
        from otaman_cli.lifecycle import derive_change_table

        active_dir, _ = program.bus_paths()
        return derive_change_table(
            changes_dir=_specs_changes_dir(program),
            bus_active_dir=active_dir if active_dir.is_dir() else None,
        )
    except Exception:  # noqa: BLE001
        return []


def _change_outcome_id(program: Program, change_name: str) -> str | None:
    """The outcome id linked from a change's .openspec.yaml `outcome:` (free text)."""
    try:
        from otaman_core.spec_lifecycle import read_openspec

        from otaman_cli.console.lifecycle import _specs_changes_dir

        changes = _specs_changes_dir(program)
        if changes is None:
            return None
        data = read_openspec(changes / change_name / ".openspec.yaml")
        return _extract_outcome_id(data.get("outcome"))
    except Exception:  # noqa: BLE001
        return None


def _change_node(program: Program, row, blocked: dict[str, str]) -> TreeNode:
    pm_id = None
    try:
        from otaman_cli.console.lifecycle import _specs_changes_dir
        from otaman_cli.console.metadata import read_pm_sync

        changes = _specs_changes_dir(program)
        if changes is not None:
            pm_id, _ = read_pm_sync(changes / row.name)
    except Exception:  # noqa: BLE001 - pm-sync id is decorative on the row
        pm_id = None
    return TreeNode(
        kind="change",
        id=row.name,
        title="",
        status=row.state or row.stage or "",
        blocked_by=blocked.get(row.name),
        next_actor=row.next_actor,
        closed=(row.triage == "absorbed"),
        dormant=(row.triage == "dormant"),
        pm_sync_id=pm_id,
    )


def _extra_registry_roots(program: Program) -> list[TreeNode]:
    """Collapsed sibling roots for enabled non-spine registries (5.1 / D7).

    Appended AFTER ``dedupe_one_parent``: these are a separate namespace of
    roots, not another view of a spine node, so they must never be dropped as a
    "duplicate" of an outcome that happens to share a key. Imported lazily —
    ``extra_registries`` imports ``TreeNode`` from here.
    """
    try:
        from otaman_cli.console.extra_registries import registry_roots

        return registry_roots(program)
    except Exception:  # noqa: BLE001 - a bad process block never breaks the tree
        return []


def build_artifact_tree(
    program: Program, *, show_closed: bool = False, lens: str = LENS_VALUE
) -> list[TreeNode]:
    """The linked artifact tree roots. Outcome-first when registries are enabled;
    otherwise a flat changes tree. *show_closed* re-includes closed/absorbed
    items (hidden by default); dormant changes always sort last."""
    # 3.2 — the capability spine is a different arrangement of the same
    # objects, built from the accumulated specs rather than the registries.
    if lens == LENS_CAPABILITY:
        from otaman_cli.console.capability import build_capability_tree, dispositions_group

        roots = dedupe_one_parent(build_capability_tree(program))
        ledger = dispositions_group(program)
        if ledger is not None:
            roots.append(ledger)
        return roots

    rows = _change_rows(program)
    blocked = _blocked_map(program)
    outcomes, solutions = _load_registries(program) if registries_enabled(program) else (None, None)

    # change-name -> node, and the outcome id each change links to
    change_nodes = {r.name: _change_node(program, r, blocked) for r in rows}
    change_outcome = {r.name: _change_outcome_id(program, r.name) for r in rows}

    def _visible(node: TreeNode) -> bool:
        return show_closed or not node.closed

    def _sort_changes(nodes: list[TreeNode]) -> list[TreeNode]:
        # dormant last, then by name (stable)
        return sorted(nodes, key=lambda n: (n.dormant, n.id))

    if outcomes is None:
        # simplified: flat changes tree, no empty outcome scaffolding (D2)
        roots = _sort_changes([n for n in change_nodes.values() if _visible(n)])
        return roots + _extra_registry_roots(program)

    roots: list[TreeNode] = []
    linked_changes: set[str] = set()
    for outcome in outcomes.outcomes:
        o_status = _enum_value(outcome.status)
        o_priority = _enum_value(outcome.priority)
        closed = o_status in _CLOSED_OUTCOME
        node = TreeNode(
            kind="outcome",
            id=outcome.id,
            title=getattr(getattr(outcome, "statement", None), "incremental_outcome", "") or "",
            status=o_status,
            priority=o_priority,
            created=str(getattr(outcome, "created", "") or ""),
            closed=closed,
            # COLLAPSED by default: canon says "children windowed to a small
            # scrollable set" (interactive-human-console spec.md:209). Every
            # solution under every outcome was expanded on open — 188 rows on
            # the live program, which is the opposite of windowed. Right-arrow
            # expands the one you want.
            collapsed=True,
        )
        chosen = getattr(outcome, "chosen_solution", None)
        if solutions is not None:
            sols = list(solutions.for_outcome(outcome.id))
            # the "decided" solution — the chosen one, else a Complete one — grays
            # and closes its still-in-play siblings (tree-view-polish 1.3).
            decided = chosen or next(
                (s.id for s in sols if _enum_value(s.status) == "Complete"), None
            )
            for sol in sols:
                s_status = _enum_value(sol.status)
                is_discarded = s_status in _CLOSED_SOLUTION
                grayed = bool(decided) and sol.id != decided and not is_discarded
                node.children.append(
                    TreeNode(
                        kind="solution",
                        id=sol.id,
                        title=getattr(sol, "description", "") or "",
                        status=s_status,
                        priority=o_priority,
                        closed=(is_discarded or grayed),
                        grayed=grayed,
                        marker="★" if chosen and sol.id == chosen else "",
                    )
                )
        for name, oid in change_outcome.items():
            if oid == outcome.id:
                change_nodes[name].priority = o_priority  # inherited (S9)
                node.children.append(change_nodes[name])
                linked_changes.add(name)
        node.children = [c for c in node.children if _visible(c)]
        node.children = _sort_changes([c for c in node.children if c.kind == "change"]) + [
            c for c in node.children if c.kind != "change"
        ]
        if _visible(node):
            roots.append(node)

    # unlinked changes (no outcome, or outcome not in the registry) grouped last
    orphans = _sort_changes(
        [n for name, n in change_nodes.items() if name not in linked_changes and _visible(n)]
    )
    roots.sort(key=lambda n: (_priority_rank(n.priority), n.id))
    if orphans:
        # Collapsed like the rest: this group held 59 changes on the live
        # program, all on screen at open. Its label carries the count so the
        # reader knows what is inside without expanding it.
        roots.append(
            TreeNode(
                kind="group",
                id=f"(unlinked changes — {len(orphans)})",
                title="",
                children=orphans,
                collapsed=True,
            )
        )
    # D4 (3.3): enforced at the BUILDER, not asked of each caller — a node drawn
    # twice makes counts lie and collapse state meaningless.
    return dedupe_one_parent(roots) + _extra_registry_roots(program)


__all__ = [
    "LENS_ORIENTATION",
    "context_line",
    "lens_orientation",
    "LENSES",
    "LENS_CAPABILITY",
    "LENS_LABEL",
    "LENS_LIFECYCLE",
    "LENS_VALUE",
    "TreeNode",
    "dedupe_one_parent",
    "next_lens",
    "build_artifact_tree",
    "registries_enabled",
    "tree_fallback_notice",
    "FALLBACK_NOTICE",
]
