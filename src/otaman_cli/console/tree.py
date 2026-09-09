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
    blocked_by: str | None = None
    next_actor: str | None = None
    closed: bool = False
    dormant: bool = False
    marker: str = ""  # e.g. ★ for the chosen solution
    children: list[TreeNode] = field(default_factory=list)

    @property
    def label(self) -> str:
        bits = [self.id or self.title]
        if self.id and self.title and self.title != self.id:
            bits.append(self.title)
        line = " ".join(bits)
        tail = []
        if self.status:
            tail.append(f"[{self.status}]")
        if self.marker:
            tail.append(self.marker)
        if self.blocked_by:
            tail.append(f"BLOCKED by {self.blocked_by}")
        return f"{line}   {' '.join(tail)}".rstrip()


def _extract_outcome_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    m = _JTBD_RE.search(raw)
    return m.group(0).upper() if m else None


def _priority_rank(priority: str | None) -> int:
    return _PRIORITY_RANK.get(priority or "", 99)


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
    return TreeNode(
        kind="change",
        id=row.name,
        title="",
        status=row.state or row.stage or "",
        blocked_by=blocked.get(row.name),
        next_actor=row.next_actor,
        closed=(row.triage == "absorbed"),
        dormant=(row.triage == "dormant"),
    )


def build_artifact_tree(program: Program, *, show_closed: bool = False) -> list[TreeNode]:
    """The linked artifact tree roots. Outcome-first when registries are enabled;
    otherwise a flat changes tree. *show_closed* re-includes closed/absorbed
    items (hidden by default); dormant changes always sort last."""
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
        return roots

    roots: list[TreeNode] = []
    linked_changes: set[str] = set()
    for outcome in outcomes.outcomes:
        closed = outcome.status in _CLOSED_OUTCOME
        node = TreeNode(
            kind="outcome",
            id=outcome.id,
            title=getattr(getattr(outcome, "statement", None), "incremental_outcome", "") or "",
            status=str(outcome.status),
            priority=str(outcome.priority),
            closed=closed,
        )
        chosen = getattr(outcome, "chosen_solution", None)
        if solutions is not None:
            for sol in solutions.for_outcome(outcome.id):
                node.children.append(
                    TreeNode(
                        kind="solution",
                        id=sol.id,
                        title=getattr(sol, "description", "") or "",
                        status=str(sol.status),
                        priority=str(outcome.priority),
                        closed=(str(sol.status) in _CLOSED_SOLUTION),
                        marker="★" if chosen and sol.id == chosen else "",
                    )
                )
        for name, oid in change_outcome.items():
            if oid == outcome.id:
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
        roots.append(TreeNode(kind="group", id="(unlinked changes)", title="", children=orphans))
    return roots


__all__ = ["TreeNode", "build_artifact_tree", "registries_enabled"]
