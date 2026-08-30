"""single-acting-session-guard 2.1 — `otaman acting-lock run|probe` verbs.

The cli wrapper around otaman_core.acting_lock: probe reports the live holder;
run holds the lock while a child command runs (parent-holds, so a descendant's
process ancestry includes the holder pid — what the 2.2 bus-write guard checks).
POSIX-only (flock).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from otaman_cli.commands import acting_lock as al
from otaman_cli.commands.acting_lock import cmd_acting_lock

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")

_URI = "otaman://acme/shop/cli-agent"


@pytest.fixture
def acting(tmp_path: Path, monkeypatch):
    """CE-layout program `shop`, agent cli-agent, lock isolated under tmp XDG."""
    meta = tmp_path / "orgs" / "acme" / "programs" / "shop" / "shop-meta"
    (meta / ".agents").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    monkeypatch.chdir(meta)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))
    monkeypatch.delenv("TMUX", raising=False)
    return tmp_path


def test_probe_free_exits_nonzero(acting, capsys):
    rc = cmd_acting_lock(["probe"])
    assert rc == 1  # free
    assert "free" in capsys.readouterr().out


def test_probe_held_reports_holder(acting, capsys):
    from otaman_core.acting_lock import acquire

    with acquire(_URI, mode="background"):
        rc = cmd_acting_lock(["probe", "--json"])
    import json

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["held"] is True
    assert payload["holder"]["mode"] == "background"


def test_run_executes_and_releases(acting):
    # run a child that succeeds; lock is free again afterwards
    rc = cmd_acting_lock(
        ["run", "--mode", "background", "--", sys.executable, "-c", "import sys; sys.exit(7)"]
    )
    assert rc == 7  # child's exit code is propagated
    from otaman_core.acting_lock import probe

    assert probe(_URI) is None  # released


def test_run_refuses_when_held_names_holder(acting, capsys):
    from otaman_core.acting_lock import acquire

    with acquire(_URI, mode="background", tmux_session="sess-x"):
        rc = cmd_acting_lock(["run", "--mode", "background", "--", "true"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "held by pid" in out and "tmux attach-session -t sess-x" in out


def test_run_preempt_gives_up_naming_holder(acting, capsys, monkeypatch):
    # a still-held background lock; interactive --preempt writes the marker,
    # waits the (shortened) window, then refuses naming the wedged holder pid.
    monkeypatch.setattr(al, "_PREEMPT_WINDOW_S", 0.4)
    from otaman_core.acting_lock import acquire, read_preempt_marker

    with acquire(_URI, mode="background", pid=4242):
        rc = cmd_acting_lock(["run", "--mode", "interactive", "--preempt", "--", "true"])
        # the preempt marker was written during the attempt
    out = capsys.readouterr().out
    assert rc == 2
    assert "did not release" in out and "4242" in out
    assert read_preempt_marker(_URI) is None  # marker cleared on give-up


def test_run_requires_double_dash(acting):
    assert cmd_acting_lock(["run", "--mode", "background", "true"]) != 0


def test_run_bad_mode(acting):
    assert cmd_acting_lock(["run", "--mode", "nope", "--", "true"]) != 0


def test_unknown_action(acting):
    assert cmd_acting_lock(["frobnicate"]) != 0
