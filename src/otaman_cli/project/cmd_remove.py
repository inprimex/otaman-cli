"""`otaman project remove <name>` (task 8.1).

Without `--delete-remote`: just removes the repos[] entry, leaves local dir.
With `--delete-remote`: requires TTY type-to-confirm; calls
`adapter.delete_repo()` via otaman_core.git_host (CVS-dependent; needs
core-agent's 1.x tasks shipped first).

Local-only removal path works today regardless of core's state.
"""

from __future__ import annotations

import sys

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.project._platform import (
    find_repo,
    git_commit_platform_yaml,
    load_platform_yaml,
    remove_repo,
    save_platform_yaml,
)


def cmd_project_remove(name: str, *, delete_remote: bool = False) -> int:
    if not name:
        UI.error("Usage: otaman project remove <name> [--delete-remote]")
        return 1
    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project")
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

    # Spec Q6: --delete-remote refuses in non-TTY.  Order matters — only
    # check TTY once we've confirmed the repo exists, so unknown-repo
    # errors report unknown-repo, not TTY (better operator UX).
    if delete_remote:
        if not sys.stdin.isatty():
            UI.error("--delete-remote requires interactive TTY (refusing in non-TTY).")
            UI.muted("  Remove the local entry first with: otaman project remove <name>")
            UI.muted("  Then delete the remote repo manually via your provider.")
            return 1
        # CVS-dependent path — gated on core-agent 1.x tasks shipping.
        # For now, surface a clear "not yet wired" message instead of failing
        # mid-call. When core ships, replace this branch with the real adapter call.
        UI.warn("--delete-remote is not yet wired up (depends on otaman-core 1.x tasks).")
        UI.muted("  Proceeding with local-only removal. Delete the remote repo manually.")

    if not remove_repo(data, name):
        UI.error(f"Failed to remove {name}")
        return 1
    save_platform_yaml(root, data)
    UI.ok(f"Removed {name} from platform.yaml (local dir intact)")
    rc, out = git_commit_platform_yaml(
        root, f"chore(platform): remove repo {name}",
    )
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")
    return 0


__all__ = ["cmd_project_remove"]
