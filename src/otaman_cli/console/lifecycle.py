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


def _load_policy(program: Program, action: str | None = None):
    import yaml
    from otaman_core.spec_lifecycle import resolve_spec_policy

    try:
        cfg = yaml.safe_load((program.root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    policy = resolve_spec_policy(None, cfg.get("spec_policy") if isinstance(cfg, dict) else None)
    if action:
        from dataclasses import replace

        from otaman_cli.commands.spec import resolve_action_enforcement

        return replace(policy, enforcement=resolve_action_enforcement(program.root, action))
    return policy


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


def _commit_push(
    repo: Path, message: str, *, add_all: bool = False, paths: list[str] | None = None
) -> tuple[bool, bool, str]:
    """Commit the console write + best-effort push. Returns (committed, pushed, detail).

    The console is the HUMAN's seat (SSH-identified, keypress = confirmation), so
    the commit runs with the branch-policy hook's own human override
    (OTAMAN_ALLOW_MAIN=1) — the identity-correct answer to "the actor is Roman, not
    the fleet agent" (gate-3.1 defect: the agent-identity commit was refused on
    main). Any git failure leaves the write flagged loudly (last-resort D2 path)."""
    import os
    import subprocess

    env = {**os.environ, "OTAMAN_ALLOW_MAIN": "1"}  # human seat: sanctioned override

    def _run(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30, env=env
        )

    try:
        if add_all:
            _run("add", "-A", "--", "openspec/changes")
        elif paths:
            _run("add", "--", *paths)
        commit = _run("commit", "-m", message)
        if commit.returncode != 0:
            reason = (commit.stderr or commit.stdout or "").strip().splitlines()
            return False, False, reason[0] if reason else "git commit failed"
        push = _run("push")
        return True, push.returncode == 0, "" if push.returncode == 0 else "push failed"
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
    committed, pushed, detail = _commit_push(
        repo,
        f"chore(spec): ratify {name} (via otaman -i by {by})",
        paths=[f"openspec/changes/{name}/.openspec.yaml"],
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
    decision = check_archive_gate(
        read_openspec(d / ".openspec.yaml"), _load_policy(program, "archive")
    )
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
    committed, pushed, detail = _commit_push(
        repo, f"chore(spec): archive {name} (via otaman -i)", add_all=True
    )
    return True, f"archived {name}{_durability_suffix(committed, pushed, detail)}"


# ---------------------------------------------------------------------------
# row detail (console-lifecycle-actions 1.3): the per-change deep view — stage,
# triage, tasks tick-state, artifacts, gate results with block reasons, delivery
# badge, and the actions available to the viewer on this row.


def _available_actions(state: str, next_actor: str, archive_clean: bool) -> list[str]:
    actions = ["nudge (n)"]
    if "human" in next_actor and "ratify" in next_actor:
        actions.insert(0, "ratify (y)")
    if state == COMPLETE_UNARCHIVED and archive_clean:
        actions.insert(0, "archive (a)")
    return actions


def change_detail(program: Program, name: str) -> dict:
    """Assemble the per-change detail (values-free) the detail screen renders."""
    import re

    from otaman_core.spec_lifecycle import (
        check_archive_gate,
        check_dispatch_gate,
        check_merge_gate,
        read_openspec,
    )

    from otaman_cli.lifecycle import _completed_next_actor

    changes = _specs_changes_dir(program)
    if changes is None:
        return {}
    d = changes / name
    if not d.is_dir():
        return {}
    data = read_openspec(d / ".openspec.yaml")
    policy = _load_policy(program)

    tasks: list[tuple[bool, str]] = []
    tf = d / "tasks.md"
    if tf.is_file():
        for line in tf.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)", line)
            if m:
                tasks.append((m.group(1).lower() == "x", m.group(2).strip()))
    done = sum(1 for t, _ in tasks if t)

    artifacts = [
        f.name
        for f in (d / "proposal.md", d / "design.md", d / "tasks.md", d / ".openspec.yaml")
        if f.is_file()
    ]
    specs = d / "specs"
    if specs.is_dir():
        artifacts += [str(p.relative_to(d)) for p in sorted(specs.rglob("*.md"))]

    gates: dict[str, dict] = {}
    for gname, fn in (
        ("merge", check_merge_gate),
        ("dispatch", check_dispatch_gate),
        ("archive", check_archive_gate),
    ):
        try:
            dec = fn(data, policy)
            gates[gname] = {
                "allowed": dec.allowed,
                "mode": dec.mode,
                "violations": list(dec.violations),
                "notices": list(dec.notices),
            }
        except Exception:  # noqa: BLE001 - a gate error shouldn't sink the detail view
            continue

    state = "—"
    next_actor = "—"
    if tasks:
        if done < len(tasks):
            state = IN_FLIGHT
        else:
            state = COMPLETE_UNARCHIVED
            next_actor = _completed_next_actor(data, name)
    archive_clean = not gates.get("archive", {}).get("violations")
    return {
        "name": name,
        "stage": data.get("stage"),
        "triage": data.get("triage"),
        "triage_note": str(data.get("triage_note") or ""),
        "delivery": data.get("delivery"),
        "tasks": tasks,
        "tasks_done": done,
        "artifacts": artifacts,
        "gates": gates,
        "state": state,
        "next_actor": next_actor,
        "actions": _available_actions(state, next_actor, archive_clean),
        "change_dir": d,
    }


__all__ = [
    "APPROVED_UNAUTHORED",
    "COMPLETE_UNARCHIVED",
    "IN_FLIGHT",
    "LifecycleRow",
    "archive_change",
    "change_detail",
    "list_lifecycle_states",
    "ratify_change",
]
