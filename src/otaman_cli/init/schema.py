"""LaunchSettings Pydantic model + load_settings() merge (tasks 1.1, 1.5).

The schema describes `launch-settings.yaml` (the committed launcher config)
and `launch-settings.local.yaml` (the gitignored per-developer override).
`load_settings()` reads both files at launcher runtime, applying local on top
of base via scalar key-path override.

Design constraints:
- `spec-agent` MUST be present and enabled — validator rejects otherwise
- `connection.mode` ∈ {local, ssh, mesh}; `ssh` requires `ssh.host` + `ssh.user`
- `tmux.layout` ∈ tiled / main-horizontal / main-vertical / even-horizontal
- Missing local file → silently use base only (not an error)
- Local file is a SPARSE override — only keys that differ; merge is
  scalar key-path replacement, not deep dict/list merge
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ConnectionMode = Literal["local", "ssh", "mesh"]
TmuxLayout = Literal[
    "tiled",
    "main-horizontal",
    "main-vertical",
    "even-horizontal",
]


class SSHParams(BaseModel):
    """SSH connection parameters. Required when `connection.mode == 'ssh'`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str
    user: str
    key_path: str | None = Field(default=None, alias="key_path")
    # Absolute path of the otaman meta dir ON THE REMOTE HOST — the base the
    # launcher resolves agent-repo dirs against in ssh mode. Optional: when
    # unset, panes print run-this-yourself guidance instead of cd'ing blind.
    remote_root: str | None = Field(default=None, alias="remote_root")

    @field_validator("host", "user")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v


class MeshParams(BaseModel):
    """Runner-mediated (Path B) spawn parameters. Required when
    `connection.mode == 'mesh'` (mirror of the ssh validator, init-wizard-mesh-mode
    D2). The launcher spawns via the runner HTTP API at a network-reachable
    endpoint — no SSH, no tmux.

    Provide EITHER ``runner_uri`` (``runner://host:port``) OR ``host`` + ``port``
    (assembled into that URI) — both is a validation error. ``token_source`` names
    where the launcher reads the runner token at runtime; the value is NEVER
    embedded in the generated launcher (default: ``OTAMAN_RUNNER_TOKEN`` env)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    runner_uri: str | None = Field(default=None, alias="runner_uri")
    host: str | None = None
    port: int | None = None
    token_source: str | None = Field(default=None, alias="token_source")

    @model_validator(mode="after")
    def _one_of_uri_or_hostport(self) -> MeshParams:
        has_uri = bool(self.runner_uri and self.runner_uri.strip())
        has_hostport = bool(self.host and self.host.strip()) and self.port is not None
        if has_uri and (self.host or self.port is not None):
            raise ValueError("connection.mesh: provide runner_uri OR host+port, not both")
        if not has_uri and not has_hostport:
            raise ValueError("connection.mesh requires runner_uri, or both host and port")
        return self

    @property
    def resolved_uri(self) -> str:
        """The canonical ``runner://host:port`` form."""
        if self.runner_uri:
            return self.runner_uri
        return f"runner://{self.host}:{self.port}"

    @property
    def http_base(self) -> str:
        """The HTTP base URL the launcher POSTs /spawn to (``runner://`` → ``http://``)."""
        uri = self.resolved_uri
        return "http://" + uri.removeprefix("runner://")


class Connection(BaseModel):
    """connection: block of launch-settings.yaml."""

    model_config = ConfigDict(extra="forbid")

    mode: ConnectionMode = "local"
    ssh: SSHParams | None = None
    mesh: MeshParams | None = None

    @model_validator(mode="after")
    def _ssh_required_when_mode_is_ssh(self) -> Connection:
        if self.mode == "ssh" and self.ssh is None:
            raise ValueError("connection.mode='ssh' requires connection.ssh.{host,user} to be set")
        return self

    @model_validator(mode="after")
    def _mesh_required_when_mode_is_mesh(self) -> Connection:
        if self.mode == "mesh" and self.mesh is None:
            raise ValueError(
                "connection.mode='mesh' requires connection.mesh (runner_uri or host+port)"
            )
        return self


class AgentEntry(BaseModel):
    """One agent to launch — name + enabled flag."""

    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool = True


class TmuxLayoutConfig(BaseModel):
    """tmux: block — session naming + window layout."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    session_prefix: str
    layout: TmuxLayout = "tiled"


class LaunchSettings(BaseModel):
    """Top-level schema for launch-settings.yaml."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    connection: Connection = Field(default_factory=Connection)
    agents: list[AgentEntry] = Field(default_factory=list)
    tmux: TmuxLayoutConfig

    @model_validator(mode="after")
    def _version_is_one(self) -> LaunchSettings:
        if self.version != 1:
            raise ValueError(f"launch-settings.yaml version must be 1; got {self.version}")
        return self

    @model_validator(mode="after")
    def _spec_agent_mandatory_and_enabled(self) -> LaunchSettings:
        for a in self.agents:
            if a.name == "spec-agent":
                if not a.enabled:
                    raise ValueError(
                        "agents[spec-agent].enabled must be true — spec-agent is mandatory"
                    )
                return self
        raise ValueError(
            "agents[] must contain a 'spec-agent' entry — spec-agent is mandatory for every project"
        )


# ---------------------------------------------------------------------------
# load_settings — scalar key-path override merge (task 1.5)


def _scalar_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive scalar-merge: overlay scalar/leaf keys onto base.

    Lists are REPLACED wholesale (not extended). Dicts merge recursively.
    Mutates a copy; returns the result. Both inputs are left intact.
    """
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _scalar_merge(out[k], v)
        else:
            # Scalar OR list OR new key → wholesale replace
            out[k] = v
    return out


def load_settings(launcher_dir: Path) -> LaunchSettings:
    """Read `launch-settings.yaml` + optional `launch-settings.local.yaml`.

    Missing local file is silently treated as "no overrides". Missing base
    file raises FileNotFoundError (caller should check before invoking).
    """
    base_path = launcher_dir / "launch-settings.yaml"
    if not base_path.is_file():
        raise FileNotFoundError(f"launch-settings.yaml not found at {base_path}")
    base = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
    if not isinstance(base, dict):
        raise ValueError(f"launch-settings.yaml must be a mapping; got {type(base).__name__}")

    local_path = launcher_dir / "launch-settings.local.yaml"
    if local_path.is_file():
        local_text = local_path.read_text(encoding="utf-8")
        # Empty file OR all-comments file → no overrides
        local = yaml.safe_load(local_text)
        if isinstance(local, dict) and local:
            base = _scalar_merge(base, local)

    return LaunchSettings.model_validate(base)


__all__ = [
    "ConnectionMode",
    "TmuxLayout",
    "SSHParams",
    "MeshParams",
    "Connection",
    "AgentEntry",
    "TmuxLayoutConfig",
    "LaunchSettings",
    "load_settings",
]
