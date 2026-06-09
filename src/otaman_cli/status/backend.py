"""StatusBackend protocol + FileStatusBackend (CE) + NatsKvStatusBackend stub (EE).

Tasks 1.2, 1.3, 1.4.

Design.md Q5 — backend selected from `platform.yaml` `bus.transport`:
    `file` (default) → FileStatusBackend
    `nats`           → NatsKvStatusBackend (stub: raises NotImplementedError)

Design.md Q1 — feature switch `platform.agent_presence` (default True).
When False, all backend writes are no-ops and `read_all` returns [].
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from otaman_cli.status.models import AgentStatus, State


# ---------------------------------------------------------------- feature switch
def is_agent_presence_enabled(root: Path) -> bool:
    """Return platform.yaml's `agent_presence` flag (default True).

    Defensive: any read/parse failure → enabled (fail-open for visibility).
    """
    pyaml = root / "platform.yaml"
    if not pyaml.is_file():
        return True
    try:
        doc = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return True
    if not isinstance(doc, dict):
        return True
    val = doc.get("agent_presence")
    if val is None:
        # also accept `platform.agent_presence` nested form
        plat = doc.get("platform") if isinstance(doc.get("platform"), dict) else {}
        val = plat.get("agent_presence") if isinstance(plat, dict) else None
    if val is None:
        return True
    return bool(val)


def _bus_transport(root: Path) -> str:
    """Return platform.yaml's `bus.transport` field; default `file`."""
    pyaml = root / "platform.yaml"
    if not pyaml.is_file():
        return "file"
    try:
        doc = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return "file"
    bus = doc.get("bus") if isinstance(doc, dict) else None
    if isinstance(bus, dict):
        return str(bus.get("transport") or "file")
    return "file"


# ---------------------------------------------------------------- protocol
@runtime_checkable
class StatusBackend(Protocol):
    """Singleton-per-agent storage. Implementations: file, NATS KV."""

    def write(self, status: AgentStatus) -> None: ...
    def read(self, agent: str) -> AgentStatus | None: ...
    def read_all(self) -> list[AgentStatus]: ...
    def delete(self, agent: str) -> None: ...


# ---------------------------------------------------------------- file backend (CE)
class FileStatusBackend:
    """CE default — one YAML file per agent under `.agents/status/`.

    Writes are atomic (write to a temp file, fsync, os.replace into place) so a
    concurrent reader never sees a partial record.
    """

    def __init__(self, root: Path, *, enabled: bool = True):
        self._root = root
        self._enabled = enabled
        self._dir = root / ".agents" / "status"

    @property
    def root(self) -> Path:
        return self._root

    @property
    def dir(self) -> Path:
        return self._dir

    def _path(self, agent: str) -> Path:
        return self._dir / f"{agent}.yaml"

    def write(self, status: AgentStatus) -> None:
        if not self._enabled:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        data = status.to_dict()
        # Atomic write: tmpfile in the same dir + os.replace
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{status.agent}.", suffix=".yaml.tmp", dir=str(self._dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path(status.agent))
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def read(self, agent: str) -> AgentStatus | None:
        p = self._path(agent)
        if not p.is_file():
            return None
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return AgentStatus.from_dict(data)

    def read_all(self) -> list[AgentStatus]:
        if not self._enabled:
            return []
        if not self._dir.is_dir():
            return []
        out: list[AgentStatus] = []
        for p in sorted(self._dir.glob("*.yaml")):
            if p.name.startswith("."):
                continue
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            out.append(AgentStatus.from_dict(data))
        return out

    def delete(self, agent: str) -> None:
        if not self._enabled:
            return
        p = self._path(agent)
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- NATS backend (EE stub)
class NatsKvStatusBackend:
    """EE backend — NATS KV bucket `otaman.status`, key = agent name.

    v1 stub: raises NotImplementedError on every method.  The full
    implementation lands with EE multi-host orchestration; the class exists
    now so `get_backend()` can dispatch on `bus.transport == "nats"` without
    a circular import.
    """

    BUCKET = "otaman.status"

    def __init__(self, root: Path, *, enabled: bool = True):
        self._root = root
        self._enabled = enabled

    def _raise_ee_only(self, op: str) -> None:
        raise NotImplementedError(
            f"NatsKvStatusBackend.{op} requires EE (NATS KV).  "
            "Set `bus.transport: file` in platform.yaml for CE."
        )

    def write(self, status: AgentStatus) -> None:
        if not self._enabled:
            return
        self._raise_ee_only("write")

    def read(self, agent: str) -> AgentStatus | None:
        self._raise_ee_only("read")
        return None  # unreachable

    def read_all(self) -> list[AgentStatus]:
        if not self._enabled:
            return []
        self._raise_ee_only("read_all")
        return []  # unreachable

    def delete(self, agent: str) -> None:
        if not self._enabled:
            return
        self._raise_ee_only("delete")


# ---------------------------------------------------------------- factory
def get_backend(root: Path) -> StatusBackend:
    """Resolve the backend implementation for *root* per `bus.transport`."""
    enabled = is_agent_presence_enabled(root)
    transport = _bus_transport(root).lower()
    if transport == "nats":
        return NatsKvStatusBackend(root, enabled=enabled)
    # `file` and any unknown value fall through to FileStatusBackend
    return FileStatusBackend(root, enabled=enabled)


__all__ = [
    "StatusBackend",
    "FileStatusBackend",
    "NatsKvStatusBackend",
    "get_backend",
    "is_agent_presence_enabled",
]
