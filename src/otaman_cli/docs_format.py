"""Fence-aware GFM markdown-table lint + fix (docs-format-check + refinements).

Lifted from otaman-strategy/scripts/mdtables.py (D4: lift, don't rewrite), then
refined (docs-format-refinements D1-D3):

Rules:
- R1 every table row has the header's cell count
- R2 row 2 is a valid ``|---|`` separator matching header width
- R3 no raw ``<`` before a non-space inside cell TEXT (code-span content is
  literal territory — exempt, D1)
- R4 rows are pipe-delimited at both ends

Caveats + refinements, all preserved:
1. Content inside code fences is NEVER touched.
2. Unfenced pipe-drawn ASCII art (a block whose second row is not a separator)
   is NEVER modified by the fixer — flagged for a fence instead.
3. Inline code spans (`` `...` ``) are literal (CommonMark): their content is
   lint-exempt AND fixer-untouchable (D1).
4. Backtick-first escaping (D2): the fixer wraps token-shaped ``<`` ``>`` ``|``
   in code spans (renders identically, reads naturally raw) and MIGRATES prior
   backslash escapes (``\\<``) to that form; backslash is only a fallback.
5. ``--align`` (D3): opt-in, idempotent width alignment; render-equivalent to the
   compact form; never applied by a plain ``--fix``.
"""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path

_SEP_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")
_SEP_ATTEMPT = re.compile(r"^\s*:?-+:?\s*$")
_RAW_LT = re.compile(r"(?<!\\)<(?=\S)")
#: An inline code span: a backtick run, content, then a matching-length run.
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_PIPE_SENTINEL = "\x00"


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _split_cells(line: str) -> list[str]:
    """Split a table row into cells on unescaped ``|`` — span-aware.

    A ``|`` inside an inline code span is NOT a cell delimiter (a wrapped
    ``\\`x|y\\``` stays one cell), so backtick-first escaping round-trips.
    """
    s = line.strip()
    # Mask pipes inside code spans so they are not treated as delimiters.
    buf: list[str] = []
    last = 0
    for m in _CODE_SPAN.finditer(s):
        buf.append(s[last : m.start()])
        buf.append(m.group(0).replace("|", _PIPE_SENTINEL))
        last = m.end()
    buf.append(s[last:])
    masked = "".join(buf)
    parts = _CELL_SPLIT.split(masked)
    if masked.startswith("|"):
        parts = parts[1:]
    if masked.endswith("|") and not masked.endswith("\\|"):
        parts = parts[:-1]
    return [p.replace(_PIPE_SENTINEL, "|") for p in parts]


def _segments(cell: str) -> list[tuple[str, str]]:
    """Split a cell into (kind, text) segments: ``span`` (incl. backticks) or ``text``."""
    out: list[tuple[str, str]] = []
    i = 0
    for m in _CODE_SPAN.finditer(cell):
        if m.start() > i:
            out.append(("text", cell[i : m.start()]))
        out.append(("span", m.group(0)))
        i = m.end()
    if i < len(cell):
        out.append(("text", cell[i:]))
    return out


def _cell_has_raw_lt(cell: str) -> bool:
    """A raw ``<`` before a non-space in cell TEXT (code-span content is exempt, D1)."""
    return any(kind == "text" and _RAW_LT.search(seg) for kind, seg in _segments(cell))


def _needs_span(token: str) -> bool:
    """Whether a (whitespace-delimited) token carries a token-shaped ``<``/``>``/``|``."""
    return bool(re.search(r"<\S", token)) or bool(re.search(r"\S>", token)) or "|" in token


def _wrap_token(token: str) -> str:
    """Backtick-first (D2): migrate escapes to raw, then wrap a token-shaped token
    in a code span; otherwise return it unchanged."""
    raw = token.replace("\\<", "<").replace("\\>", ">").replace("\\|", "|")
    return f"`{raw}`" if _needs_span(raw) else token


def _fix_cell(cell: str) -> str:
    """Fix a cell backtick-first: transform TEXT segments (wrap token-shaped
    angle/pipe tokens, migrate backslash escapes), leave code spans verbatim."""
    parts: list[str] = []
    for kind, seg in _segments(cell):
        if kind == "span":
            parts.append(seg)
        else:
            parts.append(re.sub(r"\S+", lambda m: _wrap_token(m.group(0)), seg))
    return "".join(parts).strip()


def _looks_like_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_ATTEMPT.match(c) for c in cells)


def _render_compact(rows: list[list[str]], width: int) -> list[str]:
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return out


def _render_aligned(rows: list[list[str]], width: int) -> list[str]:
    widths = [max(len(rows[r][col]) for r in range(len(rows))) for col in range(width)]

    def _fmt(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(widths[col]) for col, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * max(3, widths[col]) for col in range(width)) + " |"
    out = [_fmt(rows[0]), sep]
    for r in rows[1:]:
        out.append(_fmt(r))
    return out


def process_text(
    text: str, *, fix: bool = False, align: bool = False
) -> tuple[str, list[tuple[int, str]]]:
    """Lint (and optionally fix / align) markdown table blocks in *text*.

    Returns ``(new_text, problems)`` — ``(line, rule)`` 1-based. Fences, code-span
    content, and unfenced pipe-art are never modified. ``align`` implies writing
    and is idempotent + render-equivalent to the compact form.
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
        sep_cells = _split_cells(block[1])
        if not _looks_like_separator(sep_cells):
            problems.append(
                (
                    block_start + 1,
                    "R-art unfenced pipe block is not a table — move ASCII/pipe art into a fence",
                )
            )
            out.extend(block)
            continue

        header = _split_cells(block[0])
        width = len(header)
        sep_valid = all(_SEP_CELL.match(c) for c in sep_cells) and len(sep_cells) == width
        if not sep_valid:
            problems.append((block_start + 2, "R2 bad/short separator"))
        if any(_cell_has_raw_lt(c) for c in header):
            problems.append((block_start + 1, "R3 raw '<' in header — wrap in a code span (`<x`)"))

        fixed_rows: list[list[str]] = [[_fix_cell(c) for c in header]]
        for j, row in enumerate(block[2:]):
            c = _split_cells(row)
            ln = block_start + 3 + j
            if len(c) != width:
                problems.append((ln, f"R1 {len(c)} cells, header has {width}"))
                if len(c) < width:
                    c = c + [""] * (width - len(c))
                else:
                    c = c[: width - 1] + [" ".join(c[width - 1 :])]
            if any(_cell_has_raw_lt(x) for x in c):
                problems.append((ln, "R3 raw '<' in cell — wrap in a code span (`<x`)"))
            if not row.rstrip().endswith("|"):
                problems.append((ln, "R4 missing trailing pipe"))
            fixed_rows.append([_fix_cell(x) for x in c])

        if align:
            rendered = _render_aligned(fixed_rows, width)
        elif fix:
            rendered = _render_compact(fixed_rows, width)
        else:
            rendered = block
        out.extend(rendered)
    return "\n".join(out), problems


def lint_text(text: str) -> list[tuple[int, str]]:
    return process_text(text, fix=False)[1]


def lint_path(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return process_text(text, fix=False)[1]


def fix_path(path: Path, *, align: bool = False) -> tuple[list[tuple[int, str]], bool]:
    """Fix *path* in place (optionally aligning). Returns ``(problems, changed)``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    new_text, problems = process_text(text, fix=True, align=align)
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
    its own — the caller enforces the no-targets no-op (D2 of docs-format-check).
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
