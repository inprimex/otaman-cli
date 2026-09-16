"""blocked-entry-lifecycle 1.2, cli half — `otaman approve` fires the terminator.

The terminator already existed: plugin's `auto_tombstone_blocked`, matching each
entry's `**Proposal**:` ref. But it hung off the MCP `otaman_send` path ONLY,
while a human approves with `otaman approve`, which writes its broadcast
directly. So approvals landed and entries never cleared — deploy-agent's file
reached 16, of which 5/5 sampled were already archived.

plugin owns the matcher and tombstone format (split (a)); cli is a second caller.
"""

from __future__ import annotations

import sys
import types

import pytest

from otaman_cli.commands.approve import _terminate_blocked_entries


@pytest.fixture
def fake_plugin(monkeypatch):
    """Install an importable stub for plugin's bus_server.

    Needed because the real module imports fastmcp at module level, which is not
    present in every environment — the exact reason the call site is guarded.
    """
    calls: list[dict] = []

    def auto_tombstone_blocked(root, msg_type, body, change_name=None):
        calls.append({"root": root, "msg_type": msg_type, "body": body, "change_name": change_name})
        return [{"agent": "deploy-agent", "title": "a blocked thing", "reason": "approved"}]

    pkg = types.ModuleType("otaman_plugin")
    servers = types.ModuleType("otaman_plugin.servers")
    bus_server = types.ModuleType("otaman_plugin.servers.bus_server")
    bus_server.auto_tombstone_blocked = auto_tombstone_blocked
    monkeypatch.setitem(sys.modules, "otaman_plugin", pkg)
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers", servers)
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers.bus_server", bus_server)
    return calls


def test_approval_calls_the_terminator_with_the_broadcast(tmp_path, fake_plugin, capsys):
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "body with a stem")
    capsys.readouterr()
    assert len(fake_plugin) == 1
    call = fake_plugin[0]
    assert call["msg_type"] == "spec-change-approved"
    assert call["body"] == "body with a stem"  # the stem is recovered from the body
    assert call["root"] == tmp_path


def test_rejection_passes_its_own_type(tmp_path, fake_plugin, capsys):
    _terminate_blocked_entries(tmp_path, "spec-change-rejected", "reason body")
    capsys.readouterr()
    assert fake_plugin[0]["msg_type"] == "spec-change-rejected"


def test_cleared_entries_are_reported_by_agent_and_title(tmp_path, fake_plugin, capsys):
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "b")
    out = capsys.readouterr().out
    assert "deploy-agent" in out and "a blocked thing" in out


def test_nothing_matched_prints_nothing(tmp_path, monkeypatch, capsys):
    bus_server = types.ModuleType("otaman_plugin.servers.bus_server")
    bus_server.auto_tombstone_blocked = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "otaman_plugin", types.ModuleType("otaman_plugin"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers.bus_server", bus_server)
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "b")
    assert capsys.readouterr().out.strip() == ""


# ---------------------------------------------------------------------------
# the guard — a decision must never fail because the terminator can't run


def test_missing_plugin_does_not_raise(tmp_path, monkeypatch, capsys):
    """`bus_server` imports fastmcp at module level, so on an install without it
    this import raises. Writing the decision is approve's job; a missed tombstone
    is recoverable (`otaman blocked migrate`), a refused approval is not."""
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if "bus_server" in name:
            raise ModuleNotFoundError("No module named 'fastmcp'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "b")  # must not raise
    out = capsys.readouterr().out
    assert "terminator unavailable" in out
    assert "ModuleNotFoundError" in out  # names WHY, so it's diagnosable


def test_a_terminator_that_raises_is_contained(tmp_path, monkeypatch, capsys):
    bus_server = types.ModuleType("otaman_plugin.servers.bus_server")

    def explode(*a, **k):
        raise RuntimeError("bad blocked file")

    bus_server.auto_tombstone_blocked = explode
    monkeypatch.setitem(sys.modules, "otaman_plugin", types.ModuleType("otaman_plugin"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers.bus_server", bus_server)
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "b")  # must not raise
    assert "RuntimeError" in capsys.readouterr().out


def test_none_return_is_tolerated(tmp_path, monkeypatch, capsys):
    bus_server = types.ModuleType("otaman_plugin.servers.bus_server")
    bus_server.auto_tombstone_blocked = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "otaman_plugin", types.ModuleType("otaman_plugin"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "otaman_plugin.servers.bus_server", bus_server)
    _terminate_blocked_entries(tmp_path, "spec-change-approved", "b")
    assert capsys.readouterr().out.strip() == ""


# ---------------------------------------------------------------------------
# the call sites exist on BOTH decision paths


def test_both_decision_paths_call_the_terminator():
    """Approve and reject each write their message directly, so each needs the
    call — the bug was one write path having a hook and the other not."""
    import inspect

    from otaman_cli.commands import approve as mod

    src = inspect.getsource(mod)
    assert src.count("_terminate_blocked_entries(") >= 3  # def + 2 call sites
    assert '_terminate_blocked_entries(root, "spec-change-approved", broadcast)' in src
    assert '_terminate_blocked_entries(root, "spec-change-rejected", reject_msg)' in src
