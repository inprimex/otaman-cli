"""`otaman connection check` reports a CHANGE since the last check, not just a status.

deploy-agent named the gap (20261004T144624) while declining to write an expiry date
beside a credential:

    "a transition from ok to auth-failed is an EVENT, not a line in a report"

The store already held the previous verdict — `persist_reports` writes it on every check
and `load_reports` reads it — and nothing read it before overwriting. So the one fact that
makes a new `auth-failed` actionable (that it is NEW) was being destroyed by the act of
measuring. deploy's own framing: the same shape as their fragments-consumed signal, a
correct measurement on a surface with no reader.

This is the reporting half only. Emitting a `decision-required` on the transition — the
`harness-version-management` 2.2 pattern deploy pointed at — is messaging plus scheduling
and is proposed rather than built here.

Three states, deliberately: changed, unchanged, and no-comparand. Saying nothing on the
third would make a first-ever check look identical to a stable one.
"""

from __future__ import annotations

import pytest

import otaman_cli.commands.connection as C


class _Conn:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "https"
        self.endpoint = "https://example.invalid"
        self.secret_ref = "GH_TOKEN"
        self.ssh_ref = None
        self.scope = "program"


class _Report:
    def __init__(self, name: str, status: str) -> None:
        self.name = name
        self.status = status
        self.detail = "probe detail"
        self.healed = False
        self.checked_at = "2026-10-04T15:00:00+00:00"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Drive `_cmd_check` with a stubbed checker and a controllable prior store."""
    state: dict = {"prior": {}, "now": []}

    monkeypatch.setattr(C, "_resolve_connections", lambda root: [_Conn("gh-specs")])
    monkeypatch.setattr(C, "_available_keys", lambda root: {"GH_TOKEN"})
    monkeypatch.setattr(C, "_program_name", lambda root: "demo")

    import otaman_core.connection_check as CC

    class _Checker:
        def __init__(self, **kw) -> None:
            pass

        def check_all(self, targets, fix=False):
            return state["now"]

    monkeypatch.setattr(CC, "ConnectionChecker", _Checker)
    monkeypatch.setattr(CC, "load_reports", lambda path, program: state["prior"])
    monkeypatch.setattr(CC, "persist_reports", lambda *a, **k: None)
    monkeypatch.setattr(CC, "report_store_path", lambda: tmp_path / "reports.yaml")
    monkeypatch.setattr(CC, "render_last_check", lambda r: "just now")
    return state


def _run(capsys) -> str:
    C._cmd_check(object(), ["gh-specs"])
    return "".join(capsys.readouterr())


# ---------------------------------------------------------------------------
# the event


def test_ok_to_auth_failed_is_reported_as_a_CHANGE(wired, capsys):
    """The moment an expired credential becomes actionable."""
    wired["prior"] = {"gh-specs": _Report("gh-specs", "ok")}
    wired["now"] = [_Report("gh-specs", "auth-failed")]

    out = _run(capsys)

    assert "CHANGED since last check" in out
    assert "ok" in out and "auth-failed" in out


def test_a_recovery_is_reported_too(wired, capsys):
    """auth-failed -> ok matters as much: it is the evidence a rotation worked."""
    wired["prior"] = {"gh-specs": _Report("gh-specs", "auth-failed")}
    wired["now"] = [_Report("gh-specs", "ok")]

    out = _run(capsys)

    assert "CHANGED since last check" in out
    assert "auth-failed" in out and "ok" in out


def test_an_unchanged_status_says_so_rather_than_going_quiet(wired, capsys):
    """ "Still broken" and "newly broken" are different facts; silence conflates them."""
    wired["prior"] = {"gh-specs": _Report("gh-specs", "auth-failed")}
    wired["now"] = [_Report("gh-specs", "auth-failed")]

    out = _run(capsys)

    assert "change: none" in out
    assert "CHANGED" not in out


def test_no_previous_check_is_its_OWN_state(wired, capsys):
    """The third state. A first-ever check must not read as a stable one — that is the
    could-not-measure case this fleet keeps relearning."""
    wired["prior"] = {}
    wired["now"] = [_Report("gh-specs", "ok")]

    out = _run(capsys)

    assert "no previous check" in out
    assert "change: none" not in out


def test_the_prior_is_read_BEFORE_the_store_is_overwritten(wired, capsys):
    """Ordering is the whole fix: persist-then-compare would compare a value to itself
    and report every connection as unchanged, forever."""
    import inspect

    src = inspect.getsource(C._cmd_check)

    assert src.index("load_reports(") < src.index("persist_reports("), (
        "the store is overwritten before its prior value is read"
    )


def test_an_unreadable_store_is_not_a_failed_check(wired, capsys, monkeypatch):
    """No comparand is a missing fact, not a probe failure."""
    import otaman_core.connection_check as CC

    def boom(path, program):
        raise OSError("store unreadable")

    monkeypatch.setattr(CC, "load_reports", boom)
    wired["now"] = [_Report("gh-specs", "ok")]

    out = _run(capsys)

    assert "no previous check" in out
