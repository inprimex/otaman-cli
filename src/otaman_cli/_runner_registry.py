"""On-disk state for `otaman runner`: the platforms symlink registry and the
stable, file-backed bearer token.

Implements `specs/otaman-runner-platforms/spec.md` (multi-program-runner-impl
task 2.3). Re-authored from closed PR #82 with the contract fixes from its
2026-08-16 review (see the PR's closing comments): sanitized program names,
target-suffix validation (the runner skips resolved targets not named
`*.yaml`), symlink OSError mapped to a friendly error, and an atomic 0600
token write with no loose-permissions window.

Two locations are the entire coupling with otaman-runner:

  - platforms dir  ``~/.config/otaman/platforms/`` (``OTAMAN_PLATFORMS_DIR``)
    -- symlinks to ``platform.yaml`` files, named ``<program-name>.yaml``.
  - token file     ``~/.config/otaman/runner.token`` (``OTAMAN_RUNNER_TOKEN_FILE``,
    a CLI-side override only — the runner reads the path solely from its
    ``--token-source file:<path>`` argument) -- single raw line
    (``stable-runner-token/spec.md`` shared format).

This is the *persistence* token the CLI bootstraps/rotates. It is distinct
from the runner-owned discovery file ``~/.otaman/runner.endpoint``, which
this module never touches.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

PLATFORMS_DIR_ENV = "OTAMAN_PLATFORMS_DIR"
TOKEN_FILE_ENV = "OTAMAN_RUNNER_TOKEN_FILE"

# Program names become symlink FILENAMES; anything path-shaped ('..', '/',
# leading dot) would escape the registry dir or crash symlink_to.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class PlatformsError(Exception):
    """Raised for `platforms add|remove` precondition failures."""


class TokenError(Exception):
    """Raised for `token rotate|show` precondition failures."""


def platforms_dir(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get(PLATFORMS_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "otaman" / "platforms"


def token_file(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get(TOKEN_FILE_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "otaman" / "runner.token"


def mask_token(token: str) -> str:
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def _ensure_private_dir(d: Path) -> None:
    """Create *d* 0700 (best-effort chmod on pre-existing dirs)."""
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# platforms add|list|remove


def _validate_name(name: str, source: Path) -> str:
    """Reject names that cannot safely become a symlink filename."""
    if not _SAFE_NAME.match(name) or name in (".", ".."):
        raise PlatformsError(
            f"{source} has 'name: {name}' which is not usable as a registry filename "
            "(allowed: letters/digits then letters/digits/dot/dash/underscore, "
            "max 64 chars — no path separators)"
        )
    return name


def _read_program_name(path: Path) -> str:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise PlatformsError(f"Failed to parse YAML: {path} ({e})") from e
    if not isinstance(data, dict):
        raise PlatformsError(f"{path} does not contain a YAML mapping")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlatformsError(
            f"{path} has no non-empty top-level 'name:' field — "
            "set 'name:' in the platform.yaml so a program name can be derived"
        )
    return _validate_name(name.strip(), path)


def _resolve_link_target(link: Path) -> Path:
    raw = Path(os.readlink(link))
    if raw.is_absolute():
        return raw
    return (link.parent / raw).resolve()


def _symlink(link: Path, target: Path) -> None:
    """symlink_to with OSError mapped to PlatformsError (e.g. Windows without
    symlink privilege — Developer Mode / admin required)."""
    try:
        link.symlink_to(target)
    except OSError as e:
        raise PlatformsError(
            f"Could not create symlink {link} -> {target}: {e}. "
            "On Windows, creating symlinks requires Developer Mode or an "
            "elevated shell."
        ) from e


def platforms_add(
    target: str | Path, *, force: bool = False, dir_override: str | None = None
) -> dict[str, Any]:
    target_path = Path(target).expanduser()
    if not target_path.is_file():
        raise PlatformsError(f"Not a readable file: {target_path}")
    resolved = target_path.resolve()
    # The runner's registry scan skips any entry whose RESOLVED target is not
    # literally *.yaml (otaman-runner platforms.py) — registering such a
    # target would report success while the runner silently never serves it.
    if resolved.suffix != ".yaml":
        raise PlatformsError(
            f"{resolved} does not end in .yaml — the runner only serves *.yaml "
            "targets; rename the file (e.g. platform.yml -> platform.yaml) and retry"
        )
    name = _read_program_name(resolved)

    pdir = platforms_dir(dir_override)
    _ensure_private_dir(pdir)

    link = pdir / f"{name}.yaml"
    if link.is_symlink():
        existing_target = _resolve_link_target(link)
        if existing_target == resolved:
            return {"status": "already-installed", "name": name, "link": link, "target": resolved}
        if not force:
            raise PlatformsError(
                f"'{name}.yaml' already links to {existing_target}; refusing to point it at "
                f"{resolved} without --force"
            )
        link.unlink()
    elif link.exists():
        if not force:
            raise PlatformsError(
                f"{link} exists and is not a symlink this tool manages (use --force to replace)"
            )
        link.unlink()

    _symlink(link, resolved)
    return {"status": "installed", "name": name, "link": link, "target": resolved}


def platforms_list(*, dir_override: str | None = None) -> list[dict[str, Any]]:
    """Entries carry a single ``state``: ``ok`` | ``dangling`` | ``unmanaged``."""
    pdir = platforms_dir(dir_override)
    if not pdir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(pdir.iterdir()):
        if entry.suffix != ".yaml":
            continue
        name = entry.stem
        if entry.is_symlink():
            target = _resolve_link_target(entry)
            state = "ok" if target.exists() else "dangling"
            out.append({"name": name, "target": target, "state": state})
        else:
            out.append({"name": name, "target": entry.resolve(), "state": "unmanaged"})
    return out


def platforms_remove(name: str, *, dir_override: str | None = None) -> dict[str, Any]:
    _validate_name(name, platforms_dir(dir_override))
    pdir = platforms_dir(dir_override)
    link = pdir / f"{name}.yaml"
    if not link.exists() and not link.is_symlink():
        raise PlatformsError(f"'{name}' is not registered ({link} not found)")
    if not link.is_symlink():
        raise PlatformsError(
            f"{link} is a regular file, not a symlink this tool manages — remove it by hand"
        )
    link.unlink()
    return {"name": name, "link": link}


# ---------------------------------------------------------------------------
# token install|rotate|show


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _write_token(path: Path, value: str) -> None:
    """Atomic write, 0600 from creation: the token never exists on disk with
    loose permissions, and a runner re-reading on SIGHUP can never observe a
    partially-written file."""
    _ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (value + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def token_install(*, force: bool = False, file_override: str | None = None) -> dict[str, Any]:
    """Bootstrap or (with force=True) regenerate the persistence token.

    Confirmation for the force+existing-file case is the CLI layer's job —
    this function performs the write unconditionally when told to.
    """
    path = token_file(file_override)
    existed = path.is_file()
    if existed and not force:
        existing = path.read_text(encoding="utf-8").strip()
        return {"status": "already-installed", "path": path, "token": existing}
    value = _generate_token()
    _write_token(path, value)
    return {"status": "reinstalled" if existed else "installed", "path": path, "token": value}


def token_rotate(*, file_override: str | None = None) -> dict[str, Any]:
    path = token_file(file_override)
    if not path.is_file():
        raise TokenError(f"No token installed at {path} — run 'otaman runner token install' first")
    value = _generate_token()
    _write_token(path, value)
    return {"path": path, "token": value}


def token_show(*, file_override: str | None = None) -> dict[str, Any]:
    path = token_file(file_override)
    if not path.is_file():
        raise TokenError(f"No token installed at {path} — run 'otaman runner token install' first")
    value = path.read_text(encoding="utf-8").strip()
    mode = stat.S_IMODE(path.stat().st_mode)
    return {"path": path, "token": value, "mode": mode}
