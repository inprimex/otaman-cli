"""console-ia-consolidation P0 — the two daily papercuts (1.1, 1.2).

1.1: `otaman -i <program>` opens that program instead of making the operator
pick it from a list of one. An unambiguous single discovered program
auto-advances too; an unknown or ambiguous name exits non-zero naming the
candidates rather than silently falling back to the picker.

1.2: every screen yields `#mode-banner` (the plain-words header, D9) and it had
NO css rule at all, so header text and screen content ran together.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.launch import positional_program_name, select_program

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


def _program(tmp_path, name: str):
    root = tmp_path / name
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    root.joinpath("platform.yaml").write_text(
        f"project: {name}\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name=name, root=root)


# ---------------------------------------------------------------------------
# 1.1 — argv parsing


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["myprog"], "myprog"),
        ([], ""),
        (["--path", "/x"], ""),  # a flag VALUE is never a program name
        (["--path", "/x", "myprog"], "myprog"),
        (["myprog", "--path", "/x"], "myprog"),
        (["--search-root", "/y", "myprog"], "myprog"),
        (["--no-seat"], ""),  # bare flags skipped
        (["--no-seat", "myprog"], "myprog"),
        (["a", "b"], "a"),  # first positional wins
    ],
)
def test_positional_program_name(argv, expected):
    assert positional_program_name(argv) == expected


# ---------------------------------------------------------------------------
# 1.1 — selection


def test_named_program_is_selected(tmp_path):
    progs = [_program(tmp_path, "alpha"), _program(tmp_path, "beta")]
    chosen, error = select_program(["beta"], progs)
    assert error is None and chosen is not None and chosen.name == "beta"


def test_name_match_is_case_insensitive_and_slash_tolerant(tmp_path):
    progs = [_program(tmp_path, "alpha")]
    for token in ("ALPHA", "Alpha", "alpha/"):
        chosen, error = select_program([token], progs)
        assert error is None and chosen.name == "alpha", token


def test_single_discovered_program_auto_advances(tmp_path):
    """An unambiguous choice is not a choice worth asking about."""
    progs = [_program(tmp_path, "only")]
    chosen, error = select_program([], progs)
    assert error is None and chosen is not None and chosen.name == "only"


def test_several_programs_and_no_name_shows_the_picker(tmp_path):
    progs = [_program(tmp_path, "alpha"), _program(tmp_path, "beta")]
    chosen, error = select_program([], progs)
    assert chosen is None and error is None  # neither — i.e. show the picker


def test_unknown_name_errors_and_lists_candidates(tmp_path):
    progs = [_program(tmp_path, "alpha"), _program(tmp_path, "beta")]
    chosen, error = select_program(["gamma"], progs)
    assert chosen is None
    assert error and "gamma" in error
    assert "alpha" in error and "beta" in error  # the candidates
    assert "--path" in error  # and how to look elsewhere


def test_ambiguous_name_errors_rather_than_guessing(tmp_path):
    a = _program(tmp_path / "one", "dup")
    b = _program(tmp_path / "two", "dup")
    chosen, error = select_program(["dup"], [a, b])
    assert chosen is None
    assert error and "ambiguous" in error.lower()


def test_no_programs_discovered_with_a_name(tmp_path):
    chosen, error = select_program(["anything"], [])
    assert chosen is None
    assert error and "none discovered" in error.lower()


def test_no_programs_and_no_name_shows_the_picker(tmp_path):
    """The picker renders its own empty-state; selection must not pre-empt it."""
    chosen, error = select_program([], [])
    assert chosen is None and error is None


def test_path_flag_alone_does_not_select(tmp_path):
    """`--path`/`--search-root` behaviour unchanged: they steer DISCOVERY, and
    must not be read as a program name."""
    progs = [_program(tmp_path, "alpha"), _program(tmp_path, "beta")]
    chosen, error = select_program(["--path", "/somewhere"], progs)
    assert chosen is None and error is None


# ---------------------------------------------------------------------------
# 1.1 — run_console wiring


@_textual
def test_run_console_exits_nonzero_on_an_unknown_name(tmp_path, monkeypatch, capsys):
    from otaman_cli.console import launch

    monkeypatch.setattr(launch, "_resolve_search_root", lambda argv: tmp_path)
    monkeypatch.setattr(
        "otaman_cli.console.bus.discover_programs",
        lambda root, cwd=None: [_program(tmp_path, "alpha")],
    )
    rc = launch.run_console(["nope"], _run=False)
    out = capsys.readouterr().out
    assert rc == 2  # loud, not a silent fallback to the picker
    assert "nope" in out and "alpha" in out


@_textual
def test_run_console_builds_with_the_selected_program(tmp_path, monkeypatch):
    from otaman_cli.console import launch

    monkeypatch.setattr(launch, "_resolve_search_root", lambda argv: tmp_path)
    monkeypatch.setattr(
        "otaman_cli.console.bus.discover_programs",
        lambda root, cwd=None: [_program(tmp_path, "alpha"), _program(tmp_path, "beta")],
    )
    assert launch.run_console(["beta"], _run=False) == 0


# ---------------------------------------------------------------------------
# 1.1 — the app opens on Home, with the picker still behind it


@_textual
def test_initial_program_opens_home_not_the_picker(tmp_path):
    from otaman_cli.console.app import HomeScreen, OtamanConsole, ProgramPickerScreen

    prog = _program(tmp_path, "alpha")

    async def go():
        app = OtamanConsole([prog], search_root=tmp_path, initial_program=prog)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            # the picker remains the base screen so `escape` has a target and
            # popping the only screen can never raise
            assert any(isinstance(s, ProgramPickerScreen) for s in app.screen_stack)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_without_an_initial_program_the_picker_is_shown(tmp_path):
    from otaman_cli.console.app import OtamanConsole, ProgramPickerScreen

    progs = [_program(tmp_path, "alpha"), _program(tmp_path, "beta")]

    async def go():
        app = OtamanConsole(progs, search_root=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ProgramPickerScreen)
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# 1.2 — the separator


@_textual
def test_mode_banner_has_a_border_rule():
    """It had NO css rule at all, so header and content ran together."""
    from otaman_cli.console.app import OtamanConsole

    css = OtamanConsole.CSS
    assert "#mode-banner" in css
    assert "border-bottom" in css


@_textual
def test_separator_is_applied_on_a_real_screen(tmp_path):
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    prog = _program(tmp_path, "alpha")

    async def go():
        app = OtamanConsole([prog], search_root=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(prog))
            await pilot.pause()
            banner = app.screen.query_one("#mode-banner")
            # Textual resolves the edge type from the stylesheet; "none"/empty
            # would mean the rule never applied to the widget.
            edge = banner.styles.border_bottom
            assert edge and edge[0] not in ("", "none")
            await app.action_quit()

    asyncio.run(go())
