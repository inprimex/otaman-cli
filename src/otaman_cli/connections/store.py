"""Per-scope ``connections.yaml`` writer (agent-credential-access 3.1).

otaman-core owns the READ/cascade side (``resolve_for``); it has no write
helper, so create/update/delete land here. A connection is a mapping of
locations/identifiers only — ``{name, type, endpoint, secret_ref?, ssh_ref?,
scope?}`` — so these files never hold a secret value (spec hard invariant).

Writes target ONE scope file (the cascade is a read-time concern):
  - program: ``<program_root>/connections.yaml``
  - tenant:  ``~/.otaman/connections.yaml``
Org-scope writes need an org config dir and are out of scope for 3.1's CLI
(the resolver already reads org files when present).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The fields a connection mapping may carry, in canonical render order. Any
# other key is dropped on write — the file is a values-free location record.
CONNECTION_FIELDS = ("name", "type", "endpoint", "secret_ref", "ssh_ref", "scope")

WRITABLE_SCOPES = ("program", "tenant")


def tenant_connections_path(home: Path | None = None) -> Path:
    """``~/.otaman/connections.yaml`` — the tenant-scope write target.

    Single indirection for test isolation (monkeypatch this), mirroring the
    hitl-config / ledger pattern so no in-process test touches a real file.
    """
    base = home or Path.home()
    return base / ".otaman" / "connections.yaml"


def scope_write_path(scope: str, program_root: Path, *, home: Path | None = None) -> Path:
    """The ``connections.yaml`` file a write to *scope* targets."""
    if scope == "program":
        return program_root / "connections.yaml"
    if scope == "tenant":
        return tenant_connections_path(home)
    raise ValueError(f"scope must be one of {', '.join(WRITABLE_SCOPES)} for writes, got {scope!r}")


def _load_raw(path: Path) -> dict[str, Any]:
    """Parse a connections.yaml mapping; ``{}`` on missing/unparseable."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/malformed file → empty, never crash a read
        return {}
    return data if isinstance(data, dict) else {}


def load_connections(path: Path) -> list[dict[str, Any]]:
    """The raw connection mappings in *path* (``[]`` when absent/empty)."""
    raw = _load_raw(path)
    conns = raw.get("connections")
    return [c for c in conns if isinstance(c, dict)] if isinstance(conns, list) else []


def _clean(conn: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields with non-None values, in canonical order."""
    return {k: conn[k] for k in CONNECTION_FIELDS if conn.get(k) is not None}


def _write(path: Path, conns: list[dict[str, Any]]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"connections": [_clean(c) for c in conns]}
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False), encoding="utf-8"
    )


def find_connection(path: Path, name: str) -> dict[str, Any] | None:
    """The raw mapping named *name* in *path*, or None."""
    for c in load_connections(path):
        if c.get("name") == name:
            return c
    return None


def upsert_connection(path: Path, conn: dict[str, Any]) -> bool:
    """Insert or replace the connection by ``name``; preserve the rest.

    Returns True if an existing connection was replaced, False if inserted.
    The caller is responsible for having confirmed the metadata (the CLI's
    propose-and-confirm gate) — this is the raw persistence step.
    """
    name = conn["name"]
    conns = load_connections(path)
    replaced = False
    for i, existing in enumerate(conns):
        if existing.get("name") == name:
            conns[i] = _clean(conn)
            replaced = True
            break
    if not replaced:
        conns.append(_clean(conn))
    _write(path, conns)
    return replaced


def delete_connection(path: Path, name: str) -> bool:
    """Remove the connection named *name*; return True if one was removed."""
    conns = load_connections(path)
    kept = [c for c in conns if c.get("name") != name]
    if len(kept) == len(conns):
        return False
    _write(path, kept)
    return True


__all__ = [
    "CONNECTION_FIELDS",
    "WRITABLE_SCOPES",
    "delete_connection",
    "find_connection",
    "load_connections",
    "scope_write_path",
    "tenant_connections_path",
    "upsert_connection",
]
