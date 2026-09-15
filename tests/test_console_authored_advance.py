"""ratify-spec-approve-split 1.5 — the lifecycle screen's human ADVANCE action.

The human is the blocker at two different lifecycle steps: an AUTHORED change
waits on the dispatch gate (advance to spec-approved), a COMPLETE one waits
ratify-blocked at the archive gate (ratify). `y` previously served only the
second and answered the first with "not ratify-blocked" — a dead end on a step
the screen itself displays. Canon now forbids that: the action runs, or refuses
NAMING the command that does.
"""

from __future__ import annotations

import asyncio
import importlib.util
from types import SimpleNamespace

import pytest
import yaml

from otaman_cli.console import bus

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "specs": {"path": "../specs"},
                "human-roster": [{"name": "roman", "email": "r@x.io", "roles": ["cto"]}],
            }
        ),
        encoding="utf-8",
    )
    changes = tmp_path / "specs" / "openspec" / "changes"
    authored = changes / "authored-change"
    authored.mkdir(parents=True)
    (authored / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    (authored / "proposal.md").write_text("# p\n", encoding="utf-8")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    return bus.Program(name="demo", root=root)


def _row(name: str, *, stage: str, next_actor: str = "cli-agent"):
    return SimpleNamespace(
        name=name,
        stage=stage,
        state="in-flight",
        tasks_done=0,
        tasks_total=1,
        age="1d",
        days_in_state=1,
        next_actor=next_actor,
        triage="active",
        triage_note="",
        last_touch="?",
        last_nudged="",
        delivery=None,
    )


# ---------------------------------------------------------------------------
# the offer condition comes from ONE source


@_textual
def test_advanceable_uses_the_shared_authored_set(program):
    """The console's offer condition is `list_authored_changes` — the same set
    the b-screen offers and `advance_to_spec_approved` accepts — so the offer
    can't drift from the acceptance."""
    from otaman_cli.console.app import LifecycleScreen

    screen = LifecycleScreen(program)
    assert screen._advanceable("authored-change") is True
    assert screen._advanceable("no-such-change") is False


@_textual
def test_advanceable_is_false_when_specs_unresolvable(tmp_path, monkeypatch):
    from otaman_cli.console.app import LifecycleScreen

    root = tmp_path / "bare"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("project: x\nversion: '1.0'\n", encoding="utf-8")
    screen = LifecycleScreen(bus.Program(name="x", root=root))
    assert screen._advanceable("anything") is False  # graceful, never raises


# ---------------------------------------------------------------------------
# pressing the advance key on an AUTHORED row


@_textual
def test_authored_row_offers_the_advance(program, monkeypatch):
    """The scenario: authored row + advance key → the action runs (a reason
    prompt opens), never 'not ratify-blocked'."""
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole, ReasonModal

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = LifecycleScreen(program)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(
                type(app.screen),
                "_highlighted",
                lambda self: _row("authored-change", stage="authored"),
                raising=False,
            )
            app.screen.action_ratify()
            await pilot.pause()
            assert isinstance(app.screen, ReasonModal)  # the action RAN
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_advance_runs_the_shared_action(program, monkeypatch):
    """Confirming the advance goes through `advance_to_spec_approved` — the same
    code `otaman spec approve` and the b-screen run."""
    from otaman_cli.console import artifacts
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    seen: dict = {}
    monkeypatch.setattr(
        artifacts,
        "advance_to_spec_approved",
        lambda prog, name, reason="": seen.update(name=name, reason=reason) or (True, "ok"),
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.screen._advance_to_spec_approved(_row("authored-change", stage="authored"))
            await pilot.pause()
            # answer the reason prompt the way the human does: type, then Enter
            # (ReasonModal.on_input_submitted dismisses with the typed value).
            app.screen.on_input_submitted(SimpleNamespace(value="looks good"))
            await pilot.pause()
            assert seen == {"name": "authored-change", "reason": "looks good"}
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# no dead ends: a non-applicable row names the command that applies


@_textual
def test_non_applicable_row_names_the_commands(program, monkeypatch):
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    notes: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(
                type(app.screen),
                "_highlighted",
                lambda self: _row("dispatched-change", stage="dispatched"),
                raising=False,
            )
            monkeypatch.setattr(
                type(app), "notify", lambda self, msg, **kw: notes.append(str(msg)), raising=False
            )
            app.screen.action_ratify()
            await pilot.pause()
            assert notes, "the key must never be silently inert"
            msg = notes[0]
            # the forbidden dead end is gone...
            assert "not ratify-blocked" not in msg
            # ...and the refusal names BOTH working commands + the actual stage
            assert "otaman spec approve dispatched-change" in msg
            assert "otaman ratify dispatched-change" in msg
            assert "dispatched" in msg
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_ratify_blocked_row_still_ratifies(program, monkeypatch):
    """The original path is untouched: a ratify-blocked row still prompts for a
    (mandatory) ratify reason rather than being hijacked by the advance."""
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole, ReasonModal

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(
                type(app.screen),
                "_highlighted",
                lambda self: _row(
                    "complete-change", stage="implemented", next_actor="human (ratify)"
                ),
                raising=False,
            )
            app.screen.action_ratify()
            await pilot.pause()
            assert isinstance(app.screen, ReasonModal)
            assert "ratify" in str(app.screen.query_one("#reason-prompt").render()).lower()
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_no_highlighted_row_is_a_safe_noop(program):
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.screen.action_ratify()  # empty table → must not raise
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# the keyboard contract


@_textual
def test_advance_key_is_still_bound_and_relabelled(program):
    from otaman_cli.console.app import LifecycleScreen

    binds = {b.key: b for b in LifecycleScreen.BINDINGS}
    assert "y" in binds
    assert binds["y"].action == "ratify"  # stable action name
    assert "approve" in binds["y"].description.lower()  # surfaces the dual role


@_textual
def test_footer_hint_names_the_dual_role(program):
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            from textual.widgets import Static

            banner = str(app.screen.query_one("#mode-banner", Static).render())
            assert "approve" in banner.lower()
            await app.action_quit()

    asyncio.run(go())
