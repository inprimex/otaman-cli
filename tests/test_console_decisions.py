"""team-mode 2.4b — console CHOOSE / DISCARD actions on solution nodes."""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import bus, decisions

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - {name: roman, roles: [cto, founder]}\n",
        encoding="utf-8",
    )
    strat = tmp_path / "strat"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    return bus.Program(name="demo", root=root), strat


def _out(strat, body):
    (strat / "outcomes.yaml").write_text(body, encoding="utf-8")


def _sol(strat, body):
    (strat / "solutions.yaml").write_text(body, encoding="utf-8")


def test_choose_candidate_offerable(program):
    prog, strat = program
    _out(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    oid, note = decisions.choose_candidate(prog, "SOL-1")
    assert oid == "JTBD-1" and "JTBD-1" in note


def test_choose_candidate_none_when_already_chosen(program):
    prog, strat = program
    _out(strat, "outcomes:\n  - {id: JTBD-1, chosen-solution: SOL-1}\n")
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    assert decisions.choose_candidate(prog, "SOL-1")[0] is None


def test_choose_candidate_none_when_discarded(program):
    prog, strat = program
    _out(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1, status: Discarded}\n")
    assert decisions.choose_candidate(prog, "SOL-1")[0] is None


def test_discard_candidate(program):
    prog, strat = program
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    ok, _ = decisions.discard_candidate(prog, "SOL-1")
    assert ok is True


def test_discard_candidate_none_when_already_discarded(program):
    prog, strat = program
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1, status: Discarded}\n")
    assert decisions.discard_candidate(prog, "SOL-1")[0] is False


def test_run_choose_and_discard_build_verbs(program):
    prog, _ = program
    seen = []

    def runner(cmd, **kw):
        from types import SimpleNamespace

        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    decisions.run_choose(prog, "JTBD-1", "SOL-1", runner=runner)
    decisions.run_discard(prog, "SOL-1", "not viable", runner=runner)
    assert ["outcome", "choose", "JTBD-1", "--solution", "SOL-1"] == seen[0][1:]
    assert ["solution", "discard", "SOL-1", "--reason", "not viable"] == seen[1][1:]


@_textual
def test_registry_detail_choose_on_solution(program, monkeypatch):
    prog, strat = program
    _out(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    from otaman_cli.console import decisions as dc
    from otaman_cli.console.app import OtamanConsole, RegistryDetailScreen

    seen = {}
    monkeypatch.setattr(dc, "acting_decision_hat", lambda *a, **k: (True, "roman"))
    monkeypatch.setattr(
        dc,
        "run_choose",
        lambda program, oid, sid, **k: (
            seen.update(oid=oid, sid=sid) or type("R", (), {"ok": True, "output": "done"})()
        ),
    )

    async def go():
        app = OtamanConsole([prog], search_root=prog.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(RegistryDetailScreen(prog, "solution", "SOL-1"))
            await pilot.pause()
            from textual.widgets import Static as _Static

            body = app.screen.query_one("#registry-detail", _Static)
            assert "CHOOSE available" in str(body.render())
            app.screen.action_choose()
            await pilot.pause()
            assert seen == {"oid": "JTBD-1", "sid": "SOL-1"}
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_registry_detail_discard_prompts_for_reason(program, monkeypatch):
    prog, strat = program
    _out(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _sol(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    from otaman_cli.console.app import OtamanConsole, ReasonModal, RegistryDetailScreen

    async def go():
        app = OtamanConsole([prog], search_root=prog.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(RegistryDetailScreen(prog, "solution", "SOL-1"))
            await pilot.pause()
            app.screen.action_discard()
            await pilot.pause()
            assert isinstance(app.screen, ReasonModal)  # a reason is required
            await app.action_quit()

    asyncio.run(go())
