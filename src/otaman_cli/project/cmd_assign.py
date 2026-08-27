"""`otaman project assign <path> --owner <agent>` (tasks 4.1, 4.2).

Registers an EXISTING local git repo in `platform.yaml repos[]`. No remote
API call. Honours both duplication guards (name + resolved path).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import (
    append_repo,
    find_repo,
    git_commit_platform_yaml,
    is_git_repo,
    load_platform_yaml,
    save_platform_yaml,
)


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _detect_origin_url(repo_path: Path) -> str | None:
    """Read `git remote get-url origin`. Returns None on no remote / error."""
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    return url or None


def cmd_project_assign(
    target: str,
    *,
    owner: str | None,
    name: str | None = None,
) -> int:
    if not target:
        return _bail("Usage: otaman project assign <path> --owner <agent>")
    if not owner:
        return _bail("--owner is required")

    root = find_project_root()
    if root is None:
        return _bail("Not in an otaman project (no platform.yaml found)")

    repo_path = Path(target).expanduser().resolve()
    if not repo_path.is_dir():
        return _bail(f"Not a directory: {repo_path}")
    if not is_git_repo(repo_path):
        return _bail(f"{repo_path} is not a git repository (no .git/)")

    repo_name = name or repo_path.name

    try:
        data = load_platform_yaml(root)
    except FileNotFoundError as exc:
        return _bail(str(exc))

    # Duplication guards (Q5)
    if find_repo(data, repo_name) is not None:
        return _bail(
            f"Name {repo_name!r} is already registered. "
            f"Use `otaman project update {repo_name}` instead."
        )
    # Resolve every registered path against the meta root for comparison
    for r in data.get("repos") or []:
        if not isinstance(r, dict):
            continue
        rp = r.get("path") or ""
        if not rp:
            continue
        existing_abs = (root / rp).expanduser().resolve()
        if existing_abs == repo_path:
            return _bail(
                f"Repo at {repo_path} is already registered as {r.get('name')!r}. "
                f"Use `otaman project update`."
            )

    # Build the entry — path stored relative to root when feasible
    try:
        import os

        rel_path = os.path.relpath(repo_path, root).replace("\\", "/")
        path_field = rel_path if rel_path.startswith("..") else f"./{rel_path}"
    except ValueError:
        path_field = str(repo_path)

    entry: dict[str, Any] = {
        "name": repo_name,
        "path": path_field,
        "owner": owner,
    }
    origin = _detect_origin_url(repo_path)
    if origin:
        # Schema-accepted field is `remote:` (platform-schema.yaml repo entry).
        # CLI surface uses `--url` for user-facing readability, but on-disk
        # representation must use `remote:` to pass otaman-core validation.
        entry["remote"] = origin

    append_repo(data, entry)
    save_platform_yaml(root, data)
    UI.ok(f"Registered {repo_name} (owner: {owner}, path: {path_field})")
    if origin:
        UI.muted(f"  remote: {origin}")
    else:
        UI.muted("  no remote origin detected; remote field omitted")

    # Spec 10.5: run `otaman init` in the assigned repo so the per-repo
    # .otaman marker (with `agent: <owner>` field) gets written. Without
    # this step, downstream identity resolution from inside the assigned
    # repo would fall back to the deprecated current-agent file.
    import os as _os

    from otaman_cli.commands.init import _cmd_init_update

    prev_cwd = _os.getcwd()
    try:
        _os.chdir(root)
        init_rc = _cmd_init_update()
        if init_rc != 0:
            UI.warn(
                f"`otaman init --update` returned {init_rc}; "
                ".otaman marker may not be set for {repo_name}."
            )
    finally:
        _os.chdir(prev_cwd)

    # Commit (best-effort; surface error but don't roll back the file write)
    rc, out = git_commit_platform_yaml(
        root,
        f"feat(platform): assign repo {repo_name} (owner: {owner})",
    )
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")

    # repo-registration-materialization 1.3: registration is a platform.yaml
    # edit, not a checkout. On any host where this repo's path is absent, the
    # local tree + agent artifacts must be materialized — point the operator at
    # the command so the drift doesn't sit silently (doctor also flags it, 1.2).
    UI.muted(
        "Note: registration records the repo in platform.yaml; it does not create "
        "the checkout on other hosts. Run `otaman sync-repos` there to clone + "
        "materialize it."
    )
    return 0


__all__ = ["cmd_project_assign"]
