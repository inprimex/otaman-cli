"""The `launch:` block of launch-settings.yaml (unified-launcher-profiles 1.1).

One tenant-local block describing what the launcher offers: whether to seat the
human console, how to seat it, and any named agent profiles. It lives in
launch-settings.yaml rather than platform.yaml because menu composition is
launcher UX and tenant-local, while platform.yaml stays the lean shared contract
(D2) — and the human roster the console needs is already in platform.yaml, so
nothing is duplicated.

    launch:
      include_console: true
      console:
        socket: otaman-human       # the PRIVATE tmux server — see below
        session: otaman-console
        command: otaman -i
      profiles:
        - name: backend
          agents: [core-agent, cli-agent]
          console: false

**An absent block means exactly today's behaviour** (D3): full-only, no menu, no
console step. That is why :func:`parse` returns ``None`` rather than a default
config for a missing block — a caller can then take the legacy path explicitly
instead of inferring it from an all-defaults object.

The socket rule is enforced HERE, not only at seat time. `console.socket` must
be the private server; a tenant that writes the fleet socket into its settings
has configured the one thing the never-inject boundary forbids, and finding that
out at validation is much better than finding it out when a seat lands somewhere
fleet-reachable. The console's own refusal still backstops it (D1) — this is the
earlier of two gates, not a replacement for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Menu positions are fixed by the ruling: full=1, pick=2, profiles from 3 in
#: DECLARATION order. Declaration order rather than sorted: the tenant wrote
#: them in the order they think about them, and re-sorting would renumber a
#: menu operators learn by position.
MENU_FULL = "full"
MENU_PICK = "pick"


@dataclass(frozen=True)
class ConsoleConfig:
    """How the launcher seats the human console."""

    socket: str = ""
    session: str = ""
    command: str = ""


@dataclass(frozen=True)
class Profile:
    """A named subset of agents, optionally seating the console with them."""

    name: str
    agents: tuple[str, ...] = ()
    console: bool = False


@dataclass(frozen=True)
class LaunchConfig:
    """The parsed `launch:` block. Absent block → the caller gets ``None``."""

    include_console: bool = False
    console: ConsoleConfig = field(default_factory=ConsoleConfig)
    profiles: tuple[Profile, ...] = ()

    def menu(self) -> list[tuple[int, str, str]]:
        """``(position, key, label)`` rows — full=1, pick=2, profiles from 3."""
        rows = [(1, MENU_FULL, "full — every agent"), (2, MENU_PICK, "pick — choose agents")]
        for i, prof in enumerate(self.profiles, start=3):
            agents = ", ".join(prof.agents) or "(no agents)"
            label = f"{prof.name} — {agents}" + ("  + console" if prof.console else "")
            rows.append((i, prof.name, label))
        return rows

    def profile(self, name: str) -> Profile | None:
        """Look a profile up by name, or by its menu position as a string."""
        for prof in self.profiles:
            if prof.name == name:
                return prof
        if name.isdigit():
            index = int(name) - 3
            if 0 <= index < len(self.profiles):
                return self.profiles[index]
        return None


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse(settings: dict[str, Any] | None) -> LaunchConfig | None:
    """The `launch:` block of *settings*, or ``None`` when it is absent (D3).

    Tolerant of shape — a malformed block parses to whatever is usable and
    :func:`validate` reports the rest, so one bad profile does not cost the
    tenant their whole menu.
    """
    if not isinstance(settings, dict):
        return None
    block = settings.get("launch")
    if not isinstance(block, dict):
        return None

    raw_console = block.get("console")
    console = ConsoleConfig()
    if isinstance(raw_console, dict):
        console = ConsoleConfig(
            socket=_str(raw_console.get("socket")),
            session=_str(raw_console.get("session")),
            command=_str(raw_console.get("command")),
        )

    profiles: list[Profile] = []
    for raw in block.get("profiles") or []:
        if not isinstance(raw, dict):
            continue
        name = _str(raw.get("name"))
        if not name:
            continue
        agents = raw.get("agents") or []
        if isinstance(agents, str):
            agents = [agents]
        profiles.append(
            Profile(
                name=name,
                agents=tuple(_str(a) for a in agents if _str(a)),
                console=bool(raw.get("console")),
            )
        )

    return LaunchConfig(
        include_console=bool(block.get("include_console")),
        console=console,
        profiles=tuple(profiles),
    )


def validate(config: LaunchConfig | None, *, known_agents: set[str] | None = None) -> list[str]:
    """Problems with *config*, as operator-facing lines. Empty list = fine.

    ``None`` (absent block) validates clean: legacy behaviour is a valid
    configuration, not a missing one.
    """
    if config is None:
        return []
    from otaman_cli.console.seat import FLEET_REFUSAL, PRIVATE_SOCKET

    errors: list[str] = []

    # D1 — the one rule that is not a matter of taste.
    if config.console.socket and config.console.socket != PRIVATE_SOCKET:
        errors.append(
            f"launch.console.socket must be {PRIVATE_SOCKET!r}, not "
            f"{config.console.socket!r} — the console seat may only run on the "
            f"private tmux server.\n{FLEET_REFUSAL}"
        )

    seen: set[str] = set()
    for prof in config.profiles:
        if prof.name in (MENU_FULL, MENU_PICK):
            errors.append(
                f"profile {prof.name!r} shadows the built-in menu entry of the same name — "
                f"positions 1 and 2 are reserved for full and pick."
            )
        if prof.name in seen:
            errors.append(
                f"duplicate profile name {prof.name!r} — menu positions would be ambiguous."
            )
        seen.add(prof.name)
        if prof.name.isdigit():
            errors.append(
                f"profile name {prof.name!r} is numeric — it would collide with menu "
                f"position selection."
            )
        if not prof.agents and not prof.console:
            errors.append(f"profile {prof.name!r} launches nothing — no agents and no console.")
        if known_agents:
            unknown = [a for a in prof.agents if a not in known_agents]
            if unknown:
                errors.append(f"profile {prof.name!r} names unknown agent(s): {', '.join(unknown)}")
    return errors


__all__ = [
    "ConsoleConfig",
    "LaunchConfig",
    "MENU_FULL",
    "MENU_PICK",
    "Profile",
    "parse",
    "validate",
]
