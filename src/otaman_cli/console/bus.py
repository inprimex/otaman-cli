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
        from otaman_cli.bus_paths import _resolve_bus_paths

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
    #: `decision-required` only: the task/change this question is blocking.
    blocks: str = ""
    #: JTBD-57 1.3 / D5 — the proposal gate's row annotation, e.g.
    #: `  [gate 72/100 adequate · critique has-comments]`. Derived at INGEST, not on
    #: the render path (crs D1), and empty when the gate has produced nothing for
    #: this item. Set by the loader; the row reads it.
    gate_suffix: str = ""

    #: Short human names for the message types the console decides on. A raw
    #: `spec-change-request` in a confirmation prompt is accurate and unreadable.
    _TYPE_NAMES = {
        "spec-change-request": "SCR",
        "outcome-proposal": "outcome proposal",
    }

    def describe(self) -> str:
        """`SCR 'add rate limiting' from backend-agent` (console-undo 1.1).

        Every decision confirmation names its target before acting. Roman
        deferred an item and could not tell which one, because the prompt said
        only "Defer — enter a reason": correct, and useless. Type, title and
        sender are the three facts that identify WHICH item is about to be acted
        on, and all three are already here.

        Falls back to the stem rather than rendering an empty quote: a prompt
        that names nothing is the defect this closes.
        """
        kind = self._TYPE_NAMES.get(self.msg_type, self.msg_type or "item")
        title = (self.subject or "").strip()
        sender = (self.from_agent or "").strip()
        if not title:
            return f"{kind} {self.stem}" if self.stem else kind
        described = f"{kind} '{title}'"
        return f"{described} from {sender}" if sender else described

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

    @property
    def needs_answer(self) -> bool:
        """A question to ANSWER, not one to approve/reject (dae 1.2)."""
        from otaman_cli.console.decision_required import needs_answer

        return needs_answer(self.msg_type)

    @property
    def is_awaiting(self) -> bool:
        """Whether this row is waiting on the human at all.

        What the count and the sort key on — wider than `is_decision`, because a
        decision-required needs them just as much while offering different keys.
        """
        return self.is_decision or self.needs_answer


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
        from otaman_cli.yaml_fast import load_file

        data = load_file(platform_yaml)
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


def _meta_dirs_of(program_dir: Path) -> list[Path]:
    """The meta dirs inside *program_dir* — a child dir holding a platform.yaml.

    Core answers WHICH programs exist; the console needs their META root, because that
    is what every console read (the bus, platform.yaml) opens. This is the hop between
    the two, and the only part of the canonical arm that is the console's own question.
    """
    out: list[Path] = []
    try:
        for meta in sorted(program_dir.iterdir()):
            if meta.is_dir() and (meta / "platform.yaml").is_file():
                out.append(meta)
    except OSError:
        return []
    return out


def _canonical_meta_dirs(search_root: Path) -> list[Path]:
    """Program meta dirs under the canonical CE layout beneath *search_root*'s base(s).

    Enumeration is CORE's as of program-crud-and-context-resolution 1.2:
    `otaman_core.program_context.enumerate_programs` walks
    ``orgs/<org>/programs/<program>`` and — since core #112, which this surface's
    measurement prompted — requires a program MARKER rather than the path shape alone.
    That matters here specifically: this machine's `programs/` dir also holds two
    directories from a botched copy (a `LICENSE`, a `scripts/`), and before the
    predicate core's list and this picker disagreed 3 to 1.

    This reaches the meta dir directly (it sits 5 levels below ``$HOME``, past the
    bounded walk's ``max_depth``) — the fix for 5.1 finding #3: launched from home, the
    walk found nothing and the picker showed "No programs found". The full-shape/bus
    gate still runs on each candidate afterwards, so core's predicate and this
    surface's gate both apply.

    The local walk survives ONLY as the no-resolver fallback. cli pins no core version,
    and an old bundle losing this arm would reproduce finding #3 — an empty picker from
    the human's own home directory. It is the duplication to delete when the floor
    moves, not a second opinion: when core answers, core wins.
    """
    out: list[Path] = []
    try:
        from otaman_core.program_context import enumerate_programs
    except Exception:  # noqa: BLE001 - old bundle → the local walk below
        enumerate_programs = None  # type: ignore[assignment]

    for base in _canonical_bases(search_root):
        if enumerate_programs is not None:
            for program in enumerate_programs(base):
                out.extend(_meta_dirs_of(program.path))
            continue
        try:
            for org in (base / "orgs").iterdir():
                programs = org / "programs"
                if not (org.is_dir() and not org.name.startswith(".") and programs.is_dir()):
                    continue
                for program_dir in programs.iterdir():
                    if not program_dir.is_dir() or program_dir.name.startswith("."):
                        continue
                    out.extend(_meta_dirs_of(program_dir))
        except OSError:
            continue
    return out


def _is_canonical_layout(meta_root: Path) -> bool:
    """Whether *meta_root* sits in the canonical CE layout.

    ``<base>/orgs/<org>/programs/<program>/<meta>`` — matched on the two fixed
    segment names at their fixed depths, which is what makes a candidate canonical
    rather than merely deep.
    """
    parts = meta_root.resolve().parts
    if len(parts) < 5:
        return False
    return parts[-5] == "orgs" and parts[-3] == "programs"


def _declared_repo_dirs(meta_root: Path, meta: dict) -> set[Path]:
    """The directories a program DECLARES as its repos, resolved.

    This is what "inside another candidate's own repos tree" means
    (picker-canonical-precedence). The old rule used physical nesting anywhere
    beneath an ancestor, which cannot tell a stray copy under a program's repos/
    from a stale ancestor sitting above every program on the tenant — and on
    riseapps (2026-09-11) it picked the wrong one, showing ONE program instead of
    five.
    """
    out: set[Path] = set()
    for repo in meta.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        rel = str(repo.get("path") or "").strip()
        if rel:
            out.add((meta_root / rel).resolve())
    return out


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def discover_programs(
    search_root: Path,
    *,
    max_depth: int = 4,
    cwd: Path | None = None,
    warnings: list[str] | None = None,
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

    *warnings*, when given, collects messages about candidates that were EXCLUDED
    and why — today, pre-migration leftovers that would otherwise shadow real
    programs. Append-only and optional, so the eighteen existing call sites and
    tests keep working; a picker that silently drops a file is how the riseapps
    incident took a live debugging session to explain.
    """
    candidates: list[tuple[Program, dict]] = []
    root = search_root.resolve()
    seen_roots: set[Path] = set()

    def _add(meta_root: Path) -> None:
        resolved = meta_root.resolve()
        if resolved in seen_roots:
            return
        meta = _program_meta(resolved / "platform.yaml")
        if meta is not None:
            seen_roots.add(resolved)
            candidates.append((Program(name=str(meta["project"]), root=resolved), meta))

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

    # picker-canonical-precedence 1.1. Two rules, in this order, because the old
    # single rule conflated the cases they separate.
    #
    # (a) A non-canonical candidate that sits ABOVE a canonical one is a stale
    #     ancestor. It is excluded and NAMED — never the other way round. On
    #     riseapps a June-2026 `orgs/<org>/platform.yaml` with `repos: []` passed
    #     the shape gate, and because every real program sits physically under
    #     `orgs/<org>/`, the nested-drop discarded all five of them and the picker
    #     showed the leftover alone. Worked around live by renaming the file; any
    #     tenant carrying the same leftover hit it again.
    #
    #     The trade-off, stated: a LEGITIMATE program parked above canonical
    #     programs is also excluded. That is deliberate — org-level roots are dead
    #     by canon (uniform-ce-directory-layout), and the warning is how a
    #     misplaced one gets noticed instead of silently shadowing five programs.
    canonical = [p.root for p, _ in candidates if _is_canonical_layout(p.root)]
    stale: set[Path] = set()
    for program, _meta in candidates:
        if _is_canonical_layout(program.root):
            continue
        if any(program.root in c.parents for c in canonical):
            stale.add(program.root)
            if warnings is not None:
                warnings.append(
                    f"ignored {program.root / 'platform.yaml'}: it sits above "
                    f"canonical programs and would shadow them "
                    f"(pre-migration leftover — remove or move it)"
                )
    candidates = [(p, m) for p, m in candidates if p.root not in stale]

    # (b) The nested-drop, now scoped to what it was always FOR: a stray copy
    #     inside another program's own declared repos tree is not its own program.
    #     Physical nesting anywhere beneath an ancestor is not sufficient.
    repos_by_root = {p.root: _declared_repo_dirs(p.root, m) for p, m in candidates}
    kept: list[Program] = []
    for program, _meta in candidates:
        inside_other = any(
            other != program.root and any(_within(program.root, d) for d in dirs)
            for other, dirs in repos_by_root.items()
        )
        if not inside_other:
            kept.append(program)

    # Dedupe by program identity; the shallowest root wins (the canonical one).
    by_name: dict[str, Program] = {}
    for p in sorted(kept, key=lambda x: len(x.root.parts)):
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


#: plugin emits `Critique: <change> — <verdict>`; this lifts the change name back
#: out, which is the only join available under D6 (no parallel state store).
_CRITIQUE_SUBJECT = re.compile(r"Critique:\s*(?P<change>.+?)\s+—\s")

_QUEUE_TYPES = ("spec-change-request", "outcome-proposal")

#: Types that reach the human's queue REGARDLESS of `to:`, and that count as
#: awaiting them. `decision-required` joins the decision types here (dae 1.2):
#: an agent emitting one is blocked on the human by definition, so requiring it
#: to also be addressed correctly would let a misaddressed emission freeze the
#: agent silently — the exact failure the type exists to remove.
_AWAITING_TYPES = (*_QUEUE_TYPES, "decision-required")


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


def _critiques_from_entries(entries) -> dict:
    """`{change: Critique}` from critique-result messages in an already-walked set.

    Bounded: only entries whose TYPE is the critique result are opened, and the
    highest pass index wins — plugin allows two passes, and pass 2 exists precisely
    because pass 1's verdict was not the final word.
    """
    from otaman_cli.spec_gate_surface import CRITIQUE_RESULT_TYPE, parse_critique

    out: dict = {}
    for entry in entries:
        if entry.tag("type") != CRITIQUE_RESULT_TYPE:
            continue
        match = _CRITIQUE_SUBJECT.search(_subject_head(entry.path) or "")
        if match is None:
            continue
        try:
            body = entry.path.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = parse_critique(body)
        if not parsed.ran:
            continue
        change = match.group("change").strip()
        existing = out.get(change)
        if existing is None or parsed.pass_index >= existing.pass_index:
            out[change] = parsed
    return out


def _critique_for(subject: str, critiques: dict):
    """The critique for a proposal whose subject names a change.

    Matched on the change name appearing in the proposal's subject — D6 forbids a
    parallel state store, so the join has to come out of the messages themselves.
    Longest name first, because `console-lens` is a substring of
    `console-lens-navigation-and-filtering` and the shorter one would otherwise win.
    """
    from otaman_cli.spec_gate_surface import Critique

    if subject and critiques:
        for change in sorted(critiques, key=len, reverse=True):
            if change and change in subject:
                return critiques[change]
    return Critique()


#: Types whose row carries a stage-1 score. `spec-change-request` IS the proposal;
#: `spec-approval-pending` is the triage item `otaman propose` enqueues next to it
#: and names it, and is what the human's queue actually holds (19 of them on this
#: bus against 0 pending SCRs) — so the score has to reach that row or the number
#: never gets in front of the reviewer it was built for. No other type is scored:
#: core's required fields are the SCR's seven decision-grade sections, and an
#: outcome-proposal measured against them would score terribly for being a
#: different document.
_SCORED_TYPES = ("spec-change-request", "spec-approval-pending")

#: The SCR stem a `spec-approval-pending` body names, in backticks.
_SCR_REF = re.compile(r"`(?P<stem>\d{8}T\d{6}[A-Za-z0-9._-]*)`")


def _scr_path_for(path: Path, msg_type: str) -> Path | None:
    """The SCR file a scored row's score should be computed from, if any.

    The ONE type gate, and it lives here because this is the function that opens a
    file: every other type returns None without a read. That is the cost guard — the
    row loop above does a BOUNDED subject read for a reason (full reads of every row
    measured ~1.6s on this bus's 5,473 messages), and scoring needs a whole body.
    """
    if msg_type not in _SCORED_TYPES:
        return None
    if msg_type == "spec-change-request":
        return path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _SCR_REF.search(text)
    if match is None:
        return None
    candidate = path.parent / f"{match.group('stem')}.md"
    return candidate if candidate.is_file() else None


def _score_for(path: Path, msg_type: str, repos: list[str]):
    """Stage 1's score for one row, or an empty Score (JTBD-57 1.3 / D5).

    Reads a body for the two SCORED types only — 19 rows here, not 5,473; the type
    gate is in `_scr_path_for`, next to the read it guards. Derived at INGEST so the
    render path stays free of IO (crs D1), and it never raises: an unscoreable
    proposal renders `not scored`, never a zero.
    """
    from otaman_cli.spec_gate_surface import Score

    scr = _scr_path_for(path, msg_type)
    if scr is None:
        return Score()
    try:
        from otaman_cli.spec_gate_surface import score_for_scr

        body = scr.read_text(encoding="utf-8")
    except OSError as exc:
        return Score(error=f"proposal unreadable ({type(exc).__name__})")
    return score_for_scr(body, subject=_subject_head(scr) or "", platform_repos=repos)


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

    Reads the bus through the SHARED index (``bus_index.active_entries``), so
    the frontmatter pass is paid once for all three listers and memoized across
    renders — Home used to scan this directory three times. The old substring
    pre-filter is gone with it: it existed only to dodge a parse that is now
    cached, and it was the thing that once silently dropped the two live
    agent-addressed outcome-proposals.
    """
    from otaman_cli.console.bus_index import active_entries

    _, acks_dir = program.bus_paths()
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    # JTBD-57 1.3 / D5 — the gate's verdict per change, collected from the SAME
    # already-walked entry set. A second scan would be a second cost on the screen
    # Roman opens most, and the critique messages are right here.
    critiques = _critiques_from_entries(active_entries(program))
    # Read once for the whole listing, not per row: the lint compares a proposal's
    # affected repos against what the program declares.
    from otaman_cli.platform_config import declared_repo_names
    from otaman_cli.spec_gate_surface import row_suffix

    repos = declared_repo_names(program.root)

    out: list[Proposal] = []
    for entry in active_entries(program):
        f = entry.path
        if f"{f.stem}.human.ack" in acked:
            continue
        # CHEAP tier decides what to skip; the authoritative parse below builds
        # only the rows that survive (~735 of 5473 on the live bus).
        if entry.tag("to") != "human" and entry.tag("type") not in _AWAITING_TYPES:
            continue
        if entry.flag("x-cc"):
            continue
        fm = entry.fm
        if not fm:
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
        if fm.get("to") != "human" and msg_type not in _AWAITING_TYPES:
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
                # dae 1.2 — what a decision-required is blocking, so the tree
                # can mark the change that is waiting on this answer.
                blocks=str(fm.get("blocks", "") or ""),
                # D5 — derived HERE, at ingest, so the row renders it without IO.
                gate_suffix=row_suffix(
                    _score_for(f, msg_type, repos), _critique_for(subject or "", critiques)
                ),
            )
        )
    # Decisions first — they are the rows that need the human to act; then by
    # recency. A single list still has to say what is urgent.
    # Anything AWAITING the human first — a decision-required left below the
    # read-only traffic would be a question nobody sees.
    out.sort(key=lambda p: (not p.is_awaiting, p.timestamp), reverse=False)
    return out


def _decided_stems(program: Program) -> set[str]:
    """Stems a decision on this bus already settles — approved, rejected or
    dispositioned, in active OR archive.

    Delegates to `approval_link.decided_stems`, the single home shared with
    `otaman check`. This function used to glob `active/` itself, which made an
    ARCHIVED decision invisible and resurfaced its proposal on the mandatory queue —
    the ghost #290 was meant to remove. Values-free either way: only the back-link and
    the verdict kind are read, never a verdict's prose.
    """
    from otaman_cli.approval_link import decided_stems

    active_dir, _ = program.bus_paths()
    try:
        return set(decided_stems(active_dir, active_dir.parent / "archive"))
    except OSError:  # an unreadable bus yields no comparand; the ack check still stands
        return set()


def list_pending_proposals(program: Program) -> list[Proposal]:
    """Pending human decision items for *program* (no `<stem>.human.ack`): both
    spec-change-requests AND outcome-proposals addressed to the human (1.1).

    SCR detection matches `otaman approve`, so the two never disagree.
    Outcome-proposal CC copies (`x-cc: true`, addressed to strategic agents) are
    skipped so only the human's primary shows once. Malformed files are skipped,
    never crash the console.

    Perf: reads the bus through the SHARED index, so the frontmatter pass is
    paid once for all three listers and memoized across renders. The ack dir is
    still listed ONCE into a set rather than stat-ed per message, and the full
    file is read only for the handful of genuine pending proposals (for their
    subject + body).
    """
    from otaman_cli.console.bus_index import active_entries

    _, acks_dir = program.bus_paths()

    # List acks once → a set membership test, not a filesystem stat per message.
    try:
        acked = {p.name for p in acks_dir.glob("*.human.ack")} if acks_dir.is_dir() else set()
    except OSError:
        acked = set()

    # A decision recorded as a BROADCAST also resolves the request. 14 of the 19 items
    # on this queue were in exactly that state — approved or rejected, with the verdict
    # on the bus, and no `<stem>.human.ack` to show it. Roman opened the view and found
    # 18 of 19 already decided (spec-agent 20261006T185143); a mandatory tree that is
    # mostly ghosts trains the operator to stop reading it, defeating the review policy
    # it exists to serve.
    #
    # The back-link is parsed by its single home (`approval_link`) — the same primitive
    # `otaman check` uses to tell an approved proposal from a pending one, so the two
    # surfaces cannot disagree about what "decided" means.
    decided = _decided_stems(program)

    out: list[Proposal] = []
    for entry in active_entries(program):
        f = entry.path
        if f"{f.stem}.human.ack" in acked:
            continue
        if f.stem in decided:
            continue
        if entry.tag("type") not in _QUEUE_TYPES:
            continue  # cheap tier: skips ~99% without a YAML parse
        fm = entry.fm
        if fm.get("type") not in _QUEUE_TYPES:
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
