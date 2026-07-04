"""`otaman notify-change <change>` — post-merge-spec-notify (tasks 1.1-1.6).

Replaces the missing post-commit hook firing on GitHub-side merges
(`gh pr merge`).  Operator runs this manually after merging a spec PR:

    $ otaman notify-change cli-send-cc-fanout-parity

The command:
  1. Resolves the specs repo path from `platform.yaml specs.path`
  2. Reads `openspec/changes/<change>/tasks.md` for `@otaman-<repo>` annotations
  3. Maps each annotation to the repo's owner via `platform.yaml repos[]`
  4. Writes a `spec-change` bus message addressed to those owners
     (fallback: `spec-agent, human` when no tasks.md or no annotations)
  5. Optionally invokes `map-tasks.py` if found (graceful degradation otherwise)

Format mirrors `otaman-plugin/scripts/spec-change-hook.sh` so consumers
treat the message identically regardless of trigger source.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ANN_RE = re.compile(r"@otaman-[a-z0-9.-]+", re.IGNORECASE)


def _resolve_specs_path(root: Path) -> Path | None:
    """Read `platform.yaml specs.path` and resolve it relative to *root*.

    Returns None when `platform.yaml` is missing/malformed or the field
    is absent.  Falls back to the conventional sibling `<root>-specs`
    when the explicit path resolves to a non-existent directory — keeps
    the common workspace layout working without manual config.
    """
    try:
        import yaml
    except ImportError:
        return None

    platform = root / "platform.yaml"
    if not platform.is_file():
        return None
    try:
        doc = yaml.safe_load(platform.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None

    specs_cfg = doc.get("specs") or {}
    if isinstance(specs_cfg, dict):
        raw = specs_cfg.get("path")
        if isinstance(raw, str) and raw:
            candidate = (root / raw).resolve()
            if candidate.is_dir():
                return candidate

    # Conventional fallback
    sibling = (root.parent / f"{root.name}-specs").resolve()
    if sibling.is_dir():
        return sibling
    # Also try without the -specs suffix (some workspaces just name it `specs`)
    alt = (root.parent / "otaman-specs").resolve()
    if alt.is_dir():
        return alt
    return None


def _parse_at_annotations(tasks_md: Path) -> list[str]:
    """Return ordered, deduplicated list of `@otaman-<repo>` annotations.

    Annotations are case-insensitive; output preserves lowercase + first-
    seen order so downstream owner-lookup is deterministic.
    """
    if not tasks_md.is_file():
        return []
    try:
        text = tasks_md.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _ANN_RE.finditer(text):
        name = m.group(0).lower().lstrip("@")  # "otaman-cli"
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _lookup_owners(annotations: list[str], platform_yaml: Path) -> list[str]:
    """Map `otaman-<repo>` annotations to repo owners via platform.yaml.

    Annotations carry the literal `otaman-` prefix (e.g. `otaman-cli` from
    `@otaman-cli`), but `repos[].name` conventions differ across programs:
    this project's own platform.yaml names repos with the prefix intact
    (`otaman-cli`), while other otaman-managed programs commonly name repos
    without it (`sunflowers-specs`). Try the annotation as-is first, then
    fall back to the prefix-stripped form, so both conventions resolve.

    Returns ordered, deduplicated list of owner agent names.  Annotations
    that don't match any `repos[].name` (in either form) are silently
    skipped (the hook's behavior — better to under-notify than mis-notify).
    """
    if not platform_yaml.is_file():
        return []
    try:
        import yaml
        doc = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    repos = doc.get("repos") or []
    if not isinstance(repos, list):
        return []
    by_name = {}
    for r in repos:
        if isinstance(r, dict):
            name = r.get("name")
            owner = r.get("owner")
            if isinstance(name, str) and isinstance(owner, str) and owner:
                by_name[name] = owner

    seen: set[str] = set()
    out: list[str] = []
    for ann in annotations:
        owner = by_name.get(ann)
        if owner is None and ann.startswith("otaman-"):
            owner = by_name.get(ann[len("otaman-"):])
        if owner and owner not in seen:
            seen.add(owner)
            out.append(owner)
    return out


def derive_recipients(specs_root: Path, change_name: str, platform_yaml: Path) -> list[str]:
    """Public — derive the `to:` list for a `spec-change` notification.

    Mirrors `spec-change-hook.sh` lines 103-149 logic exactly:
      - No `tasks.md` for the change → `["spec-agent"]`
      - `tasks.md` exists but no `@otaman-<repo>` annotations OR no owner
        resolved → `["spec-agent", "human"]`
      - Otherwise → ordered list of unique owners from the annotations
    """
    tasks_md = specs_root / "openspec" / "changes" / change_name / "tasks.md"
    if not tasks_md.is_file():
        return ["spec-agent"]

    annotations = _parse_at_annotations(tasks_md)
    if not annotations:
        return ["spec-agent", "human"]

    owners = _lookup_owners(annotations, platform_yaml)
    if not owners:
        return ["spec-agent", "human"]
    return owners


def _build_message(
    *,
    change_name: str,
    recipients: list[str],
    commit_hash: str,
    commit_msg: str,
    commit_author: str,
    timestamp_iso: str,
    msg_id: str,
    specs_repo_name: str,
) -> str:
    """Render the spec-change body — mirrors spec-change-hook.sh template."""
    to_field = ", ".join(recipients)
    return (
        f"---\n"
        f"id: {msg_id}\n"
        f"from: {specs_repo_name}\n"
        f"to: {to_field}\n"
        f"priority: high\n"
        f"type: spec-change\n"
        f"timestamp: {timestamp_iso}\n"
        f"status: pending\n"
        f"---\n"
        f"\n"
        f"## Subject: Specs changed in {specs_repo_name}\n"
        f"\n"
        f"Commit `{commit_hash}` by {commit_author}: {commit_msg}\n"
        f"\n"
        f"**Change**: {change_name}\n"
        f"\n"
        f"Recipients are derived from `tasks.md` `@otaman-<repo>` annotations.\n"
        f"Fallback: `spec-agent` when no tasks.md exists; `spec-agent, human` "
        f"when no annotations.  Use `/otaman:check` to see this notification.\n"
        f"\n"
        f"This message was generated by `otaman notify-change` "
        f"(post-merge-spec-notify), not the post-commit hook.\n"
    )


def _git_metadata(specs_root: Path) -> tuple[str, str, str]:
    """Capture HEAD commit hash / message / author for the message body."""
    def _git(*args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(specs_root), *args],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return (
        _git("rev-parse", "--short", "HEAD") or "(no-commit)",
        _git("log", "-1", "--format=%s") or "(unknown commit message)",
        _git("log", "-1", "--format=%an") or "(unknown author)",
    )


def _find_map_tasks_py() -> Path | None:
    """Locate map-tasks.py — same candidate search as spec-change-hook.sh:202-212.

    Returns None when not found; caller logs a warning and continues
    (graceful degradation per task 1.4).
    """
    candidates: list[Path] = []
    # 1. Co-located with this module's repo (otaman-cli/scripts)
    pkg_root = Path(__file__).resolve().parent.parent.parent  # src/otaman_cli → repo root
    candidates.append(pkg_root / "scripts" / "map-tasks.py")
    # 2. Plugin's scripts (the canonical home)
    candidates.append(pkg_root.parent / "otaman-plugin" / "scripts" / "map-tasks.py")
    # 3. Conventional sibling under workspace
    candidates.append(pkg_root.parent / "scripts" / "map-tasks.py")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _resolve_bus_active(project_root: Path) -> Path:
    """Match cmd_send's path resolution: `.agents/bus/active/`."""
    return project_root / ".agents" / "bus" / "active"


def notify_change(project_root: Path, change_name: str) -> tuple[int, dict[str, Any]]:
    """Public entry — returns ``(exit_code, summary_dict)``.

    Exit codes:
      0 — message written; map-tasks.py either ran or was gracefully absent
      1 — change directory not found (spec doesn't exist)
      2 — bus dir not writable / unrecoverable I/O failure
    """
    summary: dict[str, Any] = {
        "change_name": change_name,
        "recipients": [],
        "message_path": None,
        "map_tasks_called": False,
        "map_tasks_path": None,
        "tasks_md_path": None,
    }

    specs_root = _resolve_specs_path(project_root)
    if specs_root is None:
        return 1, {**summary, "error": "could not resolve specs repo path from platform.yaml"}

    change_dir = specs_root / "openspec" / "changes" / change_name
    if not change_dir.is_dir():
        return 1, {**summary, "error": f"change directory not found: {change_dir}"}

    tasks_md = change_dir / "tasks.md"
    summary["tasks_md_path"] = str(tasks_md) if tasks_md.is_file() else None

    platform_yaml = project_root / "platform.yaml"
    recipients = derive_recipients(specs_root, change_name, platform_yaml)
    summary["recipients"] = recipients

    commit_hash, commit_msg, commit_author = _git_metadata(specs_root)
    now = datetime.now(timezone.utc)
    msg_ts = now.strftime("%Y%m%dT%H%M%S")
    iso_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    msg_id = f"{msg_ts}-{commit_hash}"

    body = _build_message(
        change_name=change_name,
        recipients=recipients,
        commit_hash=commit_hash,
        commit_msg=commit_msg,
        commit_author=commit_author,
        timestamp_iso=iso_ts,
        msg_id=msg_id,
        specs_repo_name=specs_root.name,
    )

    bus_active = _resolve_bus_active(project_root)
    try:
        bus_active.mkdir(parents=True, exist_ok=True)
        (bus_active / "acks").mkdir(exist_ok=True)
    except OSError as exc:
        return 2, {**summary, "error": f"bus dir not writable: {exc}"}

    msg_filename = f"{msg_ts}-{specs_root.name}-spec-change.md"
    msg_path = bus_active / msg_filename
    try:
        msg_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return 2, {**summary, "error": f"failed to write message: {exc}"}
    summary["message_path"] = str(msg_path)

    # map-tasks.py invocation (task 1.4) — graceful degradation when absent
    map_tasks = _find_map_tasks_py()
    if map_tasks is None:
        summary["map_tasks_called"] = False
    else:
        summary["map_tasks_path"] = str(map_tasks)
        # Find python interpreter — match spec-change-hook.sh's preference order
        py: str | None = None
        for c in ("python3", "py", "python"):
            if shutil.which(c):
                py = c
                break
        if py is not None and tasks_md.is_file():
            try:
                subprocess.run(
                    [py, str(map_tasks), str(tasks_md)],
                    capture_output=True, timeout=30, check=False,
                )
                summary["map_tasks_called"] = True
            except (OSError, subprocess.TimeoutExpired):
                summary["map_tasks_called"] = False

    return 0, summary


def cmd_notify_change(args: list[str]) -> int:
    """`otaman notify-change <change-name>` CLI entry point (task 1.1)."""
    from otaman_cli.main import UI, find_project_root

    if not args:
        UI.error("Usage: otaman notify-change <change-name>")
        return 2
    change_name = args[0].strip()
    if not change_name:
        UI.error("change-name cannot be empty")
        return 2

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    rc, summary = notify_change(root, change_name)
    if "error" in summary:
        UI.error(summary["error"])
        return rc

    # Task 1.5 — summary
    UI.ok(f"spec-change notification written: {Path(summary['message_path']).name}")
    UI.kv("  Change", summary["change_name"])
    UI.kv("  Recipients", ", ".join(summary["recipients"]))
    if summary["tasks_md_path"]:
        UI.muted(f"  tasks.md: {summary['tasks_md_path']}")
    else:
        UI.muted("  tasks.md: (absent — fallback recipients used)")
    if summary["map_tasks_called"]:
        UI.ok(f"map-tasks.py invoked: {summary['map_tasks_path']}")
    elif summary["map_tasks_path"]:
        UI.warn("map-tasks.py found but invocation skipped (no python interpreter or no tasks.md)")
    else:
        UI.warn("map-tasks.py not found — task-assignment dispatch deferred (graceful degradation)")

    return rc


__all__ = [
    "cmd_notify_change",
    "derive_recipients",
    "notify_change",
]
