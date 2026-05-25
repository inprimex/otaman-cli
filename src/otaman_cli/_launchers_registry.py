"""Per-user, per-host registry of known launcher folders.

Tracked in ``~/.otaman/launchers.yaml`` (legacy: was ``~/.maestro/launchers.yaml`` pre-rebrand). Used by ``otaman upgrade`` to
walk every launcher this user has on this machine and refresh it
(``git pull`` on the plugin checkout + ``otaman init`` on the otaman
folder each launcher targets).

Auto-maintained: the launcher scripts call ``register_launcher`` on
every successful launch, so the registry fills up as a side-effect
of normal usage. Manual management via ``otaman launcher add/remove``
covers first-time bootstrap and cleanup.

Why this module is separate: the launchers are PowerShell + Bash, but
they call out to Python for YAML parsing (Bash) or shell out to ``otaman
launcher register`` (both). Centralising the file format here keeps the
two launcher implementations consistent.

YAML shape::

    launchers:
      - path: "/home/user/launchers/foo"
        added: "2026-05-01T12:34:56+00:00"
        last_used: "2026-05-01T12:34:56+00:00"
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY_FILENAME = "launchers.yaml"


def registry_path() -> Path:
    """Return the registry file path. Honours ``MAESTRO_LAUNCHERS_REGISTRY``
    for tests, otherwise defaults to ``~/.otaman/launchers.yaml``.
    """
    override = os.environ.get("MAESTRO_LAUNCHERS_REGISTRY")
    if override:
        return Path(override)
    home = Path(os.path.expanduser("~"))
    return home / ".otaman" / REGISTRY_FILENAME  # legacy: was ~/.maestro/ pre-rebrand


def _now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _normalise(path: str | Path) -> str:
    """Canonicalise a launcher path so duplicate registrations from different
    cwds (e.g. ``./watchtower`` vs ``C:\\work\\launchers\\watchtower``) collapse
    to the same entry.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        # Don't fail on paths that no longer exist — keep them in the
        # registry until the user explicitly removes them; that lets
        # ``otaman launcher list`` surface stale entries.
        p = p.absolute()
    return str(p)


def load() -> list[dict[str, Any]]:
    """Read the registry. Returns an empty list if the file doesn't exist
    or is malformed (best-effort; the registry is auto-managed and
    self-healing).
    """
    path = registry_path()
    if not path.is_file():
        return []
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    raw = data.get("launchers")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if not entry.get("path"):
            continue
        out.append(dict(entry))
    return out


def save(entries: list[dict[str, Any]]) -> None:
    """Write the registry. Creates the parent directory if missing."""
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyYAML required to write launcher registry") from e
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"launchers": entries},
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(body, encoding="utf-8")


def register(launcher_path: str | Path) -> tuple[bool, dict[str, Any]]:
    """Add ``launcher_path`` to the registry, or update its ``last_used``
    if already present.

    Returns ``(was_new, entry)`` where ``was_new`` is True if the entry
    was added (vs already existed).
    """
    canonical = _normalise(launcher_path)
    entries = load()
    now = _now()
    for entry in entries:
        if _normalise(entry["path"]) == canonical:
            entry["last_used"] = now
            save(entries)
            return False, entry
    new_entry = {
        "path": canonical,
        "added": now,
        "last_used": now,
    }
    entries.append(new_entry)
    save(entries)
    return True, new_entry


def unregister(launcher_path: str | Path) -> bool:
    """Remove ``launcher_path`` from the registry. Returns True if found
    and removed, False if not present.
    """
    canonical = _normalise(launcher_path)
    entries = load()
    filtered = [e for e in entries if _normalise(e["path"]) != canonical]
    if len(filtered) == len(entries):
        return False
    save(filtered)
    return True


def list_entries() -> list[dict[str, Any]]:
    """Return all registered launchers, sorted by ``last_used`` descending
    so the most recently active appear first.
    """
    entries = load()
    entries.sort(key=lambda e: str(e.get("last_used", "")), reverse=True)
    return entries
