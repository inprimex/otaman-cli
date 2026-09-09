"""Console binding conformance — every advertised key must dispatch (IHC canon).

Root cause of the live `q`-quit defect: `Binding("q", "quit", ...)` dispatches
"quit" against the Screen namespace, which has no `action_quit`, so it was
silently dropped (only ctrl+q worked via the App default) — while every Footer
and mode-banner advertised `q`. This class makes advertised-but-dead keys
structurally impossible: for EVERY binding on EVERY screen, the action target
must resolve. Plus pilots that actually press `q` (it now quits) and confirm the
keypress echo.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
pytestmark = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


def _screen_classes():
    from otaman_cli.console.app import (
        ArtifactBrowserScreen,
        ChangeDetailScreen,
        ChangeReviewScreen,
        HomeScreen,
        InboxMessageScreen,
        InboxScreen,
        LifecycleScreen,
        PendingListScreen,
        ProgramPickerScreen,
        ProposalScreen,
        ReasonModal,
        TreeScreen,
    )

    return [
        ProgramPickerScreen,
        HomeScreen,
        InboxScreen,
        InboxMessageScreen,
        TreeScreen,
        PendingListScreen,
        ProposalScreen,
        LifecycleScreen,
        ArtifactBrowserScreen,
        ChangeReviewScreen,
        ChangeDetailScreen,
        ReasonModal,
    ]


def _binding_action(b) -> str:
    # BINDINGS entries may be Binding objects or (key, action, desc) tuples.
    return b.action if hasattr(b, "action") else b[1]


def _resolves(screen_cls, action: str, app_cls) -> bool:
    ns, dot, name = action.partition(".")
    if dot:  # namespaced, e.g. "app.quit"
        target = app_cls if ns == "app" else None
        return target is not None and hasattr(target, f"action_{name}")
    return hasattr(screen_cls, f"action_{action}")


def test_every_binding_action_resolves():
    from otaman_cli.console.app import OtamanConsole

    dead: list[str] = []
    for screen_cls in _screen_classes():
        for b in screen_cls.BINDINGS:
            action = _binding_action(b)
            if not _resolves(screen_cls, action, OtamanConsole):
                dead.append(f"{screen_cls.__name__}: {action}")
    assert dead == [], f"advertised-but-dead binding actions: {dead}"


def test_q_binding_targets_app_quit_everywhere():
    # regression: q must target app.quit (a bare 'quit' is dropped on a Screen).
    for screen_cls in _screen_classes():
        for b in screen_cls.BINDINGS:
            key = b.key if hasattr(b, "key") else b[0]
            if key == "q":
                assert _binding_action(b) == "app.quit", f"{screen_cls.__name__} q is not app.quit"


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console import bus

    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    return bus.Program(name="demo", root=root)


def test_pressing_q_quits(program):
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")  # was dead before the fix
            await pilot.pause()
        # exiting run_test without hanging + a return_code set == quit fired
        assert app.return_code is not None

    asyncio.run(go())


def test_keypress_echo_updates_subtitle(program):
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")  # any key
            await pilot.pause()
            assert "r" in app.sub_title  # the key was echoed
            await app.action_quit()

    asyncio.run(go())
