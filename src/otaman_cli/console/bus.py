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
    """A pending human decision item as the console displays it (values-free) —
    a spec-change-request OR an outcome-proposal (``msg_type`` distinguishes)."""

    stem: str
    subject: str
    from_agent: str
    timestamp: str
    priority: str
    path: Path
    body: str
    msg_type: str = "spec-change-request"

    @property
    def from_human(self) -> bool:
        """Display flag: the sender looks like a human, not an ``*-agent``.

        Mirrors ``InboxMessage.from_human`` — the merged queue (2.1) carries
        every row as a ``Proposal``, so the read view's sender label needs the
        same flag it had when the two row models were separate.
        """
        f = (self.from_agent or "").strip()
        return f == "human" or not f.endswith("-agent")

    @property
    def is_decision(self) -> bool:
        """Whether this row carries decision actions (approve/reject/defer).

        A property of the ITEM, not of which screen found it — that inversion
        is what console-ia-consolidation 2.1 removes.
        """
        return self.msg_type in _QUEUE_TYPES


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


def _frontmatter_head(f: Path, limit: int = 8192) -> str | None:
    """The YAML frontmatter block of *f*, read from a BOUNDED head only.

    Bus messages put frontmatter at the very top; reading the whole file (many
    with multi-KB bodies) across a ~3000-message active dir is what made the
    console scan 7-9s (5.1 finding #5). ``limit`` bytes always covers a bus
    frontmatter block. Returns the inner YAML text, or None if there's no
    frontmatter.
    """
    try:
        with f.open("r", encoding="utf-8") as fh:
            head = fh.read(limit)
    except OSError:
        return None
    m = re.match(r"^---\n(.+?)\n---", head, re.DOTALL)
    return m.group(1) if m else None


_QUEUE_TYPES = ("spec-change-request", "outcome-proposal")


def _subject_head(path: Path, limit: int = 4096) -> str:
    """The `## Subject:` line from a bounded head of *path*, or ``""``.

    The subject sits immediately after the frontmatter, so a small head is
    enough and a full read is waste multiplied by the message count.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(limit)
    except OSError:
        return ""
    for line in head.splitlines():
        if line.strip().startswith("## Subject:"):
            return line.strip().replace("## Subject:", "").strip()
    return ""


def read_body(item: Proposal) -> str:
    """The message body, read on demand from ``item.path``.

    The list carries no bodies (see :func:`list_human_queue`); a detail screen
    is opened for ONE item, so one read there is free where 700 reads in the
    list were not. Falls back to an already-populated ``body`` so callers of
    ``list_pending_proposals`` — which still eager-loads — are unaffected.
    """
    if item.body:
        return item.body
    try:
        content = item.path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return content.split("---", 2)[-1] if content.count("---") >= 2 else content


def list_human_queue(program: Program) -> list[Proposal]:
    """EVERY pending item addressed to the human — decisions and plain messages
    alike, newest-relevant first (console-ia-consolidation 2.1).

    The type-exclusion split is gone. There were two scanners over the same
    directory with complementary filters: ``list_pending_proposals`` kept only
    ``_QUEUE_TYPES`` and the inbox kept only everything else, so an item's TYPE
    decided which of two screens could see it — and a human looking for
    "what needs me" had to remember which list a thing lands in. One list,
    typed rows: :attr:`Proposal.is_decision` says whether a row carries decision
    actions, rather than a screen boundary saying it.

    Every row is a ``Proposal`` because the two row models had the same fields
    under different names; decision rows are the ones whose ``msg_type`` is in
    :data:`_QUEUE_TYPES`, and they are what the action keys operate on.

    Keeps the 5.1 perf shape (bounded frontmatter read, one ack-dir listing, full
    read only for rows that survive filtering) — it now pays that cost once
    instead of twice.
    """
    import yaml

    active_dir, acks_dir = program.bus_paths()
    if not active_dir.is_dir():
        return []
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    out: list[Proposal] = []
    for f in sorted(active_dir.glob("*.md")):
        fm_text = _frontmatter_head(f)
        if fm_text is None:
            continue
        # Cheap substring pre-filter before any YAML parse (the 5.1 perf shape).
        # It must admit BOTH clauses of the union below — an early
        # `"human" not in fm_text` alone silently dropped the two live
        # agent-addressed outcome-proposals, because their frontmatter never
        # mentions the human at all.
        if "human" not in fm_text and not any(t in fm_text for t in _QUEUE_TYPES):
            continue
        if f"{f.stem}.human.ack" in acked:
            continue
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        # The UNION of what the two old listers surfaced, deliberately: a row
        # qualifies if it is addressed to the human OR it is a decision type.
        #
        # That second clause is not redundant. `list_pending_proposals` filtered
        # on TYPE ALONE, so an outcome-proposal addressed to a strategic agent
        # still appeared in the human's decision queue — measured on the live
        # bus, two do. Requiring `to: human` would have silently dropped them,
        # and a pending decision disappearing from the human's queue is the
        # silent-approval-loss failure this codebase keeps relearning. Absorbing
        # a surface must not narrow it; the routing question is raised with
        # spec-agent/cofounder separately rather than settled by a filter here.
        msg_type = str(fm.get("type", ""))
        if fm.get("to") != "human" and msg_type not in _QUEUE_TYPES:
            continue
        # CC copies are addressed to strategic agents; the human's primary shows once.
        if fm.get("x-cc"):
            continue
        # BOUNDED read, not the whole file. The list needs only the subject; the
        # body is read on open by the detail screens (`read_body`), which is the
        # only place it is rendered. Full-reading every row cost ~1.6s on this
        # bus's 5.4k messages — a visible freeze on the screen Roman opens most.
        subject = _subject_head(f)
        out.append(
            Proposal(
                stem=f.stem,
                subject=subject or f.stem,
                from_agent=str(fm.get("from", "?")),
                timestamp=str(fm.get("timestamp", "")),
                priority=str(fm.get("priority", "normal")),
                path=f,
                body="",  # lazy — see read_body()
                msg_type=msg_type,
            )
        )
    # Decisions first — they are the rows that need the human to act; then by
    # recency. A single list still has to say what is urgent.
    out.sort(key=lambda p: (not p.is_decision, p.timestamp), reverse=False)
    return out


def list_pending_proposals(program: Program) -> list[Proposal]:
    """Pending human decision items for *program* (no `<stem>.human.ack`): both
    spec-change-requests AND outcome-proposals addressed to the human (1.1).

    SCR detection matches `otaman approve`, so the two never disagree.
    Outcome-proposal CC copies (`x-cc: true`, addressed to strategic agents) are
    skipped so only the human's primary shows once. Malformed files are skipped,
    never crash the console.

    Perf (5.1 finding #5 — ~3000-message active dirs): the hot loop reads only
    a bounded frontmatter head (not the whole file), skips the ~99% of
    messages that aren't spec-change-requests with a cheap substring test
    BEFORE any YAML parse, and lists the ack dir ONCE into a set instead of a
    stat per message. The full file is read only for the handful of genuine
    pending proposals (for their subject + body). ~3000 files: 2.3s → <0.3s.
    """
    import yaml

    active_dir, acks_dir = program.bus_paths()
    if not active_dir.is_dir():
        return []

    # List acks once → a set membership test, not a filesystem stat per message.
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    out: list[Proposal] = []
    for f in sorted(active_dir.glob("*.md")):
        fm_text = _frontmatter_head(f)
        if fm_text is None:
            continue
        # Cheap prefilter: skip the overwhelming majority without parsing YAML.
        # fm_text is the frontmatter ONLY, so this can't false-match a body.
        if not any(t in fm_text for t in _QUEUE_TYPES):
            continue
        if f"{f.stem}.human.ack" in acked:
            continue
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict) or fm.get("type") not in _QUEUE_TYPES:
            continue
        # Skip CC copies (outcome-proposal fans out to strategic agents) — the
        # human's primary is the one to act on; showing the CC copies would
        # duplicate the row and address it to the wrong recipient.
        if fm.get("x-cc"):
            continue
        # Only genuine pending proposals reach here (a handful) — now it's
        # cheap to read the whole file for the subject + body.
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
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
                msg_type=str(fm.get("type")),
            )
        )
    return out


__all__ = [
    "Program",
    "Proposal",
    "discover_programs",
    "list_human_queue",
    "list_pending_proposals",
    "read_body",
]
