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
import subprocess
import sys
from collections.abc import Callable

# Set inside the seat so the re-executed `otaman -i` runs the app instead of
# re-wrapping itself (recursion guard).
SEAT_ENV = "OTAMAN_CONSOLE_SEAT"
# `tmux -L <name>` → a private server with its own socket, separate from the
# fleet default server.
PRIVATE_SOCKET = "otaman-human"
SESSION_NAME = "otaman-console"
# Session-scoped tmux env var holding the otaman-cli version the running seat
# was started from — the seated process stamps it (stamp_seat_version); the
# next outer launch reads it to detect a stale surviving seat (5.1 finding #3
# follow-up UX). Session env dies with the session, so it's self-cleaning.
CONSOLE_VERSION_ENV = "OTAMAN_CONSOLE_VERSION"

# subprocess runner signature, injectable for tests.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def in_seat() -> bool:
    """True when already running inside the managed seat (skip re-wrapping)."""
    return os.environ.get(SEAT_ENV) == "1"


#: Shown when `otaman -i` is launched from inside a NON-private tmux server.
FLEET_REFUSAL = (
    f"Refusing to launch: `otaman -i` must run on your PRIVATE tmux server "
    f"(socket '{PRIVATE_SOCKET}'), not the fleet/default server. Start it from a plain SSH "
    f"shell — outside any fleet tmux session — and it creates its own private seat. "
    f"(interactive-human-console D1: the never-inject boundary is structural — there is no "
    f"fleet-reachable session to inject into.)"
)


def current_tmux_socket() -> str | None:
    """The socket path of the tmux server we're inside (from $TMUX), or None when
    not inside tmux. `$TMUX` is `<socket_path>,<pid>,<session_id>`."""
    tmux = os.environ.get("TMUX")
    if not tmux:
        return None
    return tmux.split(",", 1)[0] or None


def on_fleet_server() -> bool:
    """True when running inside a tmux server that is NOT our private socket — i.e.
    the fleet/default server. The console must refuse this (D1). Not inside tmux
    at all is fine (we'll seat onto the private socket ourselves)."""
    sock = current_tmux_socket()
    if sock is None:
        return False
    from pathlib import Path

    return Path(sock).name != PRIVATE_SOCKET


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def installed_version() -> str:
    """The installed otaman-cli version, or "" if it can't be determined."""
    try:
        import importlib.metadata

        return importlib.metadata.version("otaman-cli")
    except Exception:  # noqa: BLE001 - version is best-effort; never break launch
        return ""


def _tmux(
    args: list[str], *, socket: str = PRIVATE_SOCKET, run: Runner = subprocess.run
) -> subprocess.CompletedProcess[str] | None:
    """Run a `tmux -L <socket> …` command, returning None on any failure.

    Every seat introspection goes through here so a missing/old tmux or a dead
    server degrades to "unknown" rather than raising into the launch path.
    """
    try:
        return run(["tmux", "-L", socket, *args], capture_output=True, text=True)
    except Exception:  # noqa: BLE001 - tmux missing / spawn error → unknown
        return None


def session_exists(
    *, socket: str = PRIVATE_SOCKET, session: str = SESSION_NAME, run: Runner = subprocess.run
) -> bool:
    r = _tmux(["has-session", "-t", session], socket=socket, run=run)
    return r is not None and r.returncode == 0


def running_seat_version(
    *, socket: str = PRIVATE_SOCKET, session: str = SESSION_NAME, run: Runner = subprocess.run
) -> str | None:
    """The version stamped in the running seat's session env, or None."""
    r = _tmux(["show-environment", "-t", session, CONSOLE_VERSION_ENV], socket=socket, run=run)
    if r is None or r.returncode != 0:
        return None
    # tmux prints `NAME=value`, or `-NAME` when the var is unset.
    line = (r.stdout or "").strip()
    prefix = f"{CONSOLE_VERSION_ENV}="
    return line[len(prefix) :] if line.startswith(prefix) else None


def stamp_seat_version(
    version: str | None = None,
    *,
    socket: str = PRIVATE_SOCKET,
    session: str = SESSION_NAME,
    run: Runner = subprocess.run,
) -> None:
    """Record the running version into the session env (called from the seat)."""
    v = version if version is not None else installed_version()
    if not v:
        return
    _tmux(["set-environment", "-t", session, CONSOLE_VERSION_ENV, v], socket=socket, run=run)


def kill_seat(
    *, socket: str = PRIVATE_SOCKET, session: str = SESSION_NAME, run: Runner = subprocess.run
) -> None:
    _tmux(["kill-session", "-t", session], socket=socket, run=run)


def offer_restart_if_stale(
    *,
    socket: str = PRIVATE_SOCKET,
    session: str = SESSION_NAME,
    run: Runner = subprocess.run,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    installed: str | None = None,
) -> bool:
    """If a SURVIVING seat runs a different version than installed, offer to
    restart it before we attach (Roman hit stale code by re-attaching and had
    to know the kill-session incantation — spec-agent 20260827T073813).

    Returns True iff the stale seat was killed (caller then creates a fresh
    one). Never raises; on any uncertainty (no seat, unknown version, declined)
    it does nothing and the normal create-or-attach proceeds.
    """
    if not session_exists(socket=socket, session=session, run=run):
        return False
    running = running_seat_version(socket=socket, session=session, run=run)
    inst = installed if installed is not None else installed_version()
    if not running or not inst or running == inst:
        return False  # unknown or up to date → attach as usual
    out(f"A console seat is already running an older version ({running}); installed is {inst}.")
    try:
        answer = input_fn("Restart the seat to pick up the update? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer in ("y", "yes"):
        kill_seat(socket=socket, session=session, run=run)
        out("Restarting the seat on the updated version…")
        return True
    out("Keeping the running seat (attaching as-is).")
    return False


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
    "CONSOLE_VERSION_ENV",
    "FLEET_REFUSAL",
    "PRIVATE_SOCKET",
    "SEAT_ENV",
    "SESSION_NAME",
    "build_attach_command",
    "current_tmux_socket",
    "in_seat",
    "on_fleet_server",
    "inner_command",
    "installed_version",
    "kill_seat",
    "offer_restart_if_stale",
    "reexec_into_seat",
    "running_seat_version",
    "session_exists",
    "should_seat",
    "stamp_seat_version",
    "tmux_available",
]
