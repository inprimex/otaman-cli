"""interactive-human-console 5.1 finding #2 — v1.0.x UX polish.

#2.1 stray header glyph removed, #2.2 priority key bindings, #2.3 theme
persistence, #2.4 paint-then-fill async load (loading row before the worker
fills the list). Textual pilot tests skip cleanly without the extra.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import prefs

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


# ---------------------------------------------------------------------------
# #2.3 prefs persistence (Textual-free)


def test_prefs_round_trip():
    # conftest isolates console_prefs_path to tmp home
    assert prefs.load_prefs() == {}
    prefs.save_prefs({"theme": "nord"})
    assert prefs.load_prefs()["theme"] == "nord"


def test_prefs_load_tolerates_missing_and_corrupt(tmp_path):
    assert prefs.load_prefs(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert prefs.load_prefs(bad) == {}


# ---------------------------------------------------------------------------
# #2.2 priority key bindings (structural)


@_textual
def test_key_bindings_are_priority():
    from otaman_cli.console.app import PendingListScreen, ProgramPickerScreen

    for screen in (ProgramPickerScreen, PendingListScreen):
        q = [b for b in screen.BINDINGS if b.key == "q"]
        assert q and q[0].priority is True  # plain q fires over a focused widget


# ---------------------------------------------------------------------------
# #2.1 header glyph + #2.4 async load + #2.3 theme via the App


def _program(tmp_path, monkeypatch, *, with_proposal=True):
    from otaman_cli.console import bus

    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    if with_proposal:
        (
            root
            / ".agents"
            / "bus"
            / "active"
            / "20260101T000000-a-to-human-spec-change-request.md"
        ).write_text(
            "---\nfrom: a\nto: human\npriority: high\ntype: spec-change-request\n"
            "timestamp: t\nstatus: pending\n---\n\n## Subject: Spec change request: x\n\nb\n",
            encoding="utf-8",
        )
    return bus.Program(name="p", root=root)


@_textual
def test_header_has_no_stray_glyph():
    from otaman_cli.console.app import _header

    assert _header().icon == ""  # 5.1 #2.1: HeaderIcon ⭘ removed (rendered as 'c')


@_textual
def test_async_load_shows_loading_then_fills(tmp_path, monkeypatch):
    program = _program(tmp_path, monkeypatch)
    from otaman_cli.console.app import OtamanConsole, PendingListScreen, _ProposalItem

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            # Paint-then-fill: the list is populated by a thread worker (off the
            # UI thread) so it fills after the scan rather than blocking first
            # paint; waiting for the worker then shows the proposal.
            await app.workers.wait_for_complete()
            await pilot.pause()
            lv = app.screen.query_one("#pending-list")
            assert len([c for c in lv.children if isinstance(c, _ProposalItem)]) == 1
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_back_navigation_renders_from_cache_instantly(tmp_path, monkeypatch):
    # 5.1 finding #5: returning from a proposal must paint the cached list
    # SYNCHRONOUSLY — no rescan on navigation. A thread worker can only deliver
    # results at an await point, so if the cached item is present immediately
    # after on_screen_resume() (with no await in between), it came from the
    # cache, not a rescan.
    program = _program(tmp_path, monkeypatch)
    from otaman_cli.console.app import OtamanConsole, PendingListScreen, _ProposalItem

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = PendingListScreen(program)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()  # first load fills the cache
            await pilot.pause()
            lv = screen.query_one("#pending-list")
            assert len([c for c in lv.children if isinstance(c, _ProposalItem)]) == 1
            assert screen._cache is not None and len(screen._cache) == 1  # cache filled

            # Spy the paint: on_screen_resume must paint from the cache
            # synchronously (a thread worker can only deliver at an await point,
            # so a synchronous paint here proves back-nav renders from cache).
            painted: list[list] = []
            monkeypatch.setattr(screen, "_paint", lambda proposals: painted.append(list(proposals)))
            screen.on_screen_resume()
            assert painted and len(painted[0]) == 1  # painted the cached list, no rescan
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_theme_restored_from_prefs_and_persisted_on_change(tmp_path, monkeypatch):
    program = _program(tmp_path, monkeypatch, with_proposal=False)
    prefs.save_prefs({"theme": "nord"})  # a saved preference
    from otaman_cli.console.app import OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "nord"  # restored on startup
            app.theme = "gruvbox"  # simulate a command-palette change
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    assert prefs.load_prefs()["theme"] == "gruvbox"  # persisted for next session


def _program_with_roster(tmp_path):
    from otaman_cli.console import bus

    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - name: roman\n    email: roman@example.com\n    roles: [founder]\n",
        encoding="utf-8",
    )
    return bus.Program(name="p", root=root)


@_textual
def test_identity_badge_persists_and_reflects_verification(tmp_path, monkeypatch):
    # Roman's request (deploy 2.1): a persistent top-right badge that shows,
    # before the human acts, whether their approvals will stamp VERIFIED.
    program = _program_with_roster(tmp_path)
    from otaman_cli.console.app import OtamanConsole, PendingListScreen

    def badge(app):
        w = app.screen.query_one("#identity-badge")
        return str(w.render()), set(w.classes)

    async def go():
        monkeypatch.setenv("OTAMAN_HUMAN", "roman")  # matches roster name
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            text, classes = badge(app)  # picker screen
            assert text == "✓ Verified(roman)" and "verified" in classes

            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            text, classes = badge(app)  # program screen — still verified
            assert text == "✓ Verified(roman)" and "verified" in classes

            monkeypatch.setenv("OTAMAN_HUMAN", "Ada Lovelace")  # name-format mismatch
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            text, classes = badge(app)
            assert text == "⚠ Unverified(Ada Lovelace)" and "unverified" in classes

            monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
            app.push_screen(PendingListScreen(program))
            await pilot.pause()
            text, classes = badge(app)
            assert text == "⚠ Unverified(none)" and "unverified" in classes
            await app.action_quit()

    asyncio.run(go())
