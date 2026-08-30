"""single-acting-session-guard 2.1 + 2.2 — bus-write holder-ship guard.

The guard fails open (allows) whenever it can't adjudicate, allows the acting
session (holder pid in our ancestry) and a free lock, and refuses a passive
mirror (a live *other* holder) — naming the holder + attach — or a preempted
holder with "preempted". POSIX-only (flock).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from otaman_cli import acting_guard

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


def test_target_resolves_to_identity_uri(acting):
    assert acting_guard.resolve_target() == _URI


def test_allows_when_target_unresolvable(tmp_path, monkeypatch):
    # nowhere near an org layout → resolve_target None → fail-open allow
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    assert acting_guard.resolve_target() is None
    assert acting_guard.enforce("send") is None


def test_allows_when_lock_free(acting):
    # no holder → acquirable → allow
    assert acting_guard.enforce("send") is None


def test_allows_when_holder_is_self(acting):
    # we hold the lock ourselves → our pid is trivially in our own ancestry
    from otaman_core.acting_lock import acquire

    with acquire(_URI, mode="interactive", pid=os.getpid()):
        assert acting_guard.enforce("ack") is None


def test_refuses_passive_mirror_names_holder(acting, capsys, monkeypatch):
    # a live OTHER session holds it; our ancestry does NOT include its pid
    from otaman_core.acting_lock import acquire

    monkeypatch.setattr(acting_guard, "_pid_in_ancestry", lambda pid: False)
    with acquire(_URI, mode="background", tmux_session="sess-bg", pid=4242):
        rc = acting_guard.enforce("complete")
    out = capsys.readouterr().out
    assert rc == acting_guard.REFUSED
    assert "passive mirror" in out
    assert "pid 4242" in out and "tmux attach-session -t sess-bg" in out


def test_passive_mirror_without_tmux_still_refuses(acting, capsys, monkeypatch):
    from otaman_core.acting_lock import acquire

    monkeypatch.setattr(acting_guard, "_pid_in_ancestry", lambda pid: False)
    with acquire(_URI, mode="background", pid=4243):
        rc = acting_guard.enforce("set-status")
    out = capsys.readouterr().out
    assert rc == acting_guard.REFUSED
    assert "no tmux session was recorded" in out


def test_preempted_holder_refuses_with_preempted(acting, capsys):
    # we are the holder, but a preempt marker is pending → refuse "preempted" (2.2)
    from otaman_core.acting_lock import acquire, write_preempt_marker

    with acquire(_URI, mode="background", pid=os.getpid()):
        write_preempt_marker(_URI, pid=9001, mode="interactive")
        rc = acting_guard.enforce("send")
    out = capsys.readouterr().out
    assert rc == acting_guard.REFUSED
    assert "preempted" in out and "9001" in out


def test_ancestry_walk_finds_self(acting):
    assert acting_guard._pid_in_ancestry(os.getpid()) is True


def test_ancestry_walk_finds_parent(acting):
    assert acting_guard._pid_in_ancestry(os.getppid()) is True


def test_ancestry_walk_rejects_stranger(acting):
    # pid 2 (kthreadd on Linux) is never an ancestor of a userspace test process
    assert acting_guard._pid_in_ancestry(2) is False
