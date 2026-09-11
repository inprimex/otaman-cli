"""Console artifact review — IHC iteration 2, wired to SLE's spec-approved stage.

The second HITL stage (D5): a change at stage ``authored`` awaits a human review
of its actual artifacts (proposal / design / tasks / spec deltas) before it can be
dispatched. This module is the Textual-free logic the ``ArtifactBrowserScreen``
renders:

- :func:`list_authored_changes` — changes at stage ``authored`` (repo is truth).
- :func:`read_artifact` / :func:`change_git_diff` — per-file view + diff.
- :func:`advance_to_spec_approved` — approver-gated (cto/approver hat, D5): sets
  the change's stage to ``spec-approved`` (the authoritative signal) and posts a
  derived bus notification. Dispatch (SLE 2.2) unblocks off that stage.
- :func:`request_changes` — reviewer comments back to the author on the bus; the
  change stays at ``authored``.

The feature guard is DROPPED (:data:`ITERATION2_ENABLED` is True): core's
spec-approved stage machine has landed (SLE step 1), so the browser is live.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otaman_cli.console.bus import Program
from otaman_cli.console.lifecycle import _specs_changes_dir

#: SLE 2.3 — guard dropped now that core's spec-approved stage machine exists.
ITERATION2_ENABLED = True

_TOP_ARTIFACTS = ("proposal.md", "design.md", "tasks.md", ".openspec.yaml")


@dataclass(frozen=True)
class AuthoredChange:
    name: str
    change_dir: Path
    stage: str
    files: tuple[str, ...]


def _artifact_files(change_dir: Path) -> tuple[str, ...]:
    files: list[str] = [n for n in _TOP_ARTIFACTS if (change_dir / n).is_file()]
    specs = change_dir / "specs"
    if specs.is_dir():
        files.extend(str(f.relative_to(change_dir)) for f in sorted(specs.rglob("*.md")))
    return tuple(files)


def _has_authored_artifacts(files: tuple[str, ...]) -> bool:
    """Whether a change carries authored artifacts (anything beyond .openspec.yaml)."""
    return any(f != ".openspec.yaml" for f in files)


def list_authored_changes(program: Program) -> list[AuthoredChange]:
    """Changes awaiting the spec-approved review.

    Primarily stage ``authored``, PLUS stage ``approved`` changes that already
    carry authored artifacts — those pre-date the stage convention (e.g. SLE
    itself) and would otherwise be un-reviewable in-console (Roman's live gap,
    2026-09-07). Anything already at/past spec-approved is excluded.
    """
    from otaman_core.spec_lifecycle import read_stage

    changes = _specs_changes_dir(program)
    if changes is None:
        return []
    out: list[AuthoredChange] = []
    for d in sorted(p for p in changes.iterdir() if p.is_dir() and p.name != "archive"):
        stage = read_stage(d / ".openspec.yaml")
        files = _artifact_files(d)
        if stage == "authored" or (stage == "approved" and _has_authored_artifacts(files)):
            out.append(AuthoredChange(name=d.name, change_dir=d, stage=stage, files=files))
    return out


def read_artifact(change_dir: Path, relname: str) -> str:
    """Read one artifact file's text — confined to *change_dir* (no traversal)."""
    base = change_dir.resolve()
    p = (change_dir / relname).resolve()
    if p != base and base not in p.parents:
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def change_git_diff(change_dir: Path) -> str:
    """Best-effort ``git diff`` of the change dir (uncommitted); '' if none/clean."""
    try:
        r = subprocess.run(
            ["git", "-C", str(change_dir), "diff", "--", "."],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _eligible_approver(program: Program):
    """The resolved roster human iff they may mint spec-approved (cto/approver, D5)."""
    from otaman_core.human_roster import load_human_roster
    from otaman_core.spec_lifecycle import resolve_spec_approver

    try:
        roster = load_human_roster(program.root / "platform.yaml")
    except Exception:  # noqa: BLE001 - absent/invalid roster → no eligible approver
        roster = []
    return resolve_spec_approver(roster, os.environ.get("OTAMAN_HUMAN"))


def _now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y%m%dT%H%M%S")


def advance_to_spec_approved(
    program: Program, change_name: str, *, reason: str = ""
) -> tuple[bool, str]:
    """Advance ``authored`` → ``spec-approved`` (approver-gated). The stage in
    ``.openspec.yaml`` is the authoritative signal; a bus notification is derived."""
    from otaman_core.spec_lifecycle import (
        SPEC_APPROVED_STAGE,
        SpecLifecycleError,
        read_openspec,
        set_stage,
        spec_approved_reached,
    )

    approver = _eligible_approver(program)
    if approver is None:
        return False, (
            "not an eligible spec approver — needs the cto or approver roster hat "
            "(set OTAMAN_HUMAN to a matching roster identity)"
        )
    changes = _specs_changes_dir(program)
    if changes is None:
        return False, "specs repo not resolved (platform.yaml specs.path)"
    d = changes / change_name
    oy = d / ".openspec.yaml"
    data = read_openspec(oy)
    if spec_approved_reached(data):
        return False, f"{change_name} is already at or past spec-approved"
    stage = data.get("stage")
    # authored, or approved-with-artifacts (the pre-convention case, D9 gap #3).
    if stage not in ("authored", "approved") or (
        stage == "approved" and not _has_authored_artifacts(_artifact_files(d))
    ):
        return False, (
            f"{change_name} is at stage {stage or 'unknown'} — only an authored "
            "(or approved-with-artifacts) change can advance to spec-approved"
        )
    try:
        set_stage(oy, SPEC_APPROVED_STAGE)
    except SpecLifecycleError as exc:
        return False, str(exc)
    # Repo is truth (D1): commit the stage change (human-seat override for the
    # branch-policy hook — gate-3.1 defect fix; the actor is the present human).
    from otaman_cli.console.lifecycle import _commit_push, _durability_suffix, _specs_root

    committed, pushed, detail = _commit_push(
        _specs_root(program),
        f"chore(spec): {change_name} -> spec-approved (via otaman -i by {approver.name})",
        paths=[f"openspec/changes/{change_name}/.openspec.yaml"],
    )
    _broadcast(program, change_name, approver.name, reason, committed=committed, pushed=pushed)
    suffix = _durability_suffix(committed, pushed, detail)
    return True, f"{change_name} → spec-approved (by {approver.name}){suffix}"


def request_changes(program: Program, change_name: str, comments: str) -> tuple[bool, str]:
    """Send reviewer comments to the author (spec-agent); the change stays authored."""
    from otaman_cli.bus_write import write_message_exclusive

    if not comments.strip():
        return False, "request-changes needs a comment"
    active, _ = program.bus_paths()
    iso, ts = _now()
    stem = f"{ts}-human-to-spec-agent-review-request-{change_name}"[:120]
    content = (
        f"---\nid: {stem}\nfrom: human\nto: spec-agent\npriority: normal\ntype: review-request\n"
        f"timestamp: {iso}\nstatus: pending\n---\n\n"
        f"## Subject: Changes requested on {change_name} (stays authored)\n\n"
        f"Reviewed in otaman -i; the change is NOT spec-approved yet. Requested changes:\n\n"
        f"{comments}\n"
    )
    write_message_exclusive(active / f"{stem}.md", content)
    return True, f"changes requested on {change_name} (author notified)"


def _broadcast(
    program: Program,
    change_name: str,
    by: str,
    reason: str,
    *,
    committed: bool = True,
    pushed: bool = True,
) -> None:
    from otaman_cli.bus_write import write_message_exclusive

    active, _ = program.bus_paths()
    iso, ts = _now()
    stem = f"{ts}-human-to-all-spec-approved-{change_name}"[:120]
    reason_section = f"\n### Reason\n{reason}\n" if reason else ""
    if not committed:
        commit_note = (
            "\n\n**spec-agent: the stage change is written but NOT committed — "
            "please commit .openspec.yaml in otaman-specs to make it durable (D1).**"
        )
    elif not pushed:
        commit_note = "\n\n(stage committed locally; push pending)"
    else:
        commit_note = ""
    content = (
        # `announce`: the non-privileged fleet-broadcast type (bwsv ruling) for a
        # legit to:all notification — `info` is refused as a broadcast.
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: normal\ntype: announce\n"
        f"timestamp: {iso}\nstatus: pending\n---\n\n"
        f"## Subject: spec-approved: {change_name}\n\n"
        f"Change **{change_name}** reached **spec-approved** (advanced in otaman -i by {by}). "
        f"Dispatch is now unblocked.{reason_section}{commit_note}"
    )
    write_message_exclusive(active / f"{stem}.md", content)


__all__ = [
    "ITERATION2_ENABLED",
    "AuthoredChange",
    "advance_to_spec_approved",
    "change_git_diff",
    "list_authored_changes",
    "read_artifact",
    "request_changes",
]
