"""Bus-write holder-ship guard — single-acting-session-guard 2.1 + 2.2.

Bus-*writing* commands (`send`, `ack`, `complete`, `set-status`) must run from
the acting session for their identity, so a passive mirror — a second live
session, or a stale background session the human has walked away from — cannot
double-act on the bus (the 2026-08-16 incident shape). Reads are never guarded.

The lock primitive is ``otaman_core.acting_lock`` (core 0.1); the holder is
whichever process took the acting lock, normally via ``otaman acting-lock run``
(the launcher's wrapper). A guarded command run underneath that wrapper is a
descendant of the holder pid — which is how we recognise "we are the acting
session" without any handshake.

Decision — **fail-open**, never block a write we cannot adjudicate:

* identity/target unresolvable, or the lock is unavailable (non-POSIX) → ALLOW
* lock free / acquirable (``probe`` → ``None``)                        → ALLOW
* holder pid is in THIS process's ancestry (we are the acting session,
  or a descendant of ``otaman acting-lock run``):
    - a preempt marker is pending — we are the demoted holder  → REFUSE "preempted" (2.2)
    - otherwise                                                → ALLOW
* holder pid is a live *other* session (a passive mirror)      → REFUSE, name holder (2.1)

The preempt-refusal path is deliberately also how a holder that has *no*
launcher poll loop observes the marker: it finds out it was preempted the next
time it tries to write to the bus.
"""

from __future__ import annotations

import os

from otaman_cli.main import UI

# Exit code for a guard refusal — distinct from argparse's 2 so a caller (or a
# test) can tell "refused because another session is acting" from a usage error.
REFUSED = 3


def resolve_target() -> str | None:
    """``otaman://<org>/<program>/<agent>`` for the acting identity, quietly.

    Returns ``None`` (rather than raising or printing) whenever the acting
    identity can't be pinned down — that is the guard's fail-open signal.
    """
    try:
        from otaman_cli.bus_target import derive_local_context
        from otaman_cli.identity import find_project_root, resolve_agent_identity

        root = find_project_root()
        if root is None:
            return None
        ctx = derive_local_context(root)
        if ctx is None:
            return None
        agent = resolve_agent_identity(root)
        if not agent:
            return None
        return f"otaman://{ctx.org}/{ctx.program}/{agent}"
    except Exception:  # noqa: BLE001 - resolution is best-effort; any failure fails open
        return None


def _ppid(pid: int) -> int | None:
    """The parent pid of ``pid`` — Linux ``/proc`` fast path, ``ps`` fallback."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            data = fh.read()
        # `pid (comm) state ppid ...` — comm may hold spaces/parens, so split
        # after the final ')': [state, ppid, ...].
        after = data[data.rfind(")") + 2 :].split()
        return int(after[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        import subprocess

        r = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        )
        out = r.stdout.strip()
        return int(out) if out else None
    except Exception:  # noqa: BLE001 - ancestry is advisory; unknown parent ends the walk
        return None


def _pid_in_ancestry(target_pid: int) -> bool:
    """True if ``target_pid`` is this process or one of its ancestors."""
    pid = os.getpid()
    for _ in range(64):  # cap the walk; process trees are never this deep
        if pid == target_pid:
            return True
        if pid <= 1:
            return False
        nxt = _ppid(pid)
        if nxt is None or nxt == pid:
            return False
        pid = nxt
    return False


def _passive_refusal(verb: str, holder: dict) -> str:
    pid = holder.get("pid")
    sess = holder.get("tmux_session")
    join = (
        f" — join it with `tmux attach-session -t {sess}`"
        if sess
        else " (no tmux session was recorded for it)"
    )
    return (
        f"Refused `otaman {verb}` — this is a passive mirror, not the acting session. "
        f"The acting session for this identity is held by pid {pid}{join}. "
        f"Run bus-writing commands from the acting session, not this one."
    )


def enforce(verb: str) -> int | None:
    """Guard a bus-*write* verb. Returns ``None`` to allow, else an exit code.

    Call at the top of a write command:
    ``rc = acting_guard.enforce("send"); if rc is not None: return rc``.
    """
    target = resolve_target()
    if target is None:
        return None  # can't adjudicate → allow
    try:
        from otaman_core.acting_lock import ActingLockError, probe, read_preempt_marker
    except Exception:  # noqa: BLE001 - primitive absent → cannot enforce
        return None
    try:
        holder = probe(target)
    except ActingLockError:
        return None  # non-POSIX: no flock, cannot enforce
    if holder is None:
        return None  # lock free / acquirable
    hpid = holder.get("pid")
    if isinstance(hpid, int) and _pid_in_ancestry(hpid):
        # We are the acting session (or a child of `acting-lock run`).
        try:
            marker = read_preempt_marker(target)
        except Exception:  # noqa: BLE001 - marker read is best-effort
            marker = None
        if marker:
            UI.error(
                f"Refused `otaman {verb}` — this acting session has been preempted "
                f"(handoff requested by pid {marker.get('pid')}). Stop acting and let the "
                f"interactive session take over; this session should exit."
            )
            return REFUSED
        return None
    # Held by a live *other* session: a passive mirror.
    UI.error(_passive_refusal(verb, holder))
    return REFUSED
