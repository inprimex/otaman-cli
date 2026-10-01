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
  5. Dispatches map-tasks via `run_script` (otaman_plugin.map_tasks) when a
     tasks.md is present (graceful degradation if the dispatch fails)

Format mirrors `otaman-plugin/scripts/spec-change-hook.sh` so consumers
treat the message identically regardless of trigger source.
"""

from __future__ import annotations

import os
import re
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


def _owner_map(platform_yaml: Path) -> tuple[dict[str, str], str]:
    """``(repo -> owner, reason)`` — the map, or why there is none.

    A non-empty *reason* means the map could not be built, and it is NOT the same
    fact as a map that resolved no owner for a particular annotation. Four states
    reached `derive_recipients` as one empty list before this: absent
    platform.yaml, unreadable platform.yaml, a `repos:` key of the wrong shape, and
    a `repos:` list whose entries name no owner.
    """
    if not platform_yaml.is_file():
        return {}, f"no platform.yaml at {platform_yaml}"
    try:
        import yaml

        doc = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - unreadable is NOT "declares no owners"
        return {}, f"platform.yaml could not be read ({type(exc).__name__})"
    if not isinstance(doc, dict):
        return {}, "platform.yaml is not a mapping"
    repos = doc.get("repos")
    if repos is None:
        return {}, "platform.yaml declares no `repos:`"
    if not isinstance(repos, list):
        return {}, "platform.yaml `repos:` is not a list"
    by_name: dict[str, str] = {}
    for r in repos:
        if isinstance(r, dict):
            name = r.get("name")
            owner = r.get("owner")
            if isinstance(name, str) and isinstance(owner, str) and owner:
                by_name[name] = owner
    if not by_name:
        return {}, f"platform.yaml `repos:` names no owners ({len(repos)} entr(ies))"
    return by_name, ""


def _lookup_owners(annotations: list[str], platform_yaml: Path) -> list[str]:
    """Map `otaman-<repo>` annotations to repo owners via platform.yaml.

    Annotations carry the literal `otaman-` prefix (e.g. `otaman-cli` from
    `@otaman-cli`), but `repos[].name` conventions differ across programs:
    this project's own platform.yaml names repos with the prefix intact
    (`otaman-cli`), while other otaman-managed programs commonly name repos
    without it (`sunflowers-specs`). Try the annotation as-is first, then
    fall back to the prefix-stripped form, so both conventions resolve.

    Returns ordered, deduplicated list of owner agent names.  Annotations
    that don't match any `repos[].name` (in either form) are skipped — better to
    under-notify than mis-notify — but the skip is now REPORTED by
    :func:`resolve_recipients` rather than being invisible.
    """
    owners, _reason = _resolve_owners(annotations, platform_yaml)
    return owners


def _resolve_owners(annotations: list[str], platform_yaml: Path) -> tuple[list[str], str]:
    """``(owners, reason)`` — resolved owners, and why any annotation found none."""
    by_name, reason = _owner_map(platform_yaml)
    if reason:
        return [], reason

    seen: set[str] = set()
    out: list[str] = []
    unmatched: list[str] = []
    for ann in annotations:
        owner = by_name.get(ann)
        if owner is None and ann.startswith("otaman-"):
            owner = by_name.get(ann[len("otaman-") :])
        if owner is None:
            if ann not in unmatched:
                unmatched.append(ann)
            continue
        if owner not in seen:
            seen.add(owner)
            out.append(owner)
    if unmatched:
        return out, ("these annotations match no `repos[].name`: " + ", ".join(unmatched))
    return out, ""


#: The recipients a notification falls back to when no repo owner resolved. The
#: human is on it because somebody has to notice; spec-agent because the change is
#: theirs to re-dispatch.
FALLBACK_RECIPIENTS = ["spec-agent", "human"]


def resolve_recipients(
    specs_root: Path, change_name: str, platform_yaml: Path
) -> tuple[list[str], str]:
    """``(recipients, fallback_reason)`` for a `spec-change` notification.

    The reason is empty when real owners resolved. When it is set, the recipients
    are :data:`FALLBACK_RECIPIENTS` and the reason says WHY — which five different
    situations used to collapse into, indistinguishably:

      * the change's `tasks.md` carries no `@otaman-<repo>` annotation at all
        (legitimate: nobody is assigned yet)
      * an annotation names a repo this program does not have — a typo in
        `tasks.md` routes the whole dispatch away from the agent who owns the work
      * `platform.yaml` is absent, or unreadable, or its `repos:` is the wrong
        shape, or its entries name no owners

    All five produced `["spec-agent", "human"]` with exit 0, and the message body
    then told the reader "Fallback: spec-agent, human when no annotations" — which
    is FALSE in four of the five, and false in the one that matters most: a typo'd
    annotation. The human receiving it was being told the change assigns nobody,
    when in fact the dispatch could not find the person it named.

    Mirrors `spec-change-hook.sh` for the recipient LIST; the reason is additional,
    and nothing about the list changed.
    """
    tasks_md = specs_root / "openspec" / "changes" / change_name / "tasks.md"
    if not tasks_md.is_file():
        return ["spec-agent"], ""

    annotations = _parse_at_annotations(tasks_md)
    if not annotations:
        return list(FALLBACK_RECIPIENTS), "tasks.md carries no `@otaman-<repo>` annotation"

    owners, reason = _resolve_owners(annotations, platform_yaml)
    if not owners:
        return list(FALLBACK_RECIPIENTS), (reason or "no annotation resolved to a repo owner")
    # Owners resolved, but not for every annotation — the notification reaches the
    # agents it could name, and the ones it could not are still worth saying.
    return owners, reason


def derive_recipients(specs_root: Path, change_name: str, platform_yaml: Path) -> list[str]:
    """Public — the `to:` list for a `spec-change` notification.

    Thin wrapper over :func:`resolve_recipients` for callers that do not need the
    reason. The list is unchanged from before.
    """
    recipients, _reason = resolve_recipients(specs_root, change_name, platform_yaml)
    return recipients


def _build_message(
    *,
    change_name: str,
    recipient: str,
    recipients: list[str],
    commit_hash: str,
    commit_msg: str,
    commit_author: str,
    timestamp_iso: str,
    msg_id: str,
    specs_repo_name: str,
    fallback_reason: str = "",
) -> str:
    """Render one recipient's spec-change copy.

    notify-change-fanout (2026-08-14, spec-agent 20260814T220525): the old
    single-file form put ALL recipients in one comma-joined ``to:`` field,
    which `otaman check`'s exact-match recipient filter never matched — a
    10-agent spec-change dispatch was invisible to every recipient.  Write
    one copy per recipient instead (`cli-send-cc-fanout-parity` precedent);
    each copy's ``to:`` names exactly one agent.  The full derived list
    stays visible in the body for transparency.
    """
    return (
        f"---\n"
        f"id: {msg_id}\n"
        f"from: {specs_repo_name}\n"
        f"to: {recipient}\n"
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
        f"**All recipients**: {', '.join(recipients)}\n"
        f"\n"
        # The reason this list is what it is, stated. The old text asserted "no
        # annotations" for every fallback, which was false whenever the real cause
        # was a typo'd annotation or an unreadable platform.yaml — and told the
        # human the change assigns nobody when the dispatch had in fact failed to
        # find the person it named.
        + (
            # "Why" rather than "Fallback": the reason is also set when SOME
            # annotations resolved, where nothing fell back and the recipients are
            # real owners. One sentence that is true in both cases.
            f"**Why these recipients**: {fallback_reason}\n\n"
            if fallback_reason
            else "Recipients are derived from `tasks.md` `@otaman-<repo>` annotations.\n\n"
        )
        + (
            "Use `/otaman:check` to see this notification.\n"
            "\n"
            "This message was generated by `otaman notify-change` "
            "(post-merge-spec-notify), not the post-commit hook.\n"
        )
    )


def _git_metadata(specs_root: Path) -> tuple[str, str, str]:
    """Capture HEAD commit hash / message / author for the message body."""

    def _git(*args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(specs_root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    return (
        _git("rev-parse", "--short", "HEAD") or "(no-commit)",
        _git("log", "-1", "--format=%s") or "(unknown commit message)",
        _git("log", "-1", "--format=%an") or "(unknown author)",
    )


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
        "message_paths": [],
        "map_tasks_called": False,
        "map_tasks_path": None,
        "tasks_md_path": None,
        # Empty when real owners resolved; otherwise why the recipients are the
        # fallback. Five situations used to reach the caller as one silent list.
        "fallback_reason": "",
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
    recipients, fallback_reason = resolve_recipients(specs_root, change_name, platform_yaml)
    summary["recipients"] = recipients
    # In the summary as well as the message: the caller is the one who can fix a
    # typo'd annotation, and they see the summary, not the body they just wrote.
    summary["fallback_reason"] = fallback_reason

    commit_hash, commit_msg, commit_author = _git_metadata(specs_root)
    now = datetime.now(timezone.utc)
    msg_ts = now.strftime("%Y%m%dT%H%M%S")
    iso_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    msg_id = f"{msg_ts}-{commit_hash}"

    bus_active = _resolve_bus_active(project_root)
    try:
        bus_active.mkdir(parents=True, exist_ok=True)
        (bus_active / "acks").mkdir(exist_ok=True)
    except OSError as exc:
        return 2, {**summary, "error": f"bus dir not writable: {exc}"}

    # notify-change-fanout — one copy per recipient (see _build_message).
    # The recipient goes into the filename's -to-<agent>- segment so both
    # check's glob-based tooling and ack's filename-ownership filter treat
    # these exactly like cmd_send primaries.
    message_paths: list[str] = []
    for recipient in recipients:
        body = _build_message(
            change_name=change_name,
            recipient=recipient,
            recipients=recipients,
            commit_hash=commit_hash,
            commit_msg=commit_msg,
            commit_author=commit_author,
            timestamp_iso=iso_ts,
            msg_id=msg_id,
            specs_repo_name=specs_root.name,
            fallback_reason=fallback_reason,
        )
        # The change name is part of the stem, and the write is create-exclusive.
        # Both are needed, and neither is redundant:
        #
        # Without the change name, two dispatches to one recipient in the same
        # second produced the IDENTICAL path and the second silently overwrote
        # the first — deploy-agent lost 2 of 6 changes this way on haulops
        # (20260921T190949), every call exiting 0. Second precision is not
        # enough on its own because scripted and automated dispatch is bursty
        # by nature.
        #
        # Without the exclusive write, the residual collision — the SAME change
        # re-dispatched inside one second — still loses a message. It is also
        # what `bus_write` exists for: this writer already imported that
        # module's validator while bypassing its collision-safe allocation,
        # which is precisely the gap that let a fixed bug class reappear here.
        from otaman_cli.bus_stem_gate import bus_stem

        msg_filename = bus_stem().build_filename(
            timestamp=msg_ts,
            sender=specs_root.name,
            recipient=recipient,
            slug=f"{change_name}-spec-change",
        )
        from otaman_cli.bus_write import BusMessageValidationError, write_message_exclusive

        try:
            written = write_message_exclusive(bus_active / msg_filename, body, validate=True)
        except BusMessageValidationError as exc:
            joined = "; ".join(exc.errors)
            return 2, {**summary, "error": f"message failed self-validation: {joined}"}
        except OSError as exc:
            return 2, {**summary, "error": f"failed to write message: {exc}"}
        # The RETURNED path, not the requested one: a collision suffix must reach
        # the summary or the caller reports a file that does not exist.
        message_paths.append(str(written))
    summary["message_path"] = message_paths[0] if message_paths else None
    summary["message_paths"] = message_paths

    # map-tasks dispatch (task 1.4). B3: the old _find_map_tasks_py() searched
    # dev-checkout paths (scripts/map-tasks.py) that don't exist under
    # site-packages, so installed deployments ALWAYS took the degradation branch
    # — automation silently dead, assignments hand-written. run_script dispatches
    # via SCRIPT_MAP → `otaman_plugin.map_tasks` (an in-process import), which
    # works from both a dev checkout and an installed wheel.
    if tasks_md.is_file():
        # F2 (spec-gate-hardening): route dispatch through the SAME gate as
        # `otaman assign` — an unapproved change is refused/waived here too, with
        # the violation surfaced, an audit entry written, and x-gate-waived
        # stamped. Without this the pmeets incident shape (a silent unapproved
        # dispatch) survived on the notify-change path — the flow used after a
        # GitHub merge, so it matters most.
        from otaman_cli.commands.spec import dispatch_gate_check, dispatch_waiver_slug
        from otaman_cli.identity import resolve_agent_identity

        actor = resolve_agent_identity(project_root) or "unknown-agent"
        allowed, gate_lines = dispatch_gate_check(project_root, change_name, audit_actor=actor)
        summary["gate_lines"] = gate_lines
        if not allowed:
            summary["map_tasks_called"] = False
            summary["gate_blocked"] = True
            return 0, summary  # dispatch refused by spec policy; no assignments emitted

        summary["map_tasks_path"] = "otaman_plugin.map_tasks"
        waiver_slug = dispatch_waiver_slug(project_root, change_name)
        _prev_waived = os.environ.get("OTAMAN_GATE_WAIVED")
        if waiver_slug:
            os.environ["OTAMAN_GATE_WAIVED"] = waiver_slug
        try:
            from otaman_cli.main import run_script

            result = run_script("map-tasks.py", str(tasks_md), capture=True)
            summary["map_tasks_called"] = result.returncode == 0
            if result.returncode != 0:
                summary["map_tasks_error"] = (result.stdout or "").strip()[:200] or (
                    f"map-tasks exited {result.returncode}"
                )
        except Exception as exc:  # noqa: BLE001 - dispatch failure degrades, loudly
            summary["map_tasks_called"] = False
            summary["map_tasks_error"] = str(exc)[:200]
        finally:
            if waiver_slug:
                if _prev_waived is None:
                    os.environ.pop("OTAMAN_GATE_WAIVED", None)
                else:
                    os.environ["OTAMAN_GATE_WAIVED"] = _prev_waived

    return 0, summary


def cmd_notify_change(args: list[str]) -> int:
    """`otaman notify-change <change-name>` CLI entry point (task 1.1)."""
    from otaman_cli.identity import find_project_root, not_in_project_message
    from otaman_cli.main import UI

    if not args:
        UI.error("Usage: otaman notify-change <change-name>")
        return 2
    change_name = args[0].strip()
    if not change_name:
        UI.error("change-name cannot be empty")
        return 2

    root = find_project_root()
    if root is None:
        UI.error(not_in_project_message())
        return 1

    rc, summary = notify_change(root, change_name)
    if "error" in summary:
        UI.error(summary["error"])
        return rc

    # Task 1.5 — summary
    n_copies = len(summary.get("message_paths") or [])
    UI.ok(
        f"spec-change notification written: {Path(summary['message_path']).name}"
        + (f" (+{n_copies - 1} per-recipient copies)" if n_copies > 1 else "")
    )
    UI.kv("  Change", summary["change_name"])
    UI.kv("  Recipients", ", ".join(summary["recipients"]))
    # The operator is the one who can fix a typo'd annotation, and they see THIS,
    # not the body they just wrote. A dispatch that fell back to spec-agent+human
    # looks identical to a correct one on this line unless the reason is printed.
    reason = summary.get("fallback_reason") or ""
    if reason:
        # PARTIAL resolution is its own case: some annotations resolved and some did
        # not, so the recipients are real owners and nothing fell back. Saying "fell
        # back" there would be a second false statement in the place the first one
        # was — which is the whole defect, repeated.
        if summary["recipients"] == FALLBACK_RECIPIENTS:
            UI.warn(f"  Fell back to {', '.join(FALLBACK_RECIPIENTS)}: {reason}")
            UI.muted("  No repo owner was notified — fix the annotation or platform.yaml.")
        else:
            UI.warn(f"  Partially resolved: {reason}")
            UI.muted("  The owners above were notified; the unmatched annotations were not.")
    if summary["tasks_md_path"]:
        UI.muted(f"  tasks.md: {summary['tasks_md_path']}")
    else:
        UI.muted("  tasks.md: (absent — fallback recipients used)")
    # F2 — surface the dispatch gate result VIOLATION-first, like `otaman assign`.
    for ln in summary.get("gate_lines") or []:
        UI.warn(ln)
    if summary.get("gate_blocked"):
        UI.error(f"Dispatch blocked by spec policy: '{change_name}' is not spec-approved.")
        UI.muted("Advance it to spec-approved (or `otaman ratify`), or relax enforcement.")
        return rc

    if summary["map_tasks_called"]:
        UI.ok("map-tasks dispatch invoked (otaman_plugin.map_tasks)")
    elif summary.get("map_tasks_path"):
        UI.warn(
            "map-tasks dispatch failed: "
            + summary.get("map_tasks_error", "unknown error (task-assignment dispatch deferred)")
        )
    else:
        UI.muted("  map-tasks: skipped (no tasks.md)")

    return rc


__all__ = [
    "FALLBACK_RECIPIENTS",
    "cmd_notify_change",
    "derive_recipients",
    "notify_change",
    "resolve_recipients",
]
