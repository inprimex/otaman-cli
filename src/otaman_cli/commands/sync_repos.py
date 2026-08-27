"""`otaman sync-repos` — materialize registered-but-absent repos (task 1.1).

Registration (`otaman project assign` / `add`) edits ``platform.yaml`` but
changes nothing on disk: a repo can be registered with a ``path`` that does
not exist locally. This command closes that gap. For every repo registered
with an absent ``path`` it clones the repo from its registered ``remote``
into place, then (re)generates the repo's agent artifacts — the ``.otaman``
marker and the gitignored ``CLAUDE.local.md`` orchestration rules — via the
same ``init --update`` path used elsewhere. It is idempotent (existing
checkouts are left untouched beyond artifact regeneration), supports
``--dry-run``, and reports per-repo outcomes honestly (a generation failure
is never reported as success).

Design (task 1.1, implementer's call — recorded for spec-agent): a
STANDALONE command, not folded into ``otaman upgrade``. ``upgrade`` walks
the launcher registry refreshing whole programs across hosts (git pull +
init per launcher); ``sync-repos`` operates within ONE program on its
registered repos. Different axis, so a discoverable verb that ``doctor``
and ``project assign`` can name as the fix hint.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_program_root
from otaman_cli.main import UI

# The two artifacts that constitute a materialized repo (spec: `.otaman`
# marker + CLAUDE.local.md orchestration rules). Both must exist for a repo
# to count as fully materialized.
_MARKER = ".otaman"
_RULES = "CLAUDE.local.md"


def _load_platform(root: Path) -> dict[str, Any] | None:
    """Read + validate the program ``platform.yaml`` (shares init's guard).

    Rejects a stale/partial ORG-LEVEL platform.yaml (no ``project`` key) the
    same way ``init --update`` does — org-level files are dead/untrusted
    (incident 20260816) and would mislead the repo loop.
    """
    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        UI.error(f"platform.yaml not found at {platform_yaml}")
        return None
    try:
        import yaml as _yaml

        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 - surface any parse error as a clean bail
        UI.error(f"Failed to read platform.yaml: {e}")
        return None
    if not isinstance(config, dict) or not config.get("project"):
        UI.error(
            f"platform.yaml at {platform_yaml} has no 'project' key — this looks "
            "like a stale/partial org-level file, not a program platform.yaml."
        )
        return None
    return config


def _git_clone(remote: str, dest: Path) -> tuple[bool, str]:
    """``git clone -- <remote> <dest>``. The ``--`` guards against a remote
    or path that begins with ``-`` being read as a git option."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "clone", "--", remote, str(dest)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "git clone failed").strip()
    return True, ""


def _is_materialized(repo_dir: Path) -> bool:
    """Both the marker and the rules file are present."""
    return (repo_dir / _MARKER).is_file() and (repo_dir / _RULES).is_file()


def cmd_sync_repos(args: list[str]) -> int:
    """Clone registered-but-absent repos + regenerate their agent artifacts.

    Flags:
      --dry-run   report what would be cloned/regenerated; write nothing.
    """
    dry_run = False
    positional: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--dry-run", "-n"):
            dry_run = True
        elif a in ("--path", "--root") and i + 1 < len(args):
            positional.append(args[i + 1])
            i += 1
        else:
            positional.append(a)
        i += 1

    root = Path(positional[0]).resolve() if positional else find_program_root()
    if not root:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    config = _load_platform(root)
    if config is None:
        return 2

    UI.header("Otaman Sync Repos" + (" (dry-run)" if dry_run else ""))

    repos = [r for r in (config.get("repos") or []) if isinstance(r, dict)]
    # Classify every registered repo before doing any work.
    to_clone: list[dict[str, Any]] = []  # absent + has remote → clonable
    no_remote: list[dict[str, Any]] = []  # absent + no remote → cannot materialize
    present: list[dict[str, Any]] = []  # path exists → regenerate only

    for repo in repos:
        rel = repo.get("path") or ""
        if not rel:
            continue  # unpathed entry — nothing to materialize on disk
        repo_dir = (root / rel).resolve()
        name = repo.get("name") or rel
        remote = repo.get("remote") or ""
        entry = {"name": name, "dir": repo_dir, "remote": remote, "rel": rel}
        if repo_dir.is_dir():
            present.append(entry)
        elif remote:
            to_clone.append(entry)
        else:
            no_remote.append(entry)

    if not repos:
        UI.muted("No repos registered in platform.yaml — nothing to materialize.")
        return 0

    # ---- Dry-run: report the plan, touch nothing. -------------------------
    if dry_run:
        for e in to_clone:
            UI.info(f"would clone {e['name']} ← {e['remote']} → {e['rel']}")
        # Only present repos that are actually MISSING artifacts need work;
        # a fully-materialized repo is reported as up-to-date (not "would
        # regenerate", which read as false staleness — spec-agent gate note 2).
        present_materialized = 0
        for e in present:
            if _is_materialized(e["dir"]):
                present_materialized += 1
                UI.muted(f"{e['name']}: present and materialized (no action)")
            else:
                UI.info(f"would materialize {e['name']} (marker/rules missing)")
        for e in no_remote:
            UI.error(f"cannot materialize {e['name']}: registered path absent and no remote set")
        present_needs_work = len(present) - present_materialized
        UI.muted(
            f"\nPlan: {len(to_clone)} to clone, {present_needs_work} to (re)materialize, "
            f"{present_materialized} already materialized, {len(no_remote)} un-materializable. "
            "Re-run without --dry-run to apply."
        )
        return 1 if no_remote else 0

    # ---- Clone absent repos. ---------------------------------------------
    cloned: list[dict[str, Any]] = []
    clone_failed: list[tuple[dict[str, Any], str]] = []
    for e in to_clone:
        ok, err = _git_clone(e["remote"], e["dir"])
        if ok:
            UI.ok(f"cloned {e['name']} ← {e['remote']}")
            cloned.append(e)
        else:
            UI.error(f"clone failed {e['name']}: {err[:160]}")
            clone_failed.append((e, err))

    # ---- Regenerate artifacts for every present/cloned repo. --------------
    # `_cmd_init_update` writes each repo's `.otaman` marker (with agent:
    # field) and runs generate-agent-config once to (re)write CLAUDE.local.md
    # across the program. It only touches repos whose directory now exists, so
    # running it after the clones materializes markers+rules for all of them.
    materialize_targets = present + cloned
    gen_rc = 0
    if materialize_targets:
        import os as _os

        from otaman_cli.commands.init import _cmd_init_update

        prev = _os.getcwd()
        try:
            _os.chdir(root)
            gen_rc = _cmd_init_update()
        finally:
            _os.chdir(prev)

    # ---- Honest per-repo verification. -----------------------------------
    # A clone that succeeded but whose artifacts are absent (generation
    # crashed) must NOT be reported as materialized.
    materialized: list[str] = []
    gen_failed: list[str] = []
    for e in cloned:
        (materialized if _is_materialized(e["dir"]) else gen_failed).append(e["name"])
    regenerated: list[str] = []
    for e in present:
        (regenerated if _is_materialized(e["dir"]) else gen_failed).append(e["name"])

    # ---- Summary. ---------------------------------------------------------
    print()
    if materialized:
        UI.subheader(f"Materialized ({len(materialized)}):")
        for n in materialized:
            UI.ok(n)
    if regenerated:
        UI.subheader(f"Already present, artifacts regenerated ({len(regenerated)}):")
        for n in regenerated:
            UI.info(n)
    failures: list[str] = []
    if gen_failed:
        UI.subheader(f"Artifact generation failed ({len(gen_failed)}):")
        for n in gen_failed:
            UI.error(f"{n}: cloned/present but marker or rules missing — see generator error above")
        failures.extend(gen_failed)
    if clone_failed:
        UI.subheader(f"Clone failed ({len(clone_failed)}):")
        for e, _err in clone_failed:
            UI.error(e["name"])
        failures.extend(e["name"] for e, _ in clone_failed)
    if no_remote:
        UI.subheader(f"Cannot materialize — registered but no remote ({len(no_remote)}):")
        for e in no_remote:
            UI.error(f"{e['name']}: path {e['rel']} absent and no remote set to clone from")
        failures.extend(e["name"] for e in no_remote)

    if not failures and not materialized and not regenerated:
        UI.ok("All registered repos already materialized — nothing to do.")

    # A non-zero generator rc with no per-repo artifact gap still signals the
    # program-wide generation didn't fully succeed; fail closed.
    return 1 if (failures or gen_rc != 0) else 0


register(
    CommandSpec(
        name="sync-repos",
        handler=cmd_sync_repos,
        help="Clone registered-but-absent repos + regenerate their agent artifacts",
    )
)

__all__ = ["cmd_sync_repos"]
