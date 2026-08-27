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


def discover_programs(search_root: Path, *, max_depth: int = 4) -> list[Program]:
    """Distinct PROGRAM roots under *search_root* for the picker.

    Canonical discovery (5.1 finding #1, spec 20260827T065715): a candidate
    must have full program shape + a bus (`_program_meta`); fixture/launcher/
    example subtrees are skipped; a candidate nested inside another program's
    tree is dropped; and candidates are deduped by program IDENTITY (`project`),
    keeping the shallowest root — so one program never appears twice.
    """
    candidates: list[Program] = []
    root = search_root.resolve()

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        pf = d / "platform.yaml"
        if pf.is_file():
            meta = _program_meta(pf)
            if meta is not None:
                candidates.append(Program(name=str(meta["project"]), root=d.resolve()))
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
