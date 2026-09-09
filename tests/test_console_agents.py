"""interactive-human-console — per-agent assigned-tasks view.

Roman wanted to see the tasks assigned to each agent in `otaman -i`. This is a
plain read of the program's queue files — NOT the captured-items/backlog concept
(that's a separate spec). Covers the lenient queue parser + the pilots (Home `a`
→ Agents, Enter → one agent's tasks).
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus
from otaman_cli.console.tasks import list_agent_tasks

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "queue").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _queue(program, agent, text):
    (program.root / ".agents" / "queue" / f"{agent}.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# parser


def test_parses_live_buckets_and_counts_done(program):
    _queue(
        program,
        "cli-agent",
        "# Task Queue — cli-agent\n\n"
        "## Active\n\n- **task A** doing it\n  continuation line (not a task)\n\n"
        "## Queued\n\n- task B\n- task C\n\n"
        "## Blocked\n\n- **task D** waiting on core\n\n"
        "## Done (2026-08)\n\n- old 1\n- old 2\n- old 3\n",
    )
    (t,) = list_agent_tasks(program)
    assert t.agent == "cli-agent"
    assert t.active == ["task A doing it"]  # ** stripped, continuation ignored
    assert t.queued == ["task B", "task C"]
    assert [x.startswith("task D") for x in t.blocked] == [True]
    assert t.done == 3
    assert t.open_count == 4


def test_empty_and_missing_queue(program):
    _queue(program, "core-agent", "# Task Queue — core-agent\n\n## Active\n\n(none)\n")
    (t,) = list_agent_tasks(program)
    assert t.open_count == 0 and t.done == 0  # "(none)" is not a bullet


def test_no_queue_dir_yields_nothing(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    assert list_agent_tasks(bus.Program(name="bare", root=root)) == []


# ---------------------------------------------------------------------------
# pilots


@_textual
def test_home_a_opens_agents(program):
    _queue(program, "cli-agent", "## Active\n\n- t1\n")
    from otaman_cli.console.app import AgentsScreen, HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await app.screen.run_action("agents")
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_agents_enter_opens_agent_tasks(program):
    _queue(
        program, "cli-agent", "## Active\n\n- **big task** in flight\n\n## Queued\n\n- next up\n"
    )
    from textual.widgets import ListView

    from otaman_cli.console.app import AgentsScreen, AgentTasksScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(AgentsScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            lv = app.screen.query_one("#agents-list", ListView)
            assert len(lv.children) == 1
            lv.focus()
            lv.index = 0
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AgentTasksScreen)
            body = AgentTasksScreen._body_text(app.screen.tasks)
            assert "ACTIVE (1)" in body and "big task in flight" in body and "next up" in body
            await app.action_quit()

    asyncio.run(go())
