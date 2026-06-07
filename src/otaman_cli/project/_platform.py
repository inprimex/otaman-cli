"""Shared platform.yaml helpers for `otaman project` subcommands (task 2.2).

ruamel.yaml round-trip preserves comments + key order. All mutations go
through these helpers so the audit trail stays clean.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


# Module-shared ruamel instance — round-trip preserves comments + ordering
_YAML = YAML()
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)


def load_platform_yaml(root: Path) -> dict[str, Any]:
    """Round-trip parse of `<root>/platform.yaml`. Raises FileNotFoundError
    when missing — callers should check before invoking."""
    path = root / "platform.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"platform.yaml not found at {path}")
    return _YAML.load(path.read_text(encoding="utf-8")) or {}


def save_platform_yaml(root: Path, data: dict[str, Any]) -> None:
    """Write *data* back to `<root>/platform.yaml`, preserving comments."""
    path = root / "platform.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    _YAML.dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def append_repo(data: dict[str, Any], entry: dict[str, Any]) -> None:
    """Append *entry* to ``data['repos']``. Creates repos[] if absent.

    Mutates *data* in place.
    """
    repos = data.get("repos")
    if not isinstance(repos, list):
        repos = []
        data["repos"] = repos
    repos.append(entry)


def remove_repo(data: dict[str, Any], name: str) -> bool:
    """Remove the repos[] entry whose `name:` matches.

    Returns True when an entry was removed, False when none matched.
    """
    repos = data.get("repos") or []
    if not isinstance(repos, list):
        return False
    for i, r in enumerate(repos):
        if isinstance(r, dict) and r.get("name") == name:
            del repos[i]
            return True
    return False


def update_repo(data: dict[str, Any], name: str, fields: dict[str, Any]) -> bool:
    """Update the named repo's fields. Returns True on match.

    Fields with value None are skipped. Empty-string fields ARE applied
    (allows clearing optional fields). The `name:` key itself is immutable.
    """
    entry = find_repo(data, name)
    if entry is None:
        return False
    for k, v in fields.items():
        if k == "name":
            continue
        if v is None:
            continue
        entry[k] = v
    return True


def find_repo(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Locate a repos[] entry by `name:`. Returns the live dict (mutable)."""
    repos = data.get("repos") or []
    if not isinstance(repos, list):
        return None
    for r in repos:
        if isinstance(r, dict) and r.get("name") == name:
            return r
    return None


def git_commit_platform_yaml(root: Path, message: str) -> tuple[int, str]:
    """`git add platform.yaml && git commit -m <message>` in *root*.

    Uses inline -c args so commits work without global git identity / signing.
    Returns ``(returncode, combined_output)``. Non-zero rc is a soft fail —
    callers decide whether to surface it as a hard error.
    """
    add = subprocess.run(
        ["git", "add", "platform.yaml"],
        cwd=str(root), capture_output=True, text=True,
    )
    if add.returncode != 0:
        return add.returncode, (add.stderr or add.stdout)
    commit = subprocess.run(
        [
            "git",
            "-c", "user.email=otaman@localhost",
            "-c", "user.name=otaman",
            "-c", "commit.gpgsign=false",
            "commit", "--quiet", "-m", message,
        ],
        cwd=str(root), capture_output=True, text=True,
    )
    return commit.returncode, (commit.stderr or commit.stdout)


def is_git_repo(path: Path) -> bool:
    """True when *path*/`.git` exists (file or dir — git supports both)."""
    return (path / ".git").exists()


__all__ = [
    "load_platform_yaml",
    "save_platform_yaml",
    "append_repo",
    "remove_repo",
    "update_repo",
    "find_repo",
    "git_commit_platform_yaml",
    "is_git_repo",
]
