"""console-ux-redesign wave 2, task 2.1 — the Setup section (D4/S7).

Setup administers by VISIBLY shelling out to the tested CLI verbs against the
picked program and surfacing their results — the shell-out round-trip gate 3.1
checks. These cover the injectable runner (command shape, scoping to the program
root, output capture, failure surfacing) and the pilots: Home `s` → Setup, and
selecting a verb runs it and shows the output.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from types import SimpleNamespace

import pytest

from otaman_cli.console import bus, setup

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


# ---------------------------------------------------------------------------
# the menu is read-first, real verbs, values-free


def test_setup_menu_shells_out_to_real_verbs():
    argvs = [v.argv for v in setup.SETUP_VERBS]
    assert ("project", "list") in argvs
    assert ("sync-repos", "--dry-run") in argvs
    assert ("human", "list") in argvs  # team roster (list-first)
    assert ("connection", "map") in argvs  # the values-free credentials map


# ---------------------------------------------------------------------------
# run_verb — the shell-out round-trip


def test_run_verb_builds_command_scoped_to_program(program):
    seen = {}

    def _runner(cmd, *, cwd, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        return SimpleNamespace(returncode=0, stdout="two projects\n", stderr="")

    result = setup.run_verb(program, ("project", "list"), runner=_runner)
    assert result.ok and result.output == "two projects"
    assert result.command == "otaman project list"
    assert seen["cwd"] == str(program.root)  # scoped to the picked program
    assert seen["cmd"][-2:] == ["project", "list"]


def test_run_verb_surfaces_failure(program):
    def _runner(cmd, **kw):
        return SimpleNamespace(returncode=2, stdout="", stderr="boom\n")

    result = setup.run_verb(program, ("connection", "check"), runner=_runner)
    assert not result.ok and result.returncode == 2 and result.output == "boom"


def test_run_verb_never_raises_on_spawn_error(program):
    def _runner(cmd, **kw):
        raise OSError("no such binary")

    result = setup.run_verb(program, ("project", "list"), runner=_runner)
    assert not result.ok and "failed to run" in result.output


# ---------------------------------------------------------------------------
# pilots


@_textual
def test_home_s_opens_setup(program):
    from otaman_cli.console.app import HomeScreen, OtamanConsole, SetupScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action("setup")
            await pilot.pause()
            assert isinstance(app.screen, SetupScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_setup_verb_runs_and_shows_output(program, monkeypatch):
    # monkeypatch the runner so no real process spawns; assert the round-trip
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="active: demo\n", stderr=""),
    )
    from textual.widgets import ListView

    from otaman_cli.console.app import OtamanConsole, SetupResultScreen, SetupScreen

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SetupScreen(program))
            await pilot.pause()
            lv = app.screen.query_one("#setup-list", ListView)
            assert len(lv.children) == len(setup.SETUP_VERBS)
            lv.focus()
            lv.index = 0
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, SetupResultScreen)
            assert app.screen._result is not None
            assert app.screen._result.ok and "active: demo" in app.screen._result.output
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_add_project_shells_out_assign(program, monkeypatch):
    # the 3.1 scenario: register a project without leaving the console → the
    # console visibly runs `otaman project assign <path> --owner <agent>`.
    seen = {}

    def _run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="assigned ../my-repo\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    from textual.widgets import Input

    from otaman_cli.console.app import (
        AddProjectScreen,
        OtamanConsole,
        SetupResultScreen,
        SetupScreen,
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SetupScreen(program))
            await pilot.pause()
            await app.screen.run_action("add_project")
            await pilot.pause()
            assert isinstance(app.screen, AddProjectScreen)
            app.screen.query_one("#proj-path", Input).value = "../my-repo"
            app.screen.query_one("#proj-owner", Input).value = "backend-agent"
            app.screen.query_one("#proj-owner", Input).focus()
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, SetupResultScreen)
            # the tested verb chain ran with the collected args
            assert seen["cmd"][-4:] == ["project", "assign", "../my-repo", "--owner"] or seen[
                "cmd"
            ][-5:] == ["project", "assign", "../my-repo", "--owner", "backend-agent"]
            assert "assigned" in app.screen._result.output
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_add_project_requires_both_fields(program):
    from textual.widgets import Input

    from otaman_cli.console.app import AddProjectScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(AddProjectScreen(program))
            await pilot.pause()
            # submit with empty fields → stays on the form (no shell-out)
            app.screen.query_one("#proj-path", Input).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AddProjectScreen)
            await app.action_quit()

    asyncio.run(go())
