"""Fence-aware GFM markdown-table lint + fix (docs-format-check, lifted from
otaman-strategy/scripts/mdtables.py — D4: lift, don't rewrite).

Rules (D1):
- R1 every table row has the header's cell count
- R2 row 2 is a valid ``|---|`` separator matching header width
- R3 no raw ``<`` before a non-space inside cells (escaped as ``\\<``)
- R4 rows are pipe-delimited at both ends

Two hard-won caveats, both preserved:
1. Content inside code fences is NEVER touched.
2. Unfenced pipe-drawn ASCII art (a pipe block whose second row is not a
   separator) is NEVER modified by the fixer — the linter flags it as needing
   a fence instead. A block is treated as a real table only when its second row
   looks like a separator; otherwise the fixer leaves it byte-for-byte intact.
"""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path

_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_SEP_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")
#: A "separator attempt": dashes/colons/space only (even if too short / wrong
#: width). Distinguishes a real (possibly malformed) table from pipe-art.
_SEP_ATTEMPT = re.compile(r"^\s*:?-+:?\s*$")
_RAW_LT = re.compile(r"(?<!\\)<(?=\S)")


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _cells_of(line: str) -> list[str]:
    parts = _CELL_SPLIT.split(line.strip())
    if line.strip().startswith("|"):
        parts = parts[1:]
    if line.strip().endswith("|") and not line.strip().endswith("\\|"):
        parts = parts[:-1]
    return parts


def _esc_lt(cell: str) -> str:
    return _RAW_LT.sub(r"\\<", cell)


def _looks_like_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_ATTEMPT.match(c) for c in cells)


def process_text(text: str, *, fix: bool = False) -> tuple[str, list[tuple[int, str]]]:
    """Lint (and optionally fix) markdown table blocks in *text*.

    Returns ``(new_text, problems)`` where problems is a list of ``(line, rule)``
    (1-based line numbers). ``new_text`` == *text* when ``fix`` is False or nothing
    changed. Fences and unfenced pipe-art are never modified.
    """
    lines = text.split("\n")
    problems: list[tuple[int, str]] = []
    out: list[str] = []
    i = 0
    fenced = False
    while i < len(lines):
        if lines[i].lstrip().startswith("```"):
            fenced = not fenced
            out.append(lines[i])
            i += 1
            continue
        if fenced or not _is_row(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        block_start = i
        block: list[str] = []
        while i < len(lines) and _is_row(lines[i]):
            block.append(lines[i])
            i += 1
        if len(block) < 2:
            out.extend(block)
            continue
        sep_cells = _cells_of(block[1])
        if not _looks_like_separator(sep_cells):
            # Unfenced pipe-art / non-table pipe block: FLAG, never modify (caveat 2).
            problems.append(
                (
                    block_start + 1,
                    "R-art unfenced pipe block is not a table — move ASCII/pipe art into a fence",
                )
            )
            out.extend(block)
            continue

        header = _cells_of(block[0])
        width = len(header)
        sep_valid = all(_SEP_CELL.match(c) for c in sep_cells) and len(sep_cells) == width
        if not sep_valid:
            problems.append((block_start + 2, "R2 bad/short separator"))
        if any(_RAW_LT.search(c) for c in header):
            problems.append((block_start + 1, "R3 raw '<' in header cell"))

        fixed = [
            "| " + " | ".join(_esc_lt(c.strip()) for c in header) + " |",
            "|" + "---|" * width,
        ]
        for j, row in enumerate(block[2:]):
            c = _cells_of(row)
            ln = block_start + 3 + j
            if len(c) != width:
                problems.append((ln, f"R1 {len(c)} cells, header has {width}"))
                if len(c) < width:
                    c = c + [""] * (width - len(c))
                else:
                    c = c[: width - 1] + [" ".join(c[width - 1 :])]
            if any(_RAW_LT.search(x) for x in c):
                problems.append((ln, "R3 raw '<' in cell"))
            if not row.rstrip().endswith("|"):
                problems.append((ln, "R4 missing trailing pipe"))
            fixed.append("| " + " | ".join(_esc_lt(x.strip()) for x in c) + " |")
        out.extend(fixed if fix else block)
    return "\n".join(out), problems


def lint_text(text: str) -> list[tuple[int, str]]:
    return process_text(text, fix=False)[1]


def lint_path(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return process_text(text, fix=False)[1]


def fix_path(path: Path) -> tuple[list[tuple[int, str]], bool]:
    """Fix *path* in place. Returns ``(problems, changed)``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    new_text, problems = process_text(text, fix=True)
    changed = new_text != text
    if changed:
        path.write_text(new_text, encoding="utf-8")
    return problems, changed


# ---------------------------------------------------------------------------
# target expansion + config


def expand_targets(targets: list[str]) -> list[Path]:
    """Expand explicit files / folders / globs to a sorted, de-duped file list.

    A folder yields its ``**/*.md``; a glob yields its file matches; a file is
    taken as-is (the operator chose it). NEVER expands to a whole-repo sweep on
    its own — the caller enforces the no-targets no-op (D2).
    """
    found: set[Path] = set()
    for t in targets:
        p = Path(t)
        if p.is_dir():
            found.update(f for f in p.rglob("*.md") if f.is_file())
        elif p.is_file():
            found.add(p)
        else:
            for m in _glob.glob(t, recursive=True):
                mp = Path(m)
                if mp.is_file():
                    found.add(mp)
    return sorted(found)


_DEFAULT_INCLUDE = ["**/*.md"]


def load_docs_format_config(root: Path) -> tuple[list[str], list[str]]:
    """(include, exclude) globs from platform.yaml's ``docs-format`` block.

    Sane defaults: include ``**/*.md``, exclude nothing. Append-style historical
    logs can be excluded (lint-only) via the exclude list.
    """
    import yaml

    try:
        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return list(_DEFAULT_INCLUDE), []
    block = cfg.get("docs-format") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return list(_DEFAULT_INCLUDE), []
    include = block.get("include")
    exclude = block.get("exclude")
    include = (
        [str(g) for g in include]
        if isinstance(include, list) and include
        else list(_DEFAULT_INCLUDE)
    )
    exclude = [str(g) for g in exclude] if isinstance(exclude, list) else []
    return include, exclude


def scan_configured(root: Path, include: list[str], exclude: list[str]) -> list[Path]:
    """Markdown files under *root* matching include globs minus exclude globs."""
    inc: set[Path] = set()
    for g in include:
        inc.update(f for f in root.glob(g) if f.is_file())
    if exclude:
        excluded: set[Path] = set()
        for g in exclude:
            excluded.update(root.glob(g))
        inc = {f for f in inc if f not in excluded and not _under_any(f, excluded)}
    return sorted(inc)


def _under_any(path: Path, dirs: set[Path]) -> bool:
    parents = set(path.parents)
    return any(d in parents for d in dirs)


__all__ = [
    "expand_targets",
    "fix_path",
    "lint_path",
    "lint_text",
    "load_docs_format_config",
    "process_text",
    "scan_configured",
]
