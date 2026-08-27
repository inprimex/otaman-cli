"""interactive-human-console 1.4 — self-managing private-tmux-server seat.

The create-or-attach command shape (private socket, recursion-guarded), the
should-seat gating (already-seated / --no-seat / no-tmux), and the launch
integration (seat AFTER the extra check, before running the app). No real
tmux or exec is invoked — exec_fn is injected.
"""

from __future__ import annotations

import sys

import pytest

from otaman_cli.console import seat
from otaman_cli.console.launch import run_console


def test_in_seat_reads_env(monkeypatch):
    monkeypatch.delenv(seat.SEAT_ENV, raising=False)
    assert seat.in_seat() is False
    monkeypatch.setenv(seat.SEAT_ENV, "1")
    assert seat.in_seat() is True


def test_inner_command_sets_marker_and_reinvokes():
    cmd = seat.inner_command(["--path", "/x"], otaman="/usr/bin/otaman")
    assert cmd == ["env", "OTAMAN_CONSOLE_SEAT=1", "/usr/bin/otaman", "-i", "--path", "/x"]


def test_build_attach_command_is_create_or_attach_on_private_socket():
    cmd = seat.build_attach_command([], otaman="otaman")
    assert cmd[:9] == [
        "tmux",
        "-L",
        seat.PRIVATE_SOCKET,
        "new-session",
        "-A",
        "-s",
        seat.SESSION_NAME,
        "--",
        "env",
    ]
    assert seat.PRIVATE_SOCKET == "otaman-human"  # private, not the default server


def test_should_seat_true_when_unseated_with_tmux(monkeypatch):
    monkeypatch.delenv(seat.SEAT_ENV, raising=False)
    monkeypatch.setattr(seat, "tmux_available", lambda: True)
    assert seat.should_seat([]) is True


def test_should_seat_false_when_already_seated(monkeypatch):
    monkeypatch.setenv(seat.SEAT_ENV, "1")
    monkeypatch.setattr(seat, "tmux_available", lambda: True)
    assert seat.should_seat([]) is False


def test_should_seat_false_with_no_seat_flag(monkeypatch):
    monkeypatch.delenv(seat.SEAT_ENV, raising=False)
    monkeypatch.setattr(seat, "tmux_available", lambda: True)
    assert seat.should_seat(["--no-seat"]) is False


def test_should_seat_false_without_tmux(monkeypatch):
    monkeypatch.delenv(seat.SEAT_ENV, raising=False)
    monkeypatch.setattr(seat, "tmux_available", lambda: False)
    assert seat.should_seat([]) is False


def test_reexec_execs_the_attach_command(monkeypatch):
    monkeypatch.delenv(seat.SEAT_ENV, raising=False)
    captured = {}

    def fake_exec(file, args):
        captured["file"] = file
        captured["args"] = args

    seat.reexec_into_seat(["--path", "/x"], exec_fn=fake_exec)
    assert captured["file"] == "tmux"
    assert captured["args"][:5] == ["tmux", "-L", seat.PRIVATE_SOCKET, "new-session", "-A"]
    assert captured["args"][-3:] == ["-i", "--path", "/x"]  # inner reinvocation


# ---------------------------------------------------------------------------
# launch integration


def test_run_console_seats_before_running_app(monkeypatch):
    pytest.importorskip("textual")

    class _Seated(Exception):
        pass

    monkeypatch.setattr("otaman_cli.console.seat.should_seat", lambda argv: True)
    # keep hermetic: don't touch real tmux for the pre-attach staleness check
    monkeypatch.setattr("otaman_cli.console.seat.offer_restart_if_stale", lambda **kw: False)

    def fake_reexec(argv):
        raise _Seated

    monkeypatch.setattr("otaman_cli.console.seat.reexec_into_seat", fake_reexec)
    with pytest.raises(_Seated):
        run_console([], _run=True)  # reaches the seat before app.run()


def test_install_hint_returns_before_seating(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "textual", None)  # extra absent
    seated = []
    monkeypatch.setattr("otaman_cli.console.seat.should_seat", lambda argv: True)
    monkeypatch.setattr("otaman_cli.console.seat.reexec_into_seat", lambda argv: seated.append(1))
    assert run_console([]) == 2
    assert seated == []  # the extra check short-circuits before any seating


# ---------------------------------------------------------------------------
# stale-seat restart offer (5.1 finding #3 follow-up UX)

import subprocess  # noqa: E402


def _cp(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class _FakeTmux:
    """A fake subprocess runner mapping `tmux -L … <sub>` to canned results."""

    def __init__(self, *, has_session=True, version_line=None, raise_on=()):
        self.calls: list[list[str]] = []
        self.has_session = has_session
        self.version_line = version_line  # stdout for show-environment
        self.raise_on = set(raise_on)

    def __call__(self, cmd, capture_output=False, text=False):
        sub = cmd[3]  # ["tmux","-L",socket,<sub>,...]
        self.calls.append(cmd)
        if sub in self.raise_on:
            raise OSError("tmux boom")
        if sub == "has-session":
            return _cp(0 if self.has_session else 1)
        if sub == "show-environment":
            return _cp(0, self.version_line) if self.version_line is not None else _cp(1)
        return _cp(0)  # set-environment / kill-session

    def subcommands(self) -> list[str]:
        return [c[3] for c in self.calls]


def test_session_exists_reflects_has_session_rc():
    assert seat.session_exists(run=_FakeTmux(has_session=True)) is True
    assert seat.session_exists(run=_FakeTmux(has_session=False)) is False


def test_session_exists_false_when_tmux_raises():
    assert seat.session_exists(run=_FakeTmux(raise_on={"has-session"})) is False


def test_running_seat_version_parses_and_handles_unset():
    assert seat.running_seat_version(
        run=_FakeTmux(version_line="OTAMAN_CONSOLE_VERSION=1.2.3")
    ) == ("1.2.3")
    # tmux prints `-NAME` for an unset var → None
    assert seat.running_seat_version(run=_FakeTmux(version_line="-OTAMAN_CONSOLE_VERSION")) is None
    # non-zero (no session env) → None
    assert seat.running_seat_version(run=_FakeTmux(version_line=None)) is None


def test_stamp_seat_version_sets_environment():
    fake = _FakeTmux()
    seat.stamp_seat_version("9.9.9", run=fake)
    assert [
        "tmux",
        "-L",
        seat.PRIVATE_SOCKET,
        "set-environment",
        "-t",
        seat.SESSION_NAME,
        seat.CONSOLE_VERSION_ENV,
        "9.9.9",
    ] in fake.calls


def test_stamp_seat_version_noop_without_version():
    fake = _FakeTmux()
    seat.stamp_seat_version("", run=fake)
    assert fake.calls == []


def test_offer_restart_no_session_is_noop():
    fake = _FakeTmux(has_session=False)
    prompted = []
    assert (
        seat.offer_restart_if_stale(
            run=fake, input_fn=lambda p: prompted.append(p) or "y", installed="2.0.0"
        )
        is False
    )
    assert prompted == []  # never prompted


def test_offer_restart_up_to_date_is_noop():
    fake = _FakeTmux(version_line="OTAMAN_CONSOLE_VERSION=2.0.0")
    prompted = []
    assert (
        seat.offer_restart_if_stale(
            run=fake, input_fn=lambda p: prompted.append(p) or "y", installed="2.0.0"
        )
        is False
    )
    assert prompted == []


def test_offer_restart_stale_and_confirmed_kills_seat():
    fake = _FakeTmux(version_line="OTAMAN_CONSOLE_VERSION=1.0.0")
    out: list[str] = []
    result = seat.offer_restart_if_stale(
        run=fake, input_fn=lambda _p: "y", out=out.append, installed="2.0.0"
    )
    assert result is True
    assert "kill-session" in fake.subcommands()
    assert any("older version (1.0.0)" in m and "2.0.0" in m for m in out)


def test_offer_restart_stale_but_declined_keeps_seat():
    fake = _FakeTmux(version_line="OTAMAN_CONSOLE_VERSION=1.0.0")
    result = seat.offer_restart_if_stale(
        run=fake, input_fn=lambda _p: "n", out=lambda _m: None, installed="2.0.0"
    )
    assert result is False
    assert "kill-session" not in fake.subcommands()  # seat preserved


def test_offer_restart_unknown_running_version_is_noop():
    # session exists but no stamped version (older seat) → can't tell → attach
    fake = _FakeTmux(version_line=None)
    result = seat.offer_restart_if_stale(
        run=fake, input_fn=lambda _p: "y", out=lambda _m: None, installed="2.0.0"
    )
    assert result is False
    assert "kill-session" not in fake.subcommands()


def test_offer_restart_eof_on_prompt_keeps_seat():
    fake = _FakeTmux(version_line="OTAMAN_CONSOLE_VERSION=1.0.0")

    def _eof(_p):
        raise EOFError

    result = seat.offer_restart_if_stale(
        run=fake, input_fn=_eof, out=lambda _m: None, installed="2.0.0"
    )
    assert result is False
    assert "kill-session" not in fake.subcommands()
