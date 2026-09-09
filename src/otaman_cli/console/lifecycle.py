"""Console adapter over the shared lifecycle derivation (IHC 1.3 / SLE 2.1 D3).

The derivation now lives in :mod:`otaman_cli.lifecycle` — one derivation, two
renderers (this console view + ``otaman spec status`` / doctor). This module only
resolves the program's specs-changes dir + bus dir and delegates; the console
``LifecycleScreen`` renders the returned rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otaman_cli.console.bus import Program
from otaman_cli.lifecycle import (
    APPROVED_UNAUTHORED,
    COMPLETE_UNARCHIVED,
    IN_FLIGHT,
    LifecycleRow,
    derive_lifecycle,
)


def _specs_changes_dir(program: Program) -> Path | None:
    """The specs repo's ``openspec/changes`` dir (from platform.yaml specs.path)."""
    import yaml

    try:
        cfg = yaml.safe_load((program.root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    specs = cfg.get("specs") if isinstance(cfg, dict) else None
    path = specs.get("path") if isinstance(specs, dict) else None
    if not path:
        return None
    changes = (program.root / path / "openspec" / "changes").resolve()
    return changes if changes.is_dir() else None


def list_lifecycle_states(program: Program, *, now: datetime | None = None) -> list[LifecycleRow]:
    """Catchable lifecycle states for *program*, derived from bus + specs repo."""
    active_dir, _ = program.bus_paths()
    return derive_lifecycle(
        changes_dir=_specs_changes_dir(program),
        bus_active_dir=active_dir if active_dir.is_dir() else None,
        now=now,
    )


# ---------------------------------------------------------------------------
# console lifecycle actions (console-lifecycle-actions 1.2): one-key ratify +
# archive, committed writes (D2 — the spec-approved pattern). Human's keypress
# is the confirmation (SSH session, no adapter prompt); reason from the modal.


def _specs_root(program: Program) -> Path | None:
    changes = _specs_changes_dir(program)
    return changes.parent.parent if changes else None  # <specs>/openspec/changes → <specs>


def _load_policy(program: Program):
    import yaml
    from otaman_core.spec_lifecycle import resolve_spec_policy

    try:
        cfg = yaml.safe_load((program.root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    return resolve_spec_policy(None, cfg.get("spec_policy") if isinstance(cfg, dict) else None)


def _write_openspec(path: Path, data: dict) -> None:
    import os
    import tempfile

    import yaml

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _git(repo: Path, *args: str):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30
    )


def _commit_push(repo: Path, message: str) -> tuple[bool, bool, str]:
    """Commit staged changes + best-effort push. Returns (committed, pushed, detail)."""
    import subprocess

    try:
        c = _git(repo, "commit", "-m", message)
        if c.returncode != 0:
            reason = (c.stderr or c.stdout or "").strip().splitlines()
            return False, False, reason[0] if reason else "git commit failed"
        p = _git(repo, "push")
        return True, p.returncode == 0, "" if p.returncode == 0 else "push failed"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, False, str(exc)


def _durability_suffix(committed: bool, pushed: bool, detail: str) -> str:
    if not committed:
        return f" — ⚠ commit needed (spec-agent): {detail}"
    if not pushed:
        return " — committed (push pending)"
    return " — committed + pushed"


def ratify_change(program: Program, name: str, *, by: str, reason: str) -> tuple[bool, str]:
    """One-key RATIFY from the console (D2, human-only). Mints the ratified
    approval, stamps ratified_at, commits+pushes. *by* is the acting human."""
    from otaman_core.spec_lifecycle import (
        SpecLifecycleError,
        apply_ratification,
        ratify,
        read_openspec,
    )

    if not (by and by.strip()):
        return False, "ratify is human-only: no acting identity (set OTAMAN_HUMAN)"
    if not (reason and reason.strip()):
        return False, "ratify requires a reason"
    changes = _specs_changes_dir(program)
    if changes is None:
        return False, "specs repo not resolved (platform.yaml specs.path)"
    d = changes / name
    if not d.is_dir():
        return False, f"no change named {name!r}"
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        record = ratify(name, by=by.strip(), reason=reason.strip(), at=at)
    except SpecLifecycleError as exc:
        return False, str(exc)
    oy = d / ".openspec.yaml"
    updated = apply_ratification(read_openspec(oy), record)
    updated["ratified_at"] = at
    _write_openspec(oy, updated)
    repo = _specs_root(program)
    _git(repo, "add", "--", f"openspec/changes/{name}/.openspec.yaml")
    committed, pushed, detail = _commit_push(
        repo, f"chore(spec): ratify {name} (via otaman -i by {by})"
    )
    return True, f"ratified {name}{_durability_suffix(committed, pushed, detail)}"


def archive_change(program: Program, name: str) -> tuple[bool, str]:
    """One-key ARCHIVE from the console (D2). Runs only when the archive gate is
    ALLOWED; moves the change under archive/<date>-<name>, sets stage=archived,
    commits+pushes."""
    import shutil

    from otaman_core.spec_lifecycle import check_archive_gate, read_openspec, set_stage

    changes = _specs_changes_dir(program)
    if changes is None:
        return False, "specs repo not resolved (platform.yaml specs.path)"
    d = changes / name
    if not d.is_dir():
        return False, f"no change named {name!r}"
    decision = check_archive_gate(read_openspec(d / ".openspec.yaml"), _load_policy(program))
    # Require a CLEAN pass, not a warn-mode waiver: a violation means the change
    # isn't properly archivable (e.g. ratify it first). D1's "gate ALLOWED" for the
    # button is the clean gate, not the waived one.
    if decision.violations:
        return False, "archive blocked by gate: " + "; ".join(decision.violations)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = changes / "archive" / f"{date}-{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return False, f"archive target already exists: {dest.name}"
    shutil.move(str(d), str(dest))
    try:
        set_stage(dest / ".openspec.yaml", "archived")
    except Exception:  # noqa: BLE001 - stage write best-effort; the move is the archive
        pass
    repo = _specs_root(program)
    _git(repo, "add", "-A", "--", "openspec/changes")
    committed, pushed, detail = _commit_push(repo, f"chore(spec): archive {name} (via otaman -i)")
    return True, f"archived {name}{_durability_suffix(committed, pushed, detail)}"


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "LifecycleRow",
    "archive_change",
    "list_lifecycle_states",
    "ratify_change",
]
