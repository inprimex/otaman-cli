"""Sibling registry roots in Artifacts (console-ia-consolidation 5.1 / D7).

Four more registries are dispatched and unbuilt — risks & assumptions,
user-flows & business-processes, program vocabulary, per-project skills. Under
one-screen-per-artifact-type each would claim a top-level door, taking today's
8 entries to ~16 (console-ia-review §5). D7 caps the doors: a new registry
becomes a COLLAPSED SIBLING ROOT in Artifacts, so the tree grows sideways
inside a fixed root budget rather than growing a door per artifact type.

This module builds the SLOT, not any registry's schema. The four are unbuilt
and their schemas are spec-agent's to define, so nothing here names them or
assumes their shape: ANY process key that is not part of the value spine, is
enabled, and resolves gets rendered. Whatever keys and fields eventually land,
they arrive as sibling roots with no console change — and a registry that never
ships costs nothing. Guessing key names here would have been a guess at a spec
that does not exist yet.

The value spine (outcomes → solutions → changes) and personas are EXCLUDED:
they already have structural homes, and a node drawn twice makes counts lie
(D4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otaman_cli.console.bus import Program
from otaman_cli.console.tree import TreeNode

#: Process keys that own a place in the value spine — never sibling roots.
SPINE_PROCESSES = frozenset({"outcomes", "solutions", "personas"})

#: Process keys that are CONFIG BLOCKS, not registries of entries.
#:
#: `program.processes.skills` carries `{profile, extra}` for the skill-pack
#: resolver — there is no `skills.yaml` of rows behind it, so rendering it as a
#: registry root produced a phantom "skills — enabled · registry home unset"
#: line on every wizard-generated program.
#:
#: The collision is real and NOT settled here: "per-project skills" is also one
#: of the four dispatched REGISTRIES (console-ia-review §5), so the same key
#: would mean two different things. Excluding it keeps the console honest until
#: spec-agent and plugin-agent rule on the naming; reversing this is one line.
NON_REGISTRY_PROCESSES = frozenset({"skills"})

#: How many entries a collapsed root lists before it says how many it withheld.
#: Never a silent truncation: a root that shows 50 of 300 says so on its last
#: line, because a capped list that reads as complete is worse than a long one.
MAX_ENTRIES = 50

#: Keys tried, in order, when an entry names itself. Schema-agnostic on purpose.
_ID_KEYS = ("id", "key", "slug", "name", "term")
_TITLE_KEYS = ("title", "summary", "description", "statement", "definition", "name")


@dataclass(frozen=True)
class ExtraRegistry:
    """One enabled non-spine process and where its file would live."""

    key: str
    path: Path | None  # None when the registry home is unset
    exists: bool = False
    entries: list[tuple[str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        """`user-flows` → `user flows`; the key stays recognisable."""
        return self.key.replace("_", " ").replace("-", " ")


def _enabled(cfg: object) -> bool:
    """Is this process turned on?

    Defaults to True when the key is present but says nothing, matching the
    modelled processes (``enabled: bool = True``) — declaring a process at all
    is the opt-in.
    """
    if isinstance(cfg, dict):
        return bool(cfg.get("enabled", True))
    if isinstance(cfg, bool):
        return cfg
    return cfg is not None


def _configured_path(program: Program, key: str, cfg: object) -> Path | None:
    """Where *key*'s registry file would live, or None when unresolvable."""
    try:
        from otaman_cli.registries.loader import strategy_repo

        home = strategy_repo(program.root)
    except Exception:  # noqa: BLE001 - unresolvable home → unresolvable registry
        return None
    if home is None:
        return None
    name = cfg.get("path") if isinstance(cfg, dict) else None
    return home / str(name or f"{key}.yaml")


def _entry_rows(path: Path) -> list[tuple[str, str]]:
    """``[(id, title), ...]`` from a registry file of UNKNOWN schema.

    Accepts either a top-level list or a mapping whose first list-of-mappings
    value is the entries (``vocabulary: [...]``, ``risks: [...]``, …). Anything
    unparseable yields no rows rather than raising into the TUI — the root still
    renders, just without a preview.
    """
    try:
        from otaman_cli.yaml_fast import load_file

        data = load_file(path)
    except Exception:  # noqa: BLE001 - unreadable/invalid → no preview
        return []

    items: object = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and any(isinstance(v, dict) for v in value):
                items = value
                break
    if not isinstance(items, list):
        return []

    rows: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            rows.append(("", str(item)))
            continue
        ident = next((str(item[k]) for k in _ID_KEYS if item.get(k)), "")
        title = next((str(item[k]) for k in _TITLE_KEYS if item.get(k) and k != ident), "")
        if title == ident:
            title = ""
        rows.append((ident, title))
    return rows


def discover(program: Program) -> list[ExtraRegistry]:
    """Every enabled non-spine process, in declaration order.

    Unknown process keys survive validation as raw dicts (``ProgramProcesses``
    allows extras), which is exactly what makes the forward-compatible slot
    possible without modelling registries nobody has specified yet.
    """
    try:
        from otaman_cli.registries.platform_ext import load_program_extensions

        procs = load_program_extensions(program.root / "platform.yaml").processes
    except Exception:  # noqa: BLE001 - unreadable platform.yaml → no extra roots
        return []

    extras = getattr(procs, "model_extra", None) or {}
    out: list[ExtraRegistry] = []
    for key, cfg in extras.items():
        if key in SPINE_PROCESSES or key in NON_REGISTRY_PROCESSES or not _enabled(cfg):
            continue
        path = _configured_path(program, key, cfg)
        exists = bool(path and path.is_file())
        out.append(
            ExtraRegistry(
                key=key,
                path=path,
                exists=exists,
                entries=_entry_rows(path) if exists and path else [],
            )
        )
    return out


def registry_root(reg: ExtraRegistry) -> TreeNode:
    """One collapsed sibling root for *reg*.

    An enabled process with no file still renders, saying so. The process being
    ON is the human's declaration that this registry is part of the program, and
    silently showing nothing would leave them without feedback — while these are
    precisely the registries that do not exist yet. That is different from the
    empty-outcome scaffolding D2 refuses: nobody opted into those.
    """
    if not reg.exists:
        detail = "enabled · not created yet" if reg.path else "enabled · registry home unset"
        return TreeNode(kind="registry", id=reg.label, title=detail, collapsed=True)

    total = len(reg.entries)
    node = TreeNode(
        kind="registry",
        id=reg.label,
        title=f"{total} entr{'y' if total == 1 else 'ies'}",
        collapsed=True,  # sideways growth must not cost vertical space (D7)
    )
    for ident, title in reg.entries[:MAX_ENTRIES]:
        node.children.append(TreeNode(kind="registry-entry", id=ident or title, title=title))
    if total > MAX_ENTRIES:
        node.children.append(
            TreeNode(
                kind="note",
                id=f"(… {total - MAX_ENTRIES} more not shown — open {reg.path.name})",
                title="",
            )
        )
    return node


def registry_roots(program: Program) -> list[TreeNode]:
    """The collapsed sibling roots to append to the artifact tree (5.1)."""
    return [registry_root(r) for r in discover(program)]


__all__ = [
    "MAX_ENTRIES",
    "NON_REGISTRY_PROCESSES",
    "SPINE_PROCESSES",
    "ExtraRegistry",
    "discover",
    "registry_root",
    "registry_roots",
]
