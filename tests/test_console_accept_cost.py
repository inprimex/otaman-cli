"""team-mode 2.4a — console accept-cost action (Roman fast-track)."""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from otaman_cli.console import accept_cost, bus

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n"
        "human-roster:\n  - {name: roman, roles: [ceo, founder]}\n",
        encoding="utf-8",
    )
    strat = tmp_path / "strat"
    strat.mkdir()
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    return bus.Program(name="demo", root=root), strat


def _outcomes(strat, body):
    (strat / "outcomes.yaml").write_text(body, encoding="utf-8")


def _solutions(strat, body):
    (strat / "solutions.yaml").write_text(body, encoding="utf-8")


def test_candidate_uses_chosen_solution(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1, chosen-solution: SOL-1, cost-accepted: false}\n")
    sol, note = accept_cost.accept_cost_candidate(prog, "JTBD-1")
    assert sol == "SOL-1" and "SOL-1" in note


def test_candidate_single_estimated_solution(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _solutions(strat, "solutions:\n  - {id: SOL-9, outcome-id: JTBD-1, effort-days: 3}\n")
    sol, _ = accept_cost.accept_cost_candidate(prog, "JTBD-1")
    assert sol == "SOL-9"


def test_candidate_none_when_cost_accepted(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1, chosen-solution: SOL-1, cost-accepted: true}\n")
    sol, note = accept_cost.accept_cost_candidate(prog, "JTBD-1")
    assert sol is None and "already accepted" in note


def test_candidate_none_when_multiple_estimated(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _solutions(
        strat,
        "solutions:\n"
        "  - {id: SOL-1, outcome-id: JTBD-1, effort-days: 3}\n"
        "  - {id: SOL-2, outcome-id: JTBD-1, effort-days: 5}\n",
    )
    sol, note = accept_cost.accept_cost_candidate(prog, "JTBD-1")
    assert sol is None and "choose one" in note


def test_candidate_none_when_no_estimate(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1}\n")
    _solutions(strat, "solutions:\n  - {id: SOL-1, outcome-id: JTBD-1}\n")
    assert accept_cost.accept_cost_candidate(prog, "JTBD-1")[0] is None


def test_is_offerable(program):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1, chosen-solution: SOL-1}\n")
    assert accept_cost.is_accept_cost_offerable(prog, "JTBD-1") is True


def test_acting_hat_holds_ceo(program, monkeypatch):
    prog, _ = program
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    holds, who = accept_cost.acting_hat_holds(prog)
    assert holds is True and who == "roman"


def test_acting_hat_absent_is_advisory_false(program, monkeypatch):
    prog, _ = program
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    holds, _ = accept_cost.acting_hat_holds(prog)
    assert holds is False


def test_run_accept_cost_builds_verb(program):
    prog, _ = program
    calls = {}

    def fake_runner(cmd, **kw):
        from types import SimpleNamespace

        calls["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    r = accept_cost.run_accept_cost(prog, "JTBD-1", "SOL-1", runner=fake_runner)
    assert r.ok
    assert "outcome" in calls["cmd"] and "accept-cost" in calls["cmd"]
    assert "JTBD-1" in calls["cmd"] and "SOL-1" in calls["cmd"]


@_textual
def test_registry_detail_offers_and_runs_accept_cost(program, monkeypatch):
    prog, strat = program
    _outcomes(strat, "outcomes:\n  - {id: JTBD-1, chosen-solution: SOL-1, cost-accepted: false}\n")
    from otaman_cli.console import accept_cost as ac
    from otaman_cli.console.app import OtamanConsole, RegistryDetailScreen

    monkeypatch.setattr(
        ac, "run_accept_cost", lambda *a, **k: type("R", (), {"ok": True, "output": "done"})()
    )
    monkeypatch.setattr(ac, "acting_hat_holds", lambda *a, **k: (True, "roman"))

    async def go():
        app = OtamanConsole([prog], search_root=prog.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(RegistryDetailScreen(prog, "outcome", "JTBD-1"))
            await pilot.pause()
            from textual.widgets import Static as _Static

            body = app.screen.query_one("#registry-detail", _Static)
            assert "ACCEPT-COST available" in str(body.render())
            app.screen.action_accept_cost()
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
