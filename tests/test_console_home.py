"""console-ux-redesign wave 1, task 1.1 — the Home orientation screen (D1/S1).

Home aggregates existing surfaces into one glance and never re-derives. These
cover the Textual-free data layer (build_home_summary), the adaptive-vocabulary
text builders (fleet shows only present states; feature-usage stays reserved),
and the pilots: picker → Home, sections render, and every advertised jump key
dispatches (d/l/b land on the real screens; t/s acknowledge without a dead key).
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.home import HomeSummary, build_home_summary

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\n"
        "version: '1.0'\n"
        "repos:\n"
        "  - {name: a, path: ., owner: cli-agent}\n"
        "  - {name: b, path: ../b, owner: core-agent}\n"
        "program:\n"
        "  processes:\n"
        "    outcomes: {enabled: true}\n"
        "    solutions: {enabled: false}\n"
        "skills:\n"
        "  profile: sw-default\n"
        "  extra: [refactor, review]\n"
        "human-roster:\n"
        "  - {name: roman, roles: [cto, approver]}\n",
        encoding="utf-8",
    )
    return bus.Program(name="demo", root=root)


# ---------------------------------------------------------------------------
# build_home_summary — the data layer


def test_summary_reads_platform_panels(program):
    s = build_home_summary(program)
    assert s.processes_enabled == ["outcomes"]  # disabled ones excluded
    assert s.team_agents == 2 and s.team_humans == 1
    assert s.skills == 3  # profile + 2 extra
    assert s.policy == "warn"  # default enforcement


def test_summary_is_safe_on_empty_program(tmp_path):
    root = tmp_path / "bare"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: bare\nversion: '1.0'\nrepos: []\n", "utf-8")
    s = build_home_summary(bus.Program(name="bare", root=root))
    # every stat degrades to a sentinel, never raises (presence default varies by
    # env; the invariant is no records → empty fleet map)
    assert s.decisions_total == 0 and s.changes_total == 0
    assert s.processes_enabled == [] and s.fleet == {}


# ---------------------------------------------------------------------------
# text builders — adaptive vocabulary + reserved feature-usage


def test_fleet_line_shows_only_present_states():
    from otaman_cli.console.app import HomeScreen

    s = HomeSummary(fleet={"working": 2, "idle": 7}, presence_enabled=True)
    line = HomeScreen._fleet_line(s)
    assert "2 working" in line and "7 idle" in line
    assert "limited" not in line and "blocked" not in line  # not present → not shown


def test_fleet_line_when_presence_disabled():
    from otaman_cli.console.app import HomeScreen

    assert "presence disabled" in HomeScreen._fleet_line(HomeSummary(presence_enabled=False))


def test_body_has_all_sections_and_no_reserved_number():
    from otaman_cli.console.app import HomeScreen

    s = HomeSummary(scr_count=3, ratify_blocked=2, spec_review=1, inbox_count=4, changes_total=48)
    body = HomeScreen._body_text(s)
    for section in ("YOUR QUEUE", "MESSAGES TO YOU", "PROGRAM", "PROCESSES", "SETUP STATS"):
        assert section in body
    assert "3 spec-change requests" in body and "48 active changes" in body
    assert "feature-usage" not in body.lower()  # reserved slot — no undefined number


# ---------------------------------------------------------------------------
# pilots


@_textual
def test_picker_selection_lands_on_home(program):
    from textual.widgets import ListView

    from otaman_cli.console.app import HomeScreen, OtamanConsole, ProgramPickerScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ProgramPickerScreen)
            app.screen.query_one("#program-list", ListView).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_home_renders_summary_off_thread(program):
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()  # the summary loads off-thread
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, HomeScreen)
            assert screen._summary is not None  # render ran
            body = HomeScreen._body_text(screen._summary)
            assert "YOUR QUEUE" in body and "SETUP STATS" in body
            await app.action_quit()

    asyncio.run(go())


@_textual
@pytest.mark.parametrize(
    "key,target",
    [
        ("decisions", "PendingListScreen"),
        ("lifecycle", "LifecycleScreen"),
        ("review", "ArtifactBrowserScreen"),
    ],
)
def test_jump_keys_land_on_real_screens(program, key, target):
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action(key)
            await pilot.pause()
            assert type(app.screen).__name__ == target
            await app.action_quit()

    asyncio.run(go())


@_textual
@pytest.mark.parametrize("key", ["setup"])
def test_reserved_keys_dispatch_without_dead_key(program, key):
    # s (wave 2) isn't built yet — the key must still dispatch with a visible ack
    # and NOT navigate away or crash (no dead advertised key). (t now opens the
    # real tree — covered in test_console_tree.)
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action(key)
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)  # stayed put, no crash
            await app.action_quit()

    asyncio.run(go())
