"""instruction-regeneration 1.1 — `init --update` accounts for what it regenerated.

The change exists because of a measured four-month silence: all 18 CLAUDE.local.md
files on this fleet dated to 2026-08-28 while the generator had taken 19 commits,
and `otaman init --update` — the command that regenerates them — reported nothing
about them at all. It printed `Updated: N`, which counts `.otaman` MARKERS, and a
generator return code. Two merged instruction fixes (plugin #92's retraction, cli
#211 removing the line it names) never reached a single running agent, and no
surface anywhere said so.

So the counting is the point, and it is measured rather than asked:

**Outcomes come from CONTENT, not from the generator's self-report.** The file set is
hashed before the generator runs and again after. `refreshed` means the bytes
changed, `unchanged` means they did not, `failed` means the file is absent or
unreadable after a run that should have written it. A generator that exits 0 having
written nothing is exactly the state this change was filed about, and only a
before/after comparison catches it.

**Zero work is STATED.** "0 refreshed, 18 unchanged" is the headline fact on a
healthy fleet and the alarm on a frozen one; printing nothing when nothing changed
is how four months passed.

**A repo with no directory is SKIPPED, not failed.** The generator warns and moves
on, and conflating "this repo is not checked out here" with "the generator failed to
write it" would make every partial workspace look broken.

**The generation stamp is reported, not written.** Task 1.1 names the stamp, but the
file's content is produced inside the generator's managed block
(`<!-- otaman:begin -->...<!-- otaman:end -->`), which is otaman-plugin's — and a
stamp written by anyone else can desynchronize from the content it describes the
moment someone runs the generator directly. So this detects and reports the stamp,
including its absence, which is the honest signal until plugin's generator emits it
(asked, with the shape named, rather than assumed).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The generator's per-repo output. One name, because that is what
#: `generate_repo_claude_md` writes; a surface that guessed at more would report
#: failures for files nothing was ever going to produce.
GENERATED_FILENAME = "CLAUDE.local.md"

#: The generation stamp, as asked of plugin's generator: a comment inside the
#: managed block naming the generator that wrote it. Matched leniently on the
#: version so a future format carrying more (a timestamp, a commit) still reads.
STAMP_RE = re.compile(
    r"<!--\s*otaman:generated\s+generator=(?P<version>[^\s]+)(?P<rest>[^>]*?)-->",
    re.IGNORECASE,
)

#: What a file's state is when the path does not exist at all.
ABSENT = None


@dataclass(frozen=True)
class FileState:
    """One expected generated file, before or after a generation run."""

    repo: str
    path: Path
    digest: str | None = None
    #: Set when the repo directory itself is missing — the generator skips those.
    repo_missing: bool = False
    #: Set when the path exists and could not be read. Distinct from absent: one
    #: means nothing was written, the other that something is wrong with the file.
    unreadable: bool = False
    stamp: str = ""


@dataclass
class Outcome:
    """The counted result of one regeneration, per instruction-regeneration 1.1."""

    refreshed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Repos whose generated file carries no generation stamp. Not a failure —
    #: the stamp is plugin's to emit — but the absence is the frozen-files signal.
    unstamped: list[str] = field(default_factory=list)
    stamps: dict[str, str] = field(default_factory=dict)

    @property
    def examined(self) -> int:
        return len(self.refreshed) + len(self.unchanged) + len(self.failed)

    @property
    def did_work(self) -> bool:
        return bool(self.refreshed)


def _digest(path: Path) -> tuple[str | None, bool]:
    """``(sha256-of-content, unreadable)`` for *path*."""
    if not path.is_file():
        return ABSENT, False
    try:
        raw = path.read_bytes()
    except OSError:
        return ABSENT, True
    return hashlib.sha256(raw).hexdigest(), False


def stamp_of(text: str) -> str:
    """The generation stamp in *text*, or ``""``.

    Reads the generator version the stamp names. Deliberately tolerant about
    anything else the stamp carries, so plugin can add a timestamp or a commit
    without this becoming a second format to keep in step.
    """
    match = STAMP_RE.search(text or "")
    return match.group("version").strip() if match else ""


def expected_files(root: Path, config: dict[str, Any]) -> list[FileState]:
    """Every generated instruction file the generator should write for *config*.

    Derived from `repos[].path`, never a hardcoded list: a fleet that grows a repo
    and a count that does not follow it would under-report exactly the way the
    silence this replaces did.
    """
    out: list[FileState] = []
    for repo in config.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        rel = str(repo.get("path") or "").strip()
        if not rel:
            continue
        name = str(repo.get("name") or rel)
        repo_dir = (root / rel).resolve()
        path = repo_dir / GENERATED_FILENAME
        if not repo_dir.is_dir():
            out.append(FileState(repo=name, path=path, repo_missing=True))
            continue
        digest, unreadable = _digest(path)
        stamp = ""
        if digest is not None:
            try:
                stamp = stamp_of(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                stamp = ""
        out.append(
            FileState(repo=name, path=path, digest=digest, unreadable=unreadable, stamp=stamp)
        )
    return out


def compare(before: list[FileState], after: list[FileState]) -> Outcome:
    """Counted outcomes from a before/after snapshot of the generated file set."""
    outcome = Outcome()
    prior = {f.repo: f for f in before}
    for now in after:
        if now.repo_missing:
            outcome.skipped.append((now.repo, "repo directory not found"))
            continue
        was = prior.get(now.repo)
        if now.unreadable:
            outcome.failed.append((now.repo, f"{GENERATED_FILENAME} exists and cannot be read"))
            continue
        if now.digest is None:
            # The generator should have written this and there is no file. That is
            # the state the whole change is about, and it is a failure, not a skip.
            outcome.failed.append((now.repo, f"no {GENERATED_FILENAME} after regeneration"))
            continue
        if was is None or was.digest is None:
            outcome.refreshed.append(now.repo)
        elif was.digest != now.digest:
            outcome.refreshed.append(now.repo)
        else:
            outcome.unchanged.append(now.repo)
        if now.stamp:
            outcome.stamps[now.repo] = now.stamp
        else:
            outcome.unstamped.append(now.repo)
    return outcome


def render_lines(outcome: Outcome) -> list[str]:
    """The counted report. Zero work is a sentence, never an absence."""
    lines = [
        f"instructions: {len(outcome.refreshed)} refreshed, "
        f"{len(outcome.unchanged)} unchanged, {len(outcome.failed)} failed"
        + (f", {len(outcome.skipped)} skipped" if outcome.skipped else "")
    ]
    if not outcome.examined:
        lines.append("  no generated instruction file was examined — nothing to account for")
        return lines
    if not outcome.did_work:
        # The headline on a healthy fleet, and the alarm on a frozen one. Both need
        # saying; which one it is depends on whether the generator moved.
        lines.append("  0 refreshed — every file already matched the generator's output")
    for repo in outcome.refreshed:
        lines.append(f"  refreshed: {repo}")
    for repo, why in outcome.failed:
        lines.append(f"  FAILED: {repo} — {why}")
    for repo, why in outcome.skipped:
        lines.append(f"  skipped: {repo} — {why}")
    if outcome.unstamped:
        lines.append(
            f"  no generation stamp in {len(outcome.unstamped)} file(s): "
            + ", ".join(outcome.unstamped)
        )
        lines.append("  (the stamp is emitted by the generator — otaman-plugin, ir 1.3)")
    elif outcome.stamps:
        versions = sorted(set(outcome.stamps.values()))
        lines.append("  generation stamp: generator " + ", ".join(versions))
    return lines


__all__ = [
    "GENERATED_FILENAME",
    "STAMP_RE",
    "FileState",
    "Outcome",
    "compare",
    "expected_files",
    "render_lines",
    "stamp_of",
]
