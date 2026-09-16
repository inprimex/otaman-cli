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


#: Flags that consume the token after them — their VALUE is never a program name.
_VALUED_FLAGS = ("--path", "--search-root")


def positional_program_name(argv: list[str]) -> str:
    """The bare program name in ``otaman -i <program>``, or ``""``.

    Skips flags and the values of value-taking flags, so ``-i --path /x myprog``
    yields ``myprog`` and ``-i --path /x`` yields nothing.
    """
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in _VALUED_FLAGS:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        return tok
    return ""


def select_program(argv: list[str], programs: list):
    """(program, error) — which program to open, skipping the picker (1.1).

    * a NAME given → that program, or an error listing the candidates when it
      matches none or is ambiguous;
    * no name and exactly ONE program discovered → that one (an unambiguous
      choice is not a choice worth asking about);
    * otherwise → (None, None), meaning show the picker.

    Exactly one of the pair is ever non-None. Matching is case-insensitive and
    tolerant of a trailing slash, because the name is usually typed from a
    directory listing.
    """
    wanted = positional_program_name(argv).strip().rstrip("/")
    if not wanted:
        if len(programs) == 1:
            return programs[0], None
        return None, None

    key = wanted.lower()
    matches = [p for p in programs if p.name.lower() == key]
    if len(matches) == 1:
        return matches[0], None

    names = ", ".join(sorted(p.name for p in programs)) or "(none discovered)"
    if not matches:
        return None, (
            f"No program named {wanted!r}.\n  Discovered: {names}\n"
            "  Run `otaman -i` with no name to pick from the list, or pass "
            "`--path <dir>` to search elsewhere."
        )
    return None, (
        f"{wanted!r} is ambiguous — {len(matches)} discovered programs share that name.\n"
        f"  Discovered: {names}\n  Pass `--path <dir>` to disambiguate."
    )


def run_console(argv: list[str], *, _run: bool = True) -> int:
    """Launch the console. `_run=False` builds the app without entering the
    event loop (test seam)."""
    try:
        import textual  # noqa: F401
    except ImportError:
        print(_INSTALL_HINT)
        return 2

    from otaman_cli.console import seat

    # Private-server boundary (task 1.1 / D1): refuse to launch on the fleet
    # (default) tmux server — the never-inject boundary is structural. Launched
    # from a plain SSH shell we seat onto our own private socket below.
    if _run and seat.on_fleet_server():
        print(seat.FLEET_REFUSAL)
        return 2

    # Self-managing surviving seat (task 1.4): wrap this launch in a tmux
    # session on the private human server so it survives disconnects and stays
    # isolated from the fleet default server. Re-exec replaces this process;
    # inside the seat (or without tmux / with --no-seat) we fall through and
    # run the app directly.
    if _run:
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

    # console-ia-consolidation 1.1 — open the named (or only) program directly.
    # An unknown/ambiguous name is a loud non-zero exit rather than a silent
    # fallback to the picker: the operator asked for a specific program, and
    # quietly showing a list instead hides the typo they need to see.
    initial, error = select_program(argv, programs)
    if error:
        print(error)
        return 2

    app = OtamanConsole(programs, search_root=search_root, initial_program=initial)
    if _run:  # pragma: no cover - the blocking TUI loop is not unit-tested
        app.run()
    return 0


__all__ = ["positional_program_name", "run_console", "select_program"]
