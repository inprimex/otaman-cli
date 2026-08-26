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


def _program_name(root: Path) -> str:
    try:
        import yaml

        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and cfg.get("project"):
            return str(cfg["project"])
    except Exception:  # noqa: BLE001 - fall back to dir name
        pass
    return root.name


def discover_programs(search_root: Path, *, max_depth: int = 4) -> list[Program]:
    """Programs found under *search_root* — each directory with a platform.yaml.

    Bounded-depth scan (a program is a platform.yaml + bus; a tenant has
    several). Sorted by name; deduped by resolved root. `.git`/hidden and
    common heavy dirs are skipped so the picker stays fast.
    """
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", ".agents", "dist", "build"}
    found: dict[Path, Program] = {}
    root = search_root.resolve()

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if (d / "platform.yaml").is_file():
            rp = d.resolve()
            found.setdefault(rp, Program(name=_program_name(d), root=rp))
        try:
            children = [c for c in d.iterdir() if c.is_dir() and c.name not in skip]
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    walk(root, 0)
    return sorted(found.values(), key=lambda p: p.name.lower())


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
