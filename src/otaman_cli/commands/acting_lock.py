"""`otaman acting-lock run|probe` — the cli face of the acting-session lock.

single-acting-session-guard 2.1 (folded per spec-agent's ruling 20260829T222512):
the ONE flock/D1/D3 implementation lives in ``otaman_core.acting_lock``; this
command is the bash-friendly wrapper the launcher calls so no flock logic is
reimplemented in bash.

- ``run --mode interactive|background [--preempt] -- <cmd…>``: acquire the
  acting lock for THIS process, run <cmd> as a child while holding it, release
  on exit. The child (and anything it spawns) is a descendant of the
  lock-holder pid — which is exactly what the bus-write guard (2.2) checks. We
  hold the fd in the parent rather than ``exec``-ing (core's fd is CLOEXEC, so
  it would not survive exec); observable behavior for the launcher is identical.
- ``probe [--json]``: report the live holder (flock truth) for the launcher's
  attach-first decision. Exit 0 = held, 1 = free.

Interactive preemption (D3): ``--preempt`` (interactive only) writes a preempt
marker and waits the 10s handoff window for the background holder to demote and
release — cooperative, never a lock steal (flock can't be force-broken).
"""

from __future__ import annotations

import os

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root, resolve_agent_identity
from otaman_cli.main import UI

_MODES = ("interactive", "background")
_PREEMPT_WINDOW_S = 10.0


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _target_uri() -> str | None:
    """``otaman://<org>/<program>/<agent>`` for the acting identity, or None."""
    from otaman_cli.bus_target import derive_local_context

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return None
    ctx = derive_local_context(root)
    if ctx is None:
        UI.error(
            "The acting lock requires the declared org layout (orgs/<org>/programs/<program>/…)."
        )
        return None
    agent = resolve_agent_identity(root)
    if not agent:
        UI.error(
            "Could not resolve the acting agent identity (set OTAMAN_AGENT or --from-managed repo)."
        )
        return None
    return f"otaman://{ctx.org}/{ctx.program}/{agent}"


def _tmux_session() -> str | None:
    """The current tmux session name, if any (recorded in the lock .info)."""
    import subprocess

    if not os.environ.get("TMUX"):
        return None
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#S"], capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 - best-effort metadata
        return None


def _holder_hint(holder: dict | None) -> str:
    """A refusal tail naming the holder + how to join its session."""
    if not holder:
        return "another acting session holds the lock."
    pid = holder.get("pid")
    sess = holder.get("tmux_session")
    join = f" — join it with `tmux attach-session -t {sess}`" if sess else ""
    return f"the acting session for this identity is held by pid {pid}{join}"


def _cmd_probe(as_json: bool) -> int:
    target = _target_uri()
    if target is None:
        return 1
    from otaman_core.acting_lock import ActingLockError, probe

    try:
        holder = probe(target)
    except ActingLockError as exc:
        return _bail(f"acting lock unavailable on this platform: {exc}", code=2)
    if as_json:
        import json

        print(json.dumps({"held": holder is not None, "holder": holder}))
    elif holder is None:
        UI.muted("acting lock: free")
    else:
        UI.ok("acting lock: held")
        for k in ("pid", "mode", "tmux_session", "started_at"):
            if holder.get(k) is not None:
                UI.kv(k, str(holder[k]))
    return 0 if holder is not None else 1


def _cmd_run(mode: str, preempt: bool, cmd: list[str]) -> int:
    if mode not in _MODES:
        return _bail(f"--mode must be one of {', '.join(_MODES)}")
    if not cmd:
        return _bail("Usage: otaman acting-lock run --mode <mode> [--preempt] -- <command>")
    target = _target_uri()
    if target is None:
        return 1

    import subprocess
    import time

    from otaman_core.acting_lock import (
        ActingLockError,
        ActingLockHeld,
        acquire,
        clear_preempt_marker,
        probe,
        write_preempt_marker,
    )

    tmux = _tmux_session()

    def _try_acquire():
        return acquire(target, mode=mode, tmux_session=tmux, pid=os.getpid())

    try:
        lock = _try_acquire()
    except ActingLockHeld as held:
        holder = held.holder or probe(target)
        if not (preempt and mode == "interactive"):
            return _bail(f"Refused — {_holder_hint(holder)}.", code=2)
        # D3 cooperative preemption: mark, then wait for the holder to demote.
        write_preempt_marker(target, pid=os.getpid(), mode=mode)
        lock = None
        deadline = time.monotonic() + _PREEMPT_WINDOW_S
        while time.monotonic() < deadline:
            time.sleep(0.3)
            try:
                lock = _try_acquire()
                break
            except ActingLockHeld:
                continue
        if lock is None:
            clear_preempt_marker(target)
            pid = (holder or {}).get("pid")
            return _bail(
                f"holder pid {pid} did not release within {int(_PREEMPT_WINDOW_S)}s — it is alive "
                f"and wedged. Kill it to take the acting role: kill {pid}",
                code=2,
            )
        clear_preempt_marker(target)
    except ActingLockError as exc:
        return _bail(f"acting lock unavailable on this platform: {exc}", code=2)

    # Acquired — run the command as a child while THIS process holds the fd.
    with lock:
        try:
            return subprocess.run(cmd).returncode
        except FileNotFoundError:
            return _bail(f"command not found: {cmd[0]}", code=127)


def cmd_acting_lock(args: list[str]) -> int:
    """`otaman acting-lock <run|probe> …`."""
    if not args or args[0] in ("-h", "--help"):
        UI.muted("Usage: otaman acting-lock probe [--json]")
        UI.muted(
            "       otaman acting-lock run --mode interactive|background [--preempt] -- <command>"
        )
        return 0 if args else 1
    action, rest = args[0], args[1:]
    if action == "probe":
        return _cmd_probe(as_json="--json" in rest)
    if action == "run":
        mode = "interactive"
        preempt = False
        cmd: list[str] = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--mode" and i + 1 < len(rest):
                mode = rest[i + 1]
                i += 2
            elif a == "--preempt":
                preempt = True
                i += 1
            elif a == "--":
                cmd = rest[i + 1 :]
                break
            else:
                return _bail(f"Unexpected argument: {a} (did you forget `--` before the command?)")
        return _cmd_run(mode, preempt, cmd)
    return _bail(f"Unknown action {action!r}. Actions: run, probe")


register(
    CommandSpec(
        name="acting-lock",
        handler=cmd_acting_lock,
        help="Acting-session lock: run <cmd> holding it | probe the holder",
    )
)

__all__ = ["cmd_acting_lock"]
