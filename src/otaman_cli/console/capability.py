"""The capability lens and proposal provenance (console-ia-consolidation 3.2/3.4).

The capability spine answers a different question from the value spine: not
"why are we doing this" but "what does the system do, and what shaped it".
Its roots are the accumulated capability specs under ``openspec/specs/``; the
changes that shaped each one attach as REFERENCE lines, never as children.

That is forced rather than stylistic (D3/D4). change ↔ capability is
many-to-many — measured on this program: one change carries deltas for 6
capabilities, one capability was shaped by 9 changes — so neither may nest
inside the other without drawing nodes twice. A change's structural home is the
value spine, under its chosen solution; here it is a pointer.

3.4 — an SCR is an EDGE with three fates, none of them a tree level (D5):
pending (a human decision → Messages), approved (it minted a change →
provenance on that change), unminted (absorbed or withdrawn → the dispositions
ledger, rendered as one collapsed group). Rendering an approved proposal as a
persistent node would show an artifact with no independent life.

Everything here is a read-only join over files on disk, and every failure
degrades to "no capability lens" rather than raising into the TUI.
"""

from __future__ import annotations

import re
from pathlib import Path

from otaman_cli.console.tree import TreeNode

_REQUIREMENT_RE = re.compile(r"^###\s+Requirement:", re.MULTILINE)


def _specs_dirs(program) -> tuple[Path | None, Path | None]:
    """``(specs_root/openspec/specs, .../changes)`` or ``(None, None)``."""
    try:
        from otaman_cli.console.lifecycle import _specs_changes_dir

        changes = _specs_changes_dir(program)
        if changes is None:
            return None, None
        specs = changes.parent / "specs"
        return (specs if specs.is_dir() else None), changes
    except Exception:  # noqa: BLE001 - unresolvable specs repo → no lens
        return None, None


def requirement_count(spec_file: Path) -> int:
    """How many ``### Requirement:`` headings a capability spec declares."""
    try:
        return len(_REQUIREMENT_RE.findall(spec_file.read_text(encoding="utf-8")))
    except OSError:
        return 0


def changes_by_capability(changes_dir: Path) -> dict[str, list[tuple[str, bool]]]:
    """capability -> [(change_name, is_open), ...].

    A change declares which capabilities it touches by carrying
    ``specs/<capability>/spec.md`` — the delta itself is the edge, so the join
    needs no separate index to drift from. ACTIVE changes are open deltas;
    archived ones are history that shaped the capability.
    """
    out: dict[str, list[tuple[str, bool]]] = {}

    def scan(base: Path, *, is_open: bool) -> None:
        try:
            entries = sorted(p for p in base.iterdir() if p.is_dir() and p.name != "archive")
        except OSError:
            return
        for change in entries:
            specs = change / "specs"
            if not specs.is_dir():
                continue
            try:
                caps = sorted(p.name for p in specs.iterdir() if p.is_dir())
            except OSError:
                continue
            # Keep the DATED name for archived changes. Stripping the prefix
            # collapsed distinct shapings into one indistinguishable label —
            # `agent-credential-access` was genuinely shaped twice, by
            # 2026-08-26-… and 2026-09-04-…, and both rendered as the same line.
            for cap in caps:
                out.setdefault(cap, []).append((change.name, is_open))

    scan(changes_dir, is_open=True)
    archive = changes_dir / "archive"
    if archive.is_dir():
        scan(archive, is_open=False)
    return out


def build_capability_tree(program) -> list[TreeNode]:
    """Capability roots, each listing its requirement count and the changes that
    shaped it as reference lines (3.2).

    Open deltas are marked, because "what is still moving against this
    capability" is the question a reader has when looking at one.
    """
    specs_dir, changes_dir = _specs_dirs(program)
    if specs_dir is None:
        return []
    shaped = changes_by_capability(changes_dir) if changes_dir else {}

    roots: list[TreeNode] = []
    try:
        caps = sorted(p for p in specs_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    for cap in caps:
        spec_file = cap / "spec.md"
        count = requirement_count(spec_file) if spec_file.is_file() else 0
        entries = shaped.get(cap.name, [])
        open_deltas = [n for n, is_open in entries if is_open]
        node = TreeNode(
            kind="capability",
            id=cap.name,
            title=f"{count} requirement{'s' if count != 1 else ''}"
            + (f" · {len(open_deltas)} open delta" if open_deltas else ""),
        )
        # Reference lines, not children (D4): a change's structural home is the
        # value spine. Open deltas first — they are the live part.
        for name, is_open in sorted(entries, key=lambda e: (not e[1], e[0])):
            node.children.append(
                TreeNode(
                    kind="reference",
                    id=f"{name}{'  [open delta]' if is_open else ''}",
                    title="",
                    ref_kind="change",
                    ref_id=name,
                )
            )
        roots.append(node)
    return roots


# ---------------------------------------------------------------------------
# 3.4 — proposal provenance


def provenance_lines(openspec: dict) -> list[str]:
    """The SCR stems that produced a change, from its own ``.openspec.yaml``.

    ``requested_by`` / ``approved_by`` already carry them, so provenance needs no
    new storage — which is the point of D5: the approved proposal is an edge the
    change already records, not an artifact with a life of its own.
    """
    out: list[str] = []
    for key in ("requested_by", "approved_by"):
        value = openspec.get(key)
        if isinstance(value, str) and value.strip():
            out.append(f"{key.replace('_', ' ')}: {value.strip()}")
    return out


def load_dispositions(program) -> list[dict]:
    """The dispositions ledger — approvals that never became change folders."""
    _, changes_dir = _specs_dirs(program)
    if changes_dir is None:
        return []
    path = changes_dir.parent / "dispositions.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent/unparseable ledger → nothing to show
        return []
    return [d for d in data or [] if isinstance(d, dict)]


def dispositions_group(program) -> TreeNode | None:
    """The ledger as ONE collapsed group (3.4), or None when empty.

    A group, not a tree level: these approvals minted nothing, so they have no
    artifact to hang under — but they are also not invisible, because "approved
    and then absorbed" is a fate a reader needs to be able to find.
    """
    rows = load_dispositions(program)
    if not rows:
        return None
    node = TreeNode(
        kind="group",
        id="(dispositions — approved, never minted a change)",
        title=f"{len(rows)}",
        closed=True,  # collapsed by default: history, not live work
    )
    for row in rows:
        title = str(row.get("title") or row.get("approval") or "?")
        fate = str(row.get("disposition") or "?")
        into = row.get("absorbed-into")
        detail = f" → {into}" if into else ""
        node.children.append(TreeNode(kind="disposition", id=f"[{fate}] {title}{detail}", title=""))
    return node


__all__ = [
    "build_capability_tree",
    "changes_by_capability",
    "dispositions_group",
    "load_dispositions",
    "provenance_lines",
    "requirement_count",
]
