"""Textual-free bus reads for the console (program discovery + pending proposals).

Kept independent of Textual so the console's data layer is unit-testable with
no TUI. Every read here is values-free — proposals carry locations/metadata,
never secrets. `list_pending_proposals` mirrors `otaman approve`'s pending
detection (a spec-change-request with no `<stem>.human.ack`), so the console
and the CLI agree on what is pending.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Program:
    """One program the console can scope to (its own platform.yaml + bus)."""

    name: str
    root: Path

    def bus_paths(self) -> tuple[Path, Path]:
        """(active_dir, acks_dir) via the shared resolver — honors bus_path."""
        from otaman_cli.main import _resolve_bus_paths

        return _resolve_bus_paths(self.root)


@dataclass(frozen=True)
class Proposal:
    """A pending spec-change-request as the console displays it (values-free)."""

    stem: str
    subject: str
    from_agent: str
    timestamp: str
    priority: str
    path: Path
    body: str


# Directories that never hold a distinct PROGRAM root: heavy build dirs, the
# bus itself, and — the 5.1 picker finding (spec 20260827T065715) — fixture /
# launcher / example subtrees whose platform.yaml is a sample or a nested copy,
# not a program.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".agents",
        "dist",
        "build",
        "site-packages",
        "examples",
        "example",
        "launcher",
        "test",
        "tests",
        "fixtures",
        "sample",
        "samples",
    }
)


def _program_meta(platform_yaml: Path) -> dict | None:
    """Parsed platform.yaml IFF *platform_yaml* is a real PROGRAM root.

    A program root (not a repo-local file, org stray, or fixture) has the FULL
    program shape — `project` + `version` + a `repos` list — AND a bus
    (`.agents/` beside it). This is the picker's canonical-discovery gate
    (5.1 finding #1); it rejects the "trust any platform.yaml" behavior.
    """
    try:
        import yaml

        data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable/malformed → not a program
        return None
    if not isinstance(data, dict):
        return None
    if not (data.get("project") and data.get("version") and isinstance(data.get("repos"), list)):
        return None
    if not (platform_yaml.parent / ".agents").is_dir():
        return None  # a program has a bus; a repo-local platform.yaml does not
    return data


def _canonical_bases(search_root: Path) -> list[Path]:
    """CE-layout bases (the dir that holds ``orgs/``) reachable from
    *search_root* — either because it IS a base or because it sits inside one.

    The canonical CE layout is ``<base>/orgs/<org>/programs/<program>/<meta>``
    (uniform-ce-directory-layout canon). A human launches the console from
    their home dir (a base) or from inside a program (below a base); both must
    enumerate every program, so we derive the base from either position.
    """
    bases: list[Path] = []
    if (search_root / "orgs").is_dir():
        bases.append(search_root)
    parts = search_root.parts
    if "orgs" in parts:
        idx = parts.index("orgs")
        base = Path(*parts[:idx]) if idx > 0 else Path(search_root.anchor)
        if base not in bases:
            bases.append(base)
    return bases


def _canonical_meta_dirs(search_root: Path) -> list[Path]:
    """Program meta dirs under the canonical CE layout beneath *search_root*'s
    base(s): ``orgs/<org>/programs/<program>/<meta>`` holding a platform.yaml.

    This reaches the meta dir directly (it sits 5 levels below ``$HOME``, past
    the bounded walk's ``max_depth``) — the fix for 5.1 finding #3: launched
    from home, the walk found nothing, so the picker showed "No programs
    found". The full-shape/bus gate still runs on each candidate.
    """
    out: list[Path] = []
    for base in _canonical_bases(search_root):
        try:
            for org in (base / "orgs").iterdir():
                programs = org / "programs"
                if not (org.is_dir() and not org.name.startswith(".") and programs.is_dir()):
                    continue
                for program in programs.iterdir():
                    if not program.is_dir() or program.name.startswith("."):
                        continue
                    for meta in program.iterdir():
                        if meta.is_dir() and (meta / "platform.yaml").is_file():
                            out.append(meta)
        except OSError:
            continue
    return out


def discover_programs(
    search_root: Path, *, max_depth: int = 4, cwd: Path | None = None
) -> list[Program]:
    """Distinct PROGRAM roots for the picker, deduped by identity.

    Three candidate sources, unioned then deduped (5.1 findings #1 and #3):

    1. A bounded recursive walk under *search_root* — handles an explicit
       ``--path`` and non-canonical layouts; skips fixture/launcher/example
       subtrees and drops nested copies (finding #1).
    2. The canonical CE directory layout under *search_root*'s base(s) —
       ``orgs/<org>/programs/<program>/<meta>`` — so a home-dir launch (the
       human's natural entry point) enumerates every program even though the
       meta sits below the walk's ``max_depth`` (finding #3).
    3. When *cwd* is given, the program resolved from its marker chain — so
       launching from inside a one-off checkout outside the standard base
       still surfaces that program (finding #3, "union the cwd marker chain").

    Every candidate must pass the full-shape + bus gate (`_program_meta`);
    candidates are deduped by program IDENTITY (`project`), keeping the
    shallowest root — so one program never appears twice.
    """
    candidates: list[Program] = []
    root = search_root.resolve()

    def _add(meta_root: Path) -> None:
        meta = _program_meta(meta_root / "platform.yaml")
        if meta is not None:
            candidates.append(Program(name=str(meta["project"]), root=meta_root.resolve()))

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if (d / "platform.yaml").is_file():
            _add(d)
        try:
            children = [
                c
                for c in d.iterdir()
                if c.is_dir() and c.name not in _SKIP_DIRS and not c.name.startswith(".")
            ]
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    walk(root, 0)

    # 2. Canonical CE layout under the base(s) — the finding #3 fix.
    for meta_dir in _canonical_meta_dirs(root):
        _add(meta_dir)

    # 3. cwd marker-chain union (only when a cwd is supplied — keeps the
    #    function a pure read of the filesystem for tests that don't pass one).
    if cwd is not None:
        from otaman_cli.identity import find_project_root

        try:
            cwd_root = find_project_root(cwd)
        except Exception:  # noqa: BLE001 - a broken/unsafe marker must not kill the picker
            cwd_root = None
        if cwd_root is not None:
            _add(cwd_root)

    # Drop any candidate nested inside another candidate's tree (a stray copy
    # under a program's repos/subdirs is not its own program).
    roots = {p.root for p in candidates}
    candidates = [
        p for p in candidates if not any(o != p.root and o in p.root.parents for o in roots)
    ]

    # Dedupe by program identity; the shallowest root wins (the canonical one).
    by_name: dict[str, Program] = {}
    for p in sorted(candidates, key=lambda x: len(x.root.parts)):
        by_name.setdefault(p.name, p)
    return sorted(by_name.values(), key=lambda p: p.name.lower())


def list_pending_proposals(program: Program) -> list[Proposal]:
    """Pending spec-change-requests for *program* (no `<stem>.human.ack`).

    Same detection as `otaman approve`, so the two never disagree. Malformed
    files are skipped, never crash the console.
    """
    import yaml

    active_dir, acks_dir = program.bus_paths()
    if not active_dir.is_dir():
        return []

    out: list[Proposal] = []
    for f in sorted(active_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            if not isinstance(fm, dict) or fm.get("type") != "spec-change-request":
                continue
            if (acks_dir / f"{f.stem}.human.ack").exists():
                continue
            body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
            subject = ""
            for line in body.splitlines():
                if line.strip().startswith("## Subject:"):
                    subject = line.strip().replace("## Subject:", "").strip()
                    break
            out.append(
                Proposal(
                    stem=f.stem,
                    subject=subject or f.stem,
                    from_agent=str(fm.get("from", "?")),
                    timestamp=str(fm.get("timestamp", "")),
                    priority=str(fm.get("priority", "normal")),
                    path=f,
                    body=body.strip(),
                )
            )
        except (OSError, yaml.YAMLError):
            continue
    return out


__all__ = ["Program", "Proposal", "discover_programs", "list_pending_proposals"]
