"""Gate 6.1 F1 — the ADVERTISED strip is the surface budget.

Roman, looking at the live console after P0-P4: "I still see on the bottom
line: d Decisions · m Messages · t Artifact tree · l Lifecycle · b Spec review
· a Agents · s Setup · r Refresh · esc Back · q Quit."

That is the old eight-door strip. The surface-budget requirement says the top
level SHALL be Home, Artifacts, Messages and Setup — and the PRESENTED strip is
the top level as far as a human is concerned, so from his seat nothing had
shrunk. D8 invokes JTBD-108's discipline, where retired entries become HIDDEN
aliases: working-but-advertised is not the same as retired.

The keyboard contract is unchanged. Every advertised key dispatches, and every
hidden alias STILL dispatches and still gets its pilot test — it is simply not
in the strip.
"""

from __future__ import annotations

import asyncio

import pytest
import yaml

_textual = pytest.mark.skipif(
    __import__("importlib").util.find_spec("textual") is None, reason="textual not installed"
)

#: The top level per the surface-budget requirement, plus the two keys spec-agent
#: named explicitly: refresh/back/quit, and `a` which stays advertised because
#: its door placement was never ruled.
ADVERTISED = {"t", "m", "s", "a", "r", "escape", "q"}

#: Retired doors: bound, landing on their new homes, out of the strip.
HIDDEN_ALIASES = {"d", "l", "b"}


@pytest.fixture
def program(tmp_path):
    from otaman_cli.console.bus import Program

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "demo", "version": "1.0", "repos": []}), encoding="utf-8"
    )
    return Program(name="demo", root=root)


def _bindings():
    from otaman_cli.console.app import HomeScreen

    return list(HomeScreen.BINDINGS)


def _shown(binding) -> bool:
    return getattr(binding, "show", True)


# ---------------------------------------------------------------------------
# what the strip advertises


def test_the_strip_advertises_only_the_budgeted_doors():
    advertised = {b.key for b in _bindings() if _shown(b)}
    assert advertised == ADVERTISED, f"strip drifted: {sorted(advertised)}"


@pytest.mark.parametrize("key", sorted(HIDDEN_ALIASES))
def test_each_retired_door_is_hidden(key):
    binding = next(b for b in _bindings() if b.key == key)
    assert _shown(binding) is False, f"{key} is still advertised"


def test_every_retired_door_is_still_bound():
    """Hidden, not removed — removal happens only after the usage signal is
    clean, and never in the same release as the move (D8)."""
    keys = {b.key for b in _bindings()}
    assert HIDDEN_ALIASES <= keys


def test_agents_stays_advertised_deliberately():
    """`a`'s door placement was never ruled; spec-agent put the question to
    Roman. Folding it here would decide an open question by implementation."""
    binding = next(b for b in _bindings() if b.key == "a")
    assert _shown(binding) is True


def test_the_banner_hint_matches_the_strip():
    """The banner is a second advertisement — it said `d decisions · … · b
    review` while the Footer said the same thing, so fixing one without the
    other would leave the eight doors on screen anyway."""
    import inspect

    from otaman_cli.console.app import HomeScreen

    src = inspect.getsource(HomeScreen.compose)
    for gone in ("d decisions", "l lifecycle", "b review"):
        assert gone not in src, f"banner still advertises {gone!r}"
    for kept in ("t artifacts", "m messages", "s setup"):
        assert kept in src, f"banner no longer advertises {kept!r}"


def test_the_artifacts_door_is_named_artifacts():
    """It read "Artifact tree" — the door is Artifacts (three lenses live
    behind it), and the strip should say what the door is."""
    binding = next(b for b in _bindings() if b.key == "t")
    assert binding.description == "Artifacts"


# ---------------------------------------------------------------------------
# the keyboard contract is untouched


@_textual
@pytest.mark.parametrize("key", sorted(HIDDEN_ALIASES))
def test_a_hidden_alias_still_dispatches(program, key, tmp_path):
    """ "An unadvertised alias still gets its pilot test, it just is not in the
    strip." Pressing it must still land somewhere — a hidden key that does
    nothing is a removal pretending to be an alias."""
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, HomeScreen), f"{key} dispatched nowhere"
            await app.action_quit()

    asyncio.run(go())


@_textual
@pytest.mark.parametrize("key", sorted(ADVERTISED - {"escape", "q", "r"}))
def test_every_advertised_key_dispatches(program, key, tmp_path):
    """The binding-conformance rule, unchanged: advertise nothing that does not
    work."""
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, HomeScreen), f"{key} dispatched nowhere"
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_a_retired_door_still_names_its_new_home(program, tmp_path):
    """Behaviour unchanged: the alias lands on its new home WITH the one-line
    notice. Hiding the key must not also hide the explanation."""
    from otaman_cli.console.app import HomeScreen, OtamanConsole

    notices: list[str] = []

    async def go():
        app = OtamanConsole([program], search_root=program.root, log_dir=tmp_path / "logs")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HomeScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify = lambda msg, **kw: notices.append(str(msg))  # type: ignore[assignment]
            await pilot.press("d")
            await pilot.pause()
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    assert any("Messages" in n for n in notices), notices
