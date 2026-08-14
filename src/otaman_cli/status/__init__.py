"""Agent status presence — singleton status file per agent.

Public surface:
    AgentStatus           — dataclass for the per-agent status record
    State                 — enum: working | blocked | waiting | idle
    StatusBackend         — Protocol; implementations write/read status records
    FileStatusBackend     — CE default; YAML files under .agents/status/
    NatsKvStatusBackend   — EE; NATS KV bucket `otaman.status` (stub in v1)
    get_backend(root)     — factory; selects backend from platform.yaml bus.transport
    is_agent_presence_enabled(root) — reads platform.yaml agent_presence (default True)
"""

from otaman_cli.status.backend import (
    FileStatusBackend,
    NatsKvStatusBackend,
    StatusBackend,
    get_backend,
    is_agent_presence_enabled,
)
from otaman_cli.status.models import AgentStatus, State

__all__ = [
    "AgentStatus",
    "State",
    "StatusBackend",
    "FileStatusBackend",
    "NatsKvStatusBackend",
    "get_backend",
    "is_agent_presence_enabled",
]
