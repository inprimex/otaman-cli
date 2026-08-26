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
