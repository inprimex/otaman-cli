"""`otaman project remove <name>` (project-add-and-delete-remote 1.2).

Without `--delete-remote`: removes the repos[] entry, leaves the local dir
and makes no git-host API call at all.

With `--delete-remote`: refuses outright in non-TTY, and in a TTY requires
type-to-confirm of the exact repo name before calling
`adapter.delete_repo()` via `otaman_core.git_host`. A mismatch aborts with
NOTHING changed — no deregistration either, because an operator who
mistyped the name has not yet told us which of the two acts they wanted.

Order of operations is deliberate: the remote is deleted BEFORE the entry is
dropped, so a failed deletion leaves the repo still registered and therefore
still findable. The reverse order can strand a live remote with no record of
it in platform.yaml.
"""

from __future__ import annotations

import sys

from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.project._platform import (
    find_repo,
    git_commit_platform_yaml,
    load_platform_yaml,
    remove_repo,
    save_platform_yaml,
)
from otaman_cli.ui import UI


def _confirm_exact_name(name: str) -> bool:
    """Type-to-confirm: only the exact repo name proceeds."""
    UI.warn(f"This will DELETE the remote repository for {name!r}. This cannot be undone.")
    try:
        typed = input(f"  Type the repo name to confirm ({name}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return typed == name


def _delete_remote(root, entry, name: str) -> tuple[bool, str]:
    """``(deleted, message)``. A False with a message is a hard failure."""
    try:
        from otaman_core.git_host import (
            GitHostError,
            get_adapter,
            load_git_host_config,
            parse_remote_url,
        )
    except ImportError as exc:  # pragma: no cover - otaman-core always present in-tree
        return False, f"otaman_core.git_host unavailable ({exc})"

    cfg = load_git_host_config(root)
    if cfg is None:
        return False, "no `git_host:` block in platform.yaml — cannot reach the provider"

    remote = (entry or {}).get("remote") or ""
    if not remote:
        return False, f"{name} has no `remote:` recorded — nothing to delete remotely"

    info = parse_remote_url(remote, provider_hint=cfg.provider)
    if info is None:
        return False, f"could not parse the recorded remote URL: {remote!r}"

    try:
        adapter = get_adapter(cfg, maestro_root=root)
        adapter.delete_repo(info.owner, info.repo)
    except GitHostError as exc:
        return False, str(exc)
    return True, f"Deleted remote {info.slug}"


def cmd_project_remove(name: str, *, delete_remote: bool = False) -> int:
    if not name:
        UI.error("Usage: otaman project remove <name> [--delete-remote]")
        return 1
    root = find_project_root()
    if root is None:
        UI.error(not_in_project_message())
        return 1
    try:
        data = load_platform_yaml(root)
    except FileNotFoundError as exc:
        UI.error(str(exc))
        return 2

    entry = find_repo(data, name)
    if entry is None:
        UI.error(f"Repo not found: {name}")
        return 1

    # Spec Q6: --delete-remote refuses in non-TTY. Order matters — only
    # check TTY once we've confirmed the repo exists, so unknown-repo
    # errors report unknown-repo, not TTY (better operator UX).
    if delete_remote:
        if not sys.stdin.isatty():
            UI.error("--delete-remote requires interactive TTY (refusing in non-TTY).")
            UI.muted("  Remove the local entry first with: otaman project remove <name>")
            UI.muted("  Then delete the remote repo manually via your provider.")
            return 1
        if not _confirm_exact_name(name):
            UI.error("Name did not match — aborted. Nothing was changed.")
            return 1
        deleted, message = _delete_remote(root, entry, name)
        if not deleted:
            UI.error(f"Remote deletion failed: {message}")
            UI.muted(f"  {name} is still registered in platform.yaml (nothing was changed).")
            UI.muted(f"  To deregister locally only: otaman project remove {name}")
            return 1
        UI.ok(message)

    if not remove_repo(data, name):
        UI.error(f"Failed to remove {name}")
        return 1
    save_platform_yaml(root, data)
    UI.ok(f"Removed {name} from platform.yaml (local dir intact)")
    rc, out = git_commit_platform_yaml(
        root,
        f"chore(platform): remove repo {name}",
    )
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")
    return 0


__all__ = ["cmd_project_remove"]
