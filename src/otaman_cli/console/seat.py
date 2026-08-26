"""Self-managing surviving console seat on a PRIVATE tmux server (task 1.4).

`otaman -i` create-or-attaches a tmux session on a SEPARATE tmux server
(private socket via `tmux -L`), distinct from the fleet's default server. Two
properties fall out:

- **Survival** — the session outlives an SSH disconnect; reconnecting and
  re-running `otaman -i` re-attaches the same session (`new-session -A`), state
  intact. Zero runner dependency (CE-first): the console does this itself.
- **Isolation (Q7)** — a private socket keeps the human seat off the default
  server that fleet agents share, so an agent's `tmux send-keys` against the
  default server cannot reach it. On a shared tenant user this is a
  trust+policy boundary, not a physical/cryptographic one (the mandatory
  no-send-keys fleet policy — plugin 3.1 — is the compensating control).

Watchdog note: a human seat being idle is normal; session-lifecycle
escalation must NOT apply to it (enforced by the human participant type, not
here). This module only manages the create-or-attach wrapper.
"""

from __future__ import annotations

import os
import shutil
import sys

# Set inside the seat so the re-executed `otaman -i` runs the app instead of
# re-wrapping itself (recursion guard).
SEAT_ENV = "OTAMAN_CONSOLE_SEAT"
# `tmux -L <name>` → a private server with its own socket, separate from the
# fleet default server.
PRIVATE_SOCKET = "otaman-human"
SESSION_NAME = "otaman-console"


def in_seat() -> bool:
    """True when already running inside the managed seat (skip re-wrapping)."""
    return os.environ.get(SEAT_ENV) == "1"


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def inner_command(argv: list[str], *, otaman: str | None = None) -> list[str]:
    """The command tmux runs INSIDE the session: re-invoke `otaman -i` with the
    seat marker set (via `env`, so it works on any tmux version)."""
    entry = otaman or sys.argv[0] or "otaman"
    return ["env", f"{SEAT_ENV}=1", entry, "-i", *argv]


def build_attach_command(
    argv: list[str],
    *,
    socket: str = PRIVATE_SOCKET,
    session: str = SESSION_NAME,
    otaman: str | None = None,
) -> list[str]:
    """`tmux -L <socket> new-session -A -s <session> -- <inner>` (create-or-attach)."""
    return [
        "tmux",
        "-L",
        socket,
        "new-session",
        "-A",
        "-s",
        session,
        "--",
        *inner_command(argv, otaman=otaman),
    ]


def should_seat(argv: list[str]) -> bool:
    """Whether to wrap this launch in the surviving seat.

    Skip when already seated, when `--no-seat` is passed, or when tmux is
    unavailable (CE-first: the console still runs directly, just without the
    survival wrapper).
    """
    return not in_seat() and "--no-seat" not in argv and tmux_available()


def reexec_into_seat(argv: list[str], *, exec_fn=os.execvp) -> None:
    """Replace this process with the tmux create-or-attach seat. Does not
    return on success (`exec`); `exec_fn` is injectable for tests."""
    cmd = build_attach_command(argv)
    exec_fn(cmd[0], cmd)


__all__ = [
    "PRIVATE_SOCKET",
    "SEAT_ENV",
    "SESSION_NAME",
    "build_attach_command",
    "in_seat",
    "inner_command",
    "reexec_into_seat",
    "should_seat",
    "tmux_available",
]
