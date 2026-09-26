"""Finding bus messages by TYPE, which lives in frontmatter and not in the name.

A message's filename is a convention this CLI happens to produce — the slug is
built from the subject, so the type usually lands in the name as a side effect.
It is not a contract, and two different mistakes follow from treating it as one:

* **False hits.** A message whose SUBJECT mentions a type matches a name filter
  for it. On the live bus, 5 of the 75 files matching ``*spec-change-approved*``
  are ``type: info`` — messages discussing an approval, not granting one. They
  became phantom "approved but never authored" rows in the lifecycle report,
  which tells spec-agent to author changes nobody approved.
* **Misses.** A message written by another producer (the MCP bus server, a
  future tool) whose name does not embed the type is invisible. Zero of the 70
  real approvals miss today, so this direction is latent rather than live —
  worth closing at the same time as the one that is biting.

Credit where due: plugin-agent hit the same class in their dispatch sweep
(`*task-complete*.md`), fixed it against frontmatter, and said plainly that any
sweep matching on filename has the same gap. This module is that check, run.

Cost: an authoritative scan reads the head of every message — ~330ms over 6550
files on the live bus. That is paid in reporting commands, not hot loops, and it
buys a report that cannot invent rows.
"""

from __future__ import annotations

from pathlib import Path

#: Bytes read per file. Frontmatter is the first block, so the type is always
#: well inside this; reading whole messages would multiply the cost for nothing.
_HEAD_BYTES = 1024


def _type_of(path: Path) -> str:
    """The frontmatter ``type:`` of *path*, or "" when unreadable/absent."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return ""
    if not head.startswith("---"):
        return ""
    for line in head.splitlines()[1:]:
        if line.startswith("---"):
            break  # end of frontmatter
        if line.startswith("type:"):
            return line.split(":", 1)[1].strip()
    return ""


def messages_of_type(bus_dir: Path, *types: str, recursive: bool = False) -> list[Path]:
    """Every message under *bus_dir* whose frontmatter type is one of *types*.

    Sorted, so callers that depend on chronological order (filenames lead with a
    compact timestamp) keep it. *recursive* walks archive subdirectories too.
    """
    if not bus_dir.is_dir():
        return []
    wanted = {t.strip() for t in types if t.strip()}
    if not wanted:
        return []
    try:
        paths = sorted(bus_dir.rglob("*.md") if recursive else bus_dir.glob("*.md"))
    except OSError:
        return []
    return [p for p in paths if _type_of(p) in wanted]
