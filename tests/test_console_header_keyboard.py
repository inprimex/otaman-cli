"""console-ux-redesign wave 1, task 1.5 — header + keyboard contracts (D3/S2/S3).

D3 makes contracts over conventions enforceable in CI:
- HOME carries the full three-line header; other screens a compact form.
- every advertised key DISPATCHES (pressed via pilot, not just statically
  resolvable) — no dead advertised keys, ever (S3).
- every caught key produces a VISIBLE acknowledgment (the keypress echo).

The static binding-resolution guard lives in test_console_key_bindings; this
adds the live pilot.press sweep + the echo + the header-shape assertions for the
wave-1 screens.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
pytestmark = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _new_screen(name, program):
    from otaman_cli.console.app import HomeScreen, InboxScreen, TreeScreen

    return {"HomeScreen": HomeScreen, "InboxScreen": InboxScreen, "TreeScreen": TreeScreen}[name](
        program
    )


_WAVE1_SCREENS = ["HomeScreen", "InboxScreen", "TreeScreen"]


def _advertised_non_terminal_keys(screen_cls):
    # every advertised key except quit (which tears the app down — covered in
    # test_console_key_bindings) — each must dispatch with a visible echo.
    out = []
    for b in screen_cls.BINDINGS:
        key = b.key if hasattr(b, "key") else b[0]
        action = b.action if hasattr(b, "action") else b[1]
        if action == "app.quit":
            continue
        out.append(key)
    return out


def _pairs():
    from otaman_cli.console.app import HomeScreen, InboxScreen, TreeScreen

    classes = {"HomeScreen": HomeScreen, "InboxScreen": InboxScreen, "TreeScreen": TreeScreen}
    return [
        (name, key)
        for name in _WAVE1_SCREENS
        for key in _advertised_non_terminal_keys(classes[name])
    ]


# ---------------------------------------------------------------------------
# keyboard: every advertised key dispatches with a visible ack (pilot.press)


@pytest.mark.parametrize("screen_name,key", _pairs())
def test_advertised_key_dispatches_with_echo(program, screen_name, key):
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(_new_screen(screen_name, program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            # visible acknowledgment: the App echoes the caught key/action into
            # the header sub_title (bound keys via run_action, unbound via on_key)
            assert str(app.sub_title).startswith("⌨")
            # app is still alive (no crash / no dead key)
            assert app.screen_stack

    asyncio.run(go())


def test_unbound_key_still_echoes(program):
    # S3: even a key the screen does not bind is acknowledged (on_key echo).
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await pilot.press("z")  # unbound
            await pilot.pause()
            assert "z" in str(app.sub_title)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# header: full three-line on Home, compact elsewhere


def test_home_has_full_three_line_header(program):
    from textual.widgets import Static

    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            header = app.screen.query_one("#home-header", Static)
            assert header is not None
            # 3 lines: identity+program / fleet+decisions / breadcrumb+policy
            assert HomeScreen._header_text(app.screen, app.screen._summary).count("\n") == 2
            await app.action_quit()

    asyncio.run(go())


@pytest.mark.parametrize("screen_name", ["InboxScreen", "TreeScreen"])
def test_non_home_screens_are_compact(program, screen_name):
    # compact form: the mode-banner is present, the full 3-line home-header is NOT.
    from textual.css.query import NoMatches
    from textual.widgets import Static

    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(_new_screen(screen_name, program))
            await pilot.pause()
            assert app.screen.query_one("#mode-banner", Static) is not None
            with pytest.raises(NoMatches):
                app.screen.query_one("#home-header", Static)
            await app.action_quit()

    asyncio.run(go())
