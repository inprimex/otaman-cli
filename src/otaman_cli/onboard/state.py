"""users.yaml + projects.yaml read/write with light schema validation.

The MVP keeps everything as plain dicts loaded via PyYAML. Heavy schema
validation (jsonschema) is a follow-up — for now, we validate required
fields and known role names. PyYAML's safe_load prevents arbitrary code
execution on malicious files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Known role names. Adding a role here is an explicit API change.
KNOWN_ROLES = frozenset({
    "otaman:admin",
    "otaman:approver",
    "otaman:developer",
    "otaman:viewer",
})

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StateError(ValueError):
    """Raised on schema / validation failures in users.yaml or projects.yaml."""


@dataclass
class User:
    email: str
    display_name: str
    roles: list[str]
    unix_user: str | None = None
    unix_groups: list[str] = field(default_factory=list)
    telegram_id: int | None = None
    user_id: str | None = None        # Zitadel sub claim — None in v0 MVP
    created_at: str = ""
    last_seen: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "email": self.email,
            "display_name": self.display_name,
            "roles": list(self.roles),
            "unix_user": self.unix_user,
            "unix_groups": list(self.unix_groups),
            "telegram_id": self.telegram_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "enabled": self.enabled,
        }
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "User":
        if "email" not in d:
            raise StateError("user record missing required field: email")
        if "roles" not in d:
            raise StateError(f"user {d.get('email')!r} missing required field: roles")
        return cls(
            email=d["email"],
            display_name=d.get("display_name") or d["email"].split("@")[0],
            roles=list(d["roles"] or []),
            unix_user=d.get("unix_user"),
            unix_groups=list(d.get("unix_groups") or []),
            telegram_id=d.get("telegram_id"),
            user_id=d.get("user_id"),
            created_at=d.get("created_at") or "",
            last_seen=d.get("last_seen"),
            enabled=bool(d.get("enabled", True)),
        )


def validate_email(email: str) -> None:
    if not EMAIL_RE.match(email or ""):
        raise StateError(f"not a valid email: {email!r}")


def validate_roles(roles: list[str]) -> None:
    if not roles:
        raise StateError("roles list must not be empty")
    for r in roles:
        if r not in KNOWN_ROLES:
            raise StateError(
                f"unknown role: {r!r}. Known roles: {sorted(KNOWN_ROLES)}"
            )


def default_state_dir() -> Path:
    """Where users.yaml + projects.yaml + audit/ live by default.

    Resolution: ``OTAMAN_STATE_DIR`` env var if set, else ``/var/otaman``.
    Tests inject a tmp_path via the env var.
    """
    import os

    env = os.environ.get("OTAMAN_STATE_DIR")
    if env:
        return Path(env)
    return Path("/var/otaman")


# ---- users.yaml --------------------------------------------------------


def users_path(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / "users.yaml"


def load_users(state_dir: Path | None = None) -> list[User]:
    """Load users.yaml. Missing file = empty list."""
    path = users_path(state_dir)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise StateError(f"users.yaml parse error: {exc}") from exc
    raw_users = data.get("users") or []
    if not isinstance(raw_users, list):
        raise StateError(f"users.yaml: 'users' must be a list, got {type(raw_users).__name__}")
    return [User.from_dict(u) for u in raw_users]


def save_users(users: list[User], state_dir: Path | None = None) -> Path:
    """Persist users.yaml. Creates parent dir if needed."""
    path = users_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"users": [u.to_dict() for u in users]}
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return path


def find_user(users: list[User], email: str) -> User | None:
    for u in users:
        if u.email == email:
            return u
    return None


def upsert_user(state_dir: Path | None, new_user: User) -> tuple[User, bool]:
    """Add or update a user. Returns (resulting_user, was_added).

    Idempotency:
    - Email not present → append; was_added=True
    - Email present with identical fields → no change; was_added=False
    - Email present with different fields → StateError (use update-user)
    """
    validate_email(new_user.email)
    validate_roles(new_user.roles)
    users = load_users(state_dir)
    existing = find_user(users, new_user.email)
    if existing is None:
        if not new_user.created_at:
            new_user.created_at = datetime.now(timezone.utc).isoformat()
        users.append(new_user)
        save_users(users, state_dir)
        return new_user, True
    # Idempotent re-add: every field except created_at + last_seen must match.
    if (
        existing.display_name != new_user.display_name
        or sorted(existing.roles) != sorted(new_user.roles)
        or existing.unix_user != new_user.unix_user
        or sorted(existing.unix_groups) != sorted(new_user.unix_groups)
        or existing.telegram_id != new_user.telegram_id
    ):
        raise StateError(
            f"user {new_user.email!r} already exists with different fields; "
            f"use `otaman onboard update-user` to change"
        )
    return existing, False
