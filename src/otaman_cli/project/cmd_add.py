"""`otaman project add <name> --owner <agent>` (project-add-and-delete-remote 1.1).

Creates the repo — remotely via the git-host adapter when `git_host:` is
configured AND its token resolves, local-only (with a warning) otherwise —
then clones/inits it beside the meta repo, registers it in `platform.yaml`
`repos[]`, runs `otaman init`, and commits.

Two ordering rules carry the spec's atomicity requirement:

* the repos[] entry is written BEFORE `otaman init` runs, so an init failure
  has something to roll back — on failure the entry is removed and the
  command exits non-zero, while the directory is deliberately LEFT on disk
  for inspection (deleting a fresh clone would destroy the only local copy
  of a remote we just created);
* nothing touches `platform.yaml` until the directory exists, so the
  already-exists and remote-creation failures leave the file unmodified.

No provider logic lives here — `otaman_core.git_host` owns it all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.project._platform import (
    append_repo,
    find_repo,
    git_commit_platform_yaml,
    load_platform_yaml,
    remove_repo,
    save_platform_yaml,
)
from otaman_cli.project.launch_scaffold import build_launch_block, owner_refusal
from otaman_cli.ui import UI

# Providers otaman_core.git_host.get_adapter() can build. Kept here only to
# give `--provider junk` a clear error BEFORE a token is resolved; core
# remains the single authority and raises its own GitHostError if this list
# ever drifts ahead of it.
_SUPPORTED_PROVIDERS = ("github", "gitlab", "bitbucket", "azure-devops", "gitea", "forgejo")


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _known_agents(root: Path) -> set[str]:
    """agents.yaml names, or an empty set on any failure (no registry to check)."""
    try:
        from otaman_core.validate_message import load_known_agents

        return load_known_agents(root)
    except Exception:  # noqa: BLE001 - absent core/agents.yaml → nothing to validate
        return set()


def _resolve_adapter(
    root: Path,
    *,
    provider_override: str | None,
) -> tuple[Any | None, Any | None, str | None]:
    """``(adapter, cfg, reason)``.

    ``reason`` is a human-readable explanation when no adapter could be
    built; it is None on success. A None adapter with a reason is the
    local-only path, NOT an error — except for an unsupported
    ``--provider``, which the caller rejects outright.
    """
    try:
        from otaman_core.git_host import GitHostError, get_adapter, load_git_host_config
    except ImportError as exc:  # pragma: no cover - otaman-core always present in-tree
        return None, None, f"otaman_core.git_host unavailable ({exc})"

    cfg = load_git_host_config(root)
    if cfg is None:
        return None, None, "no `git_host:` block in platform.yaml"

    if provider_override:
        # Route to the named provider, keeping the configured token/host.
        # `host` must follow the provider or we would call GitLab's API at
        # github.com; re-deriving the SaaS default is the only safe move,
        # and an empty default (gitea/forgejo are always self-hosted) means
        # the override cannot be honoured without a host in platform.yaml.
        from dataclasses import replace

        from otaman_core.git_host import default_host_for

        host = default_host_for(provider_override)
        if not host:
            return (
                None,
                cfg,
                f"--provider {provider_override} is always self-hosted; "
                f"set `git_host.host:` in platform.yaml to use it",
            )
        cfg = replace(cfg, provider=provider_override, host=host)

    try:
        return get_adapter(cfg, maestro_root=root), cfg, None
    except GitHostError as exc:
        return None, cfg, str(exc)


def _git_init(target: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["git", "init", "-q"],
        cwd=str(target),
        capture_output=True,
        text=True,
    )
    return r.returncode, (r.stderr or r.stdout)


def _git_clone(clone_url: str, target: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["git", "clone", "--quiet", clone_url, str(target)],
        capture_output=True,
        text=True,
    )
    return r.returncode, (r.stderr or r.stdout)


def _run_init_update(root: Path) -> tuple[int, str]:
    """`otaman init --update` with *root* as cwd. ``(rc, error_text)``.

    An exception is folded into a non-zero rc so the caller has one
    rollback path for both failure shapes the spec names.
    """
    from otaman_cli.commands.init import _cmd_init_update

    prev_cwd = os.getcwd()
    try:
        os.chdir(root)
        rc = _cmd_init_update()
        return rc, "" if rc == 0 else f"`otaman init --update` returned {rc}"
    except Exception as exc:  # noqa: BLE001 - any init failure must roll back, not crash
        return 1, f"`otaman init --update` failed: {exc}"
    finally:
        os.chdir(prev_cwd)


def cmd_project_add(
    name: str,
    *,
    owner: str | None,
    org: str | None = None,
    provider: str | None = None,
    description: str = "",
    private: bool = True,
) -> int:
    if not name:
        return _bail("Usage: otaman project add <name> --owner <agent>")
    # Prompts are never issued, in a TTY or out of it, so a missing --owner is
    # a usage error everywhere — which is what the non-TTY requirement asks for.
    if not owner:
        return _bail("--owner is required: otaman project add <name> --owner <agent>")
    if provider and provider not in _SUPPORTED_PROVIDERS:
        return _bail(
            f"Unsupported --provider {provider!r}. Supported: {', '.join(_SUPPORTED_PROVIDERS)}.",
            2,
        )

    root = find_project_root()
    if root is None:
        return _bail(not_in_project_message())

    try:
        data = load_platform_yaml(root)
    except FileNotFoundError as exc:
        return _bail(str(exc), 2)

    if find_repo(data, name) is not None:
        return _bail(
            f"Name {name!r} is already registered. Use `otaman project update {name}` instead."
        )

    known_agents = _known_agents(root)
    refusal = owner_refusal(owner, known_agents)
    if refusal:
        return _bail(refusal)

    # Sibling of the meta repo — the layout every other repos[] path assumes.
    target = root.parent / name
    if target.exists():
        return _bail(f"Directory `{name}` already exists.")

    adapter, cfg, reason = _resolve_adapter(root, provider_override=provider)
    remote_url: str | None = None

    if adapter is None:
        UI.warn("No token found. Creating local repo only.")
        if reason:
            UI.muted(f"  reason: {reason}")
        target.mkdir(parents=True)
        rc, out = _git_init(target)
        if rc != 0:
            # Nothing registered yet; clean up the empty dir we just made so a
            # retry isn't blocked by the already-exists guard.
            try:
                target.rmdir()
            except OSError:
                pass
            return _bail(f"git init failed in {target}: {out.strip()[:200]}")
        UI.ok(f"Created local repo at {target} (no remote)")
    else:
        from otaman_core.git_host import GitHostError

        target_org = org or getattr(cfg, "org", None)
        try:
            info = adapter.create_repo(
                name,
                target_org,
                private,
                description,
            )
        except GitHostError as exc:
            return _bail(f"Remote creation failed: {exc}")
        remote_url = info.clone_url
        UI.ok(f"Created remote {info.html_url}")
        rc, out = _git_clone(info.clone_url, target)
        if rc != 0:
            # The remote EXISTS at this point — say so, or the operator
            # retries and hits an already-exists error from the provider
            # with no idea why.
            UI.error(f"Clone failed: {out.strip()[:200]}")
            UI.muted(f"  The remote was created and still exists: {info.html_url}")
            UI.muted(f"  Clone it manually, then: otaman project assign {target} --owner {owner}")
            return 1
        UI.ok(f"Cloned into {target}")

    # --- platform.yaml mutation starts here; everything above is rollback-free.
    rel = os.path.relpath(target, root).replace("\\", "/")
    path_field = rel if rel.startswith("..") else f"./{rel}"
    entry: dict[str, Any] = {"name": name, "path": path_field, "owner": owner}
    if remote_url:
        entry["remote"] = remote_url
    if description:
        entry["description"] = description
    entry["launch"] = build_launch_block(data, name, owner)
    append_repo(data, entry)
    save_platform_yaml(root, data)
    UI.ok(f"Registered {name} (owner: {owner}, path: {path_field})")

    init_rc, init_err = _run_init_update(root)
    if init_rc != 0:
        # Spec: roll the entry back, LEAVE the directory, exit non-zero with
        # the init error. Re-read from disk first — init may have rewritten
        # platform.yaml before failing, and rolling back a stale in-memory
        # copy would silently revert whatever it wrote.
        try:
            current = load_platform_yaml(root)
        except FileNotFoundError:
            current = data
        remove_repo(current, name)
        save_platform_yaml(root, current)
        UI.error(init_err)
        UI.muted(f"  Rolled back the repos[] entry for {name}; platform.yaml is unchanged.")
        UI.muted(f"  The directory is left for inspection: {target}")
        return 1

    rc, out = git_commit_platform_yaml(
        root,
        f"feat(platform): add repo {name} (owner: {owner})",
    )
    if rc != 0:
        UI.warn(f"git commit failed (file written): {out.strip()[:120]}")
    return 0


__all__ = ["cmd_project_add"]
