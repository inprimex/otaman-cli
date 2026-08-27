"""`otaman -i` entry point — resolve context, check the extra, run the app.

Keeps Textual OPTIONAL: if the `console` extra is not installed we print a
one-line install hint and exit non-zero rather than crashing on import.
"""

from __future__ import annotations

from pathlib import Path

_INSTALL_HINT = (
    "The interactive console needs the 'console' extra (Textual):\n"
    "    pip install 'otaman-cli[console]'\n"
    "(or `uv sync --extra console`)."
)


def _resolve_search_root(argv: list[str]) -> Path:
    """Where to discover programs: explicit --path, else the project root's
    parent (to surface sibling programs), else cwd."""
    for i, tok in enumerate(argv):
        if tok in ("--path", "--search-root") and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser()
    from otaman_cli.identity import find_project_root

    root = find_project_root()
    return root.parent if root else Path.cwd()


def run_console(argv: list[str], *, _run: bool = True) -> int:
    """Launch the console. `_run=False` builds the app without entering the
    event loop (test seam)."""
    try:
        import textual  # noqa: F401
    except ImportError:
        print(_INSTALL_HINT)
        return 2

    # Self-managing surviving seat (task 1.4): wrap this launch in a tmux
    # session on the private human server so it survives disconnects and stays
    # isolated from the fleet default server. Re-exec replaces this process;
    # inside the seat (or without tmux / with --no-seat) we fall through and
    # run the app directly.
    if _run:
        from otaman_cli.console import seat

        if seat.should_seat(argv):
            # If a surviving seat is running an OLDER version, offer to restart
            # it before we attach — otherwise re-attaching lands on stale code
            # and the operator would need the kill-session incantation
            # (spec-agent 20260827T073813 UX item). Best-effort; never blocks.
            seat.offer_restart_if_stale()
            seat.reexec_into_seat(argv)  # exec — does not return on success
        if seat.in_seat():
            # We ARE the seat's inner process: stamp our version into the
            # session env so the NEXT outer launch can detect staleness.
            seat.stamp_seat_version()

    from otaman_cli.console.app import OtamanConsole
    from otaman_cli.console.bus import discover_programs

    search_root = _resolve_search_root(argv)
    # Pass cwd so discovery can union the marker-chain program when launched
    # from inside a one-off checkout (5.1 finding #3); canonical CE-layout
    # enumeration handles the standard home-dir launch.
    programs = discover_programs(search_root, cwd=Path.cwd())
    app = OtamanConsole(programs, search_root=search_root)
    if _run:  # pragma: no cover - the blocking TUI loop is not unit-tested
        app.run()
    return 0


__all__ = ["run_console"]
