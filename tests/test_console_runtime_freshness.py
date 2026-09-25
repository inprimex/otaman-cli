"""session-runtime-freshness 1.3 — the console renders plugin's verdicts.

The defect this change exists for: a running session silently outlives the
config it depends on, and nothing says which one you are looking at. It
produced three false bug reports in one week — plugin's anchor guard against an
old core, spec-agent's gate measuring an installed tenant while both fixes sat
in source, and plugin's `--past-due` against an install predating cli #200.

So the load-bearing property here is NOT that the table renders. It is that
`not-checked` never renders as silence: a view that shows nothing when it could
not check looks exactly like a view that checked and found nothing wrong, which
is the failure one level up from the one being fixed.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from otaman_cli.console import bus, freshness

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


class _Finding:
    def __init__(self, verdict, check="c", subject="s", reason="r", remedy=""):
        self.verdict, self.check, self.subject = verdict, check, subject
        self.reason, self.remedy = reason, remedy


# ---------------------------------------------------------------------------
# no second checker


def test_the_console_consumes_plugins_assess_and_does_not_reimplement_it():
    """1.3's whole constraint. A cli-side checker would be a second opinion
    about "stale", which is the defect one level up."""
    source = Path(freshness.__file__).read_text(encoding="utf-8")
    assert "runtime_freshness" in source and "assess" in source
    # No local re-derivation of the things plugin's checks compare.
    for reimplemented in ("st_mtime", "/proc/", "psutil", "started_at"):
        assert reimplemented not in source, f"cli is re-deriving {reimplemented!r}"


def test_rows_come_back_worst_verdict_first(monkeypatch, tmp_path):
    """The row that should change behaviour is the row on screen."""
    monkeypatch.setattr(
        freshness,
        "_checker",
        lambda: type(
            "M",
            (),
            {
                "assess": staticmethod(
                    lambda root: [
                        _Finding("fresh"),
                        _Finding("not-checked"),
                        _Finding("stale"),
                        _Finding("skewed"),
                    ]
                )
            },
        )(),
    )
    verdicts = [r.verdict for r in freshness.assess_rows(tmp_path)]
    assert verdicts == ["stale", "skewed", "not-checked", "fresh"]


# ---------------------------------------------------------------------------
# not-checked is never silence


def test_a_plugin_without_the_checks_yields_not_checked_not_an_empty_list(monkeypatch, tmp_path):
    """An empty list reads as "nothing is stale" — the claim we are least
    entitled to make when we could not look."""
    monkeypatch.setattr(freshness, "_checker", lambda: None)
    rows = freshness.assess_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0].verdict == freshness.VERDICT_NOT_CHECKED
    assert "otaman-plugin" in rows[0].reason
    assert rows[0].remedy, "a not-checked row must say how to make it checkable"


def test_a_checker_that_raises_yields_not_checked_rather_than_blanking(monkeypatch, tmp_path):
    def boom(root):
        raise RuntimeError("proc table unreadable")

    monkeypatch.setattr(
        freshness, "_checker", lambda: type("M", (), {"assess": staticmethod(boom)})()
    )
    rows = freshness.assess_rows(tmp_path)
    assert rows[0].verdict == freshness.VERDICT_NOT_CHECKED
    assert "proc table unreadable" in rows[0].reason


def test_an_empty_assessment_is_reported_as_not_checked(monkeypatch, tmp_path):
    """plugin returns [] for a non-program root — "nothing to be fresh RELATIVE
    TO", which is not "all fresh"."""
    monkeypatch.setattr(
        freshness,
        "_checker",
        lambda: type("M", (), {"assess": staticmethod(lambda root: [])})(),
    )
    rows = freshness.assess_rows(tmp_path)
    assert len(rows) == 1 and rows[0].verdict == freshness.VERDICT_NOT_CHECKED


def test_the_summary_names_every_verdict_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        freshness,
        "_checker",
        lambda: type(
            "M",
            (),
            {
                "assess": staticmethod(
                    lambda root: [
                        _Finding("stale"),
                        _Finding("fresh"),
                        _Finding("fresh"),
                    ]
                )
            },
        )(),
    )
    summary = freshness.summarize(freshness.assess_rows(tmp_path))
    assert "1 stale" in summary and "2 fresh" in summary


def test_an_unknown_verdict_sorts_last_rather_than_crashing():
    """plugin may add a verdict before cli learns to rank it; that must degrade
    to "shown at the bottom", never to a traceback."""
    assert freshness.Row(verdict="invented", check="c", subject="s", reason="r").rank == len(
        freshness.VERDICT_ORDER
    )


# ---------------------------------------------------------------------------
# the screen


@_textual
def test_the_view_opens_and_renders_a_row_per_finding(program, monkeypatch):
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole, SessionFreshnessScreen

    monkeypatch.setattr(
        freshness,
        "_checker",
        lambda: type(
            "M",
            (),
            {
                "assess": staticmethod(
                    lambda root: [
                        _Finding(
                            "stale", reason="config changed under it", remedy="restart the session"
                        ),
                        _Finding("fresh"),
                    ]
                )
            },
        )(),
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SessionFreshnessScreen(program))
            await pilot.pause()
            table = app.screen.query_one("#freshness-rows", DataTable)
            assert table.row_count == 2
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_remedy_is_shown_beside_the_verdict(program, monkeypatch):
    """A verdict the reader cannot act on is a verdict that gets ignored."""
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole, SessionFreshnessScreen

    monkeypatch.setattr(
        freshness,
        "_checker",
        lambda: type(
            "M",
            (),
            {
                "assess": staticmethod(
                    lambda root: [
                        _Finding(
                            "stale", reason="outlived its config", remedy="restart the session"
                        ),
                    ]
                )
            },
        )(),
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SessionFreshnessScreen(program))
            await pilot.pause()
            table = app.screen.query_one("#freshness-rows", DataTable)
            cells = [str(c) for c in table.get_row_at(0)]
            assert any("restart the session" in c for c in cells)
            await app.action_quit()

    asyncio.run(go())


@_textual
def test_the_binding_is_advertised(program):
    """This console's own show=False audit (sdca 1.4): a staleness view nobody
    can find does not prevent a stale session."""
    from otaman_cli.console.app import OtamanConsole

    bindings = {b.action: b for b in OtamanConsole.BINDINGS if hasattr(b, "action")}
    assert "session_freshness" in bindings
    assert bindings["session_freshness"].show is not False


@_textual
def test_refresh_reassesses(program, monkeypatch):
    from textual.widgets import DataTable

    from otaman_cli.console.app import OtamanConsole, SessionFreshnessScreen

    calls = {"n": 0}

    def assess(root):
        calls["n"] += 1
        return [_Finding("fresh")] * calls["n"]

    monkeypatch.setattr(
        freshness, "_checker", lambda: type("M", (), {"assess": staticmethod(assess)})()
    )

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SessionFreshnessScreen(program))
            await pilot.pause()
            app.screen.action_refresh()
            await pilot.pause()
            table = app.screen.query_one("#freshness-rows", DataTable)
            assert table.row_count == 2, "refresh did not re-run the assessment"
            await app.action_quit()

    asyncio.run(go())
