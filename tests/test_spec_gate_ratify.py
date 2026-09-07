"""SLE 2.2 — `otaman spec gate` (local CI-less gates), `otaman ratify`, dispatch wiring.

The three gates run as LOCAL checks (D2): block refuses, warn/self-waive proceed
with a VISIBLE notice. `otaman ratify` is human-only (HUMAN-DECISION tier, mandatory
reason) and stamps the ratified marker + ratified_at. `otaman assign` refuses a
dispatch that a block-mode policy rejects, before any bus message is written.
"""

from __future__ import annotations

import json

import pytest

from otaman_cli.commands import spec as spec_cmd


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "prog"
    (r / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (r / "specs" / "openspec" / "changes").mkdir(parents=True)
    monkeypatch.setattr(spec_cmd, "find_project_root", lambda: r)
    return r


def _platform(root, enforcement=None):
    block = "project: demo\nspecs:\n  path: specs\n"
    if enforcement:
        block += f"spec_policy:\n  enforcement: {enforcement}\n"
    (root / "platform.yaml").write_text(block, encoding="utf-8")


def _change(root, name, *, stage=None, approved_by=None):
    d = root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    oy = {}
    if stage:
        oy["stage"] = stage
    if approved_by:
        oy["approved_by"] = approved_by
    if oy:
        import yaml

        (d / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# spec gate — modes


def test_gate_dispatch_warn_allows_with_notice(root, capsys):
    _platform(root)  # default = warn
    _change(root, "wip", stage="authored")
    rc = spec_cmd.cmd_spec(["gate", "wip", "--at", "dispatch"])
    out = capsys.readouterr().out
    assert rc == 0 and "ALLOWED" in out and "proceeding despite" in out


def test_gate_dispatch_block_refuses(root, capsys):
    _platform(root, enforcement="block")
    _change(root, "wip", stage="authored")  # not spec-approved
    rc = spec_cmd.cmd_spec(["gate", "wip", "--at", "dispatch"])
    out = capsys.readouterr().out
    assert rc == 1 and "BLOCKED" in out and "not spec-approved" in out


def test_gate_dispatch_block_allows_spec_approved(root, capsys):
    _platform(root, enforcement="block")
    _change(root, "ready", stage="spec-approved")
    rc = spec_cmd.cmd_spec(["gate", "ready", "--at", "dispatch"])
    assert rc == 0


def test_gate_self_waive_prints_visible_notice(root, capsys):
    _platform(root, enforcement="self-waive")
    _change(root, "poc", stage="authored")
    rc = spec_cmd.cmd_spec(["gate", "poc", "--at", "dispatch"])
    out = capsys.readouterr().out
    assert rc == 0 and "self-waive:" in out  # D2: self-waive MUST be visible


def test_gate_archive_block_with_delta_refuses(root, capsys):
    _platform(root, enforcement="block")
    _change(root, "unappr", stage="authored")
    rc = spec_cmd.cmd_spec(["gate", "unappr", "--at", "archive"])
    assert rc == 1


def test_gate_json_structure(root, capsys):
    _platform(root, enforcement="block")
    _change(root, "wip", stage="authored")
    rc = spec_cmd.cmd_spec(["gate", "wip", "--at", "merge", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["allowed"] is False and data["mode"] == "block"
    assert data["gate"] == "merge" and data["violations"]


def test_gate_unknown_change_errors(root, capsys):
    _platform(root)
    assert spec_cmd.cmd_spec(["gate", "nope"]) == 1


def test_gate_bad_at_rejected(root, capsys):
    _platform(root)
    assert spec_cmd.cmd_spec(["gate", "x", "--at", "bogus"]) == 2


# ---------------------------------------------------------------------------
# dispatch_gate_check — the assign helper


def test_dispatch_check_absent_change_never_blocks(root):
    _platform(root, enforcement="block")
    assert spec_cmd.dispatch_gate_check(root, "does-not-exist") == (True, [])


def test_dispatch_check_legacy_change_never_blocks(root):
    _platform(root, enforcement="block")
    _change(root, "legacy")  # no .openspec.yaml
    assert spec_cmd.dispatch_gate_check(root, "legacy") == (True, [])


def test_dispatch_check_blocks_under_block_policy(root):
    _platform(root, enforcement="block")
    _change(root, "wip", stage="authored")
    allowed, lines = spec_cmd.dispatch_gate_check(root, "wip")
    assert allowed is False and any("not spec-approved" in ln for ln in lines)


# ---------------------------------------------------------------------------
# ratify — human-only, mandatory reason, HUMAN-DECISION


@pytest.fixture
def _confirm_yes(monkeypatch):
    from otaman_cli import safety

    monkeypatch.setattr(safety, "confirm_human_decision", lambda *a, **k: True)


def test_ratify_writes_marker(root, monkeypatch, _confirm_yes):
    _platform(root)
    d = _change(root, "keystone", stage="authored")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    rc = spec_cmd.cmd_ratify(["keystone", "--reason", "founder-mode unblock"])
    assert rc == 0
    import yaml

    data = yaml.safe_load((d / ".openspec.yaml").read_text())
    assert data["stage"] == "approved" and data["ratified"] is True
    assert "roman" in data["approved_by"] and data["ratified_at"]


def test_ratify_requires_reason(root, monkeypatch, _confirm_yes):
    _platform(root)
    _change(root, "c", stage="authored")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert spec_cmd.cmd_ratify(["c"]) == 1


def test_ratify_requires_human_identity(root, monkeypatch, _confirm_yes):
    _platform(root)
    _change(root, "c", stage="authored")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    assert spec_cmd.cmd_ratify(["c", "--reason", "x"]) == 2


def test_ratify_aborts_without_human_confirm(root, monkeypatch):
    from otaman_cli import safety

    _platform(root)
    d = _change(root, "c", stage="authored")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.setattr(safety, "confirm_human_decision", lambda *a, **k: False)
    rc = spec_cmd.cmd_ratify(["c", "--reason", "x"])
    assert rc == 2
    import yaml

    assert yaml.safe_load((d / ".openspec.yaml").read_text()).get("ratified") is None


def test_ratify_unknown_change(root, monkeypatch, _confirm_yes):
    _platform(root)
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    assert spec_cmd.cmd_ratify(["ghost", "--reason", "x"]) == 1


# ---------------------------------------------------------------------------
# assign dispatch-gate wiring


def test_assign_refuses_blocked_dispatch_before_writing(root, monkeypatch, capsys):
    from otaman_cli.commands import bus_messaging

    _platform(root, enforcement="block")
    _change(root, "wip", stage="authored")
    monkeypatch.setattr(bus_messaging, "find_project_root", lambda: root)
    called = {"ran": False}

    def _no_run(*a, **k):
        called["ran"] = True
        raise AssertionError("run_script must not run when dispatch is blocked")

    monkeypatch.setattr(bus_messaging, "run_script", _no_run)
    rc = bus_messaging.cmd_assign(["openspec/changes/wip"])
    out = capsys.readouterr().out
    assert rc == 2 and "blocked by spec policy" in out.lower()
    assert called["ran"] is False


def test_change_name_from_target_variants():
    from otaman_cli.commands.bus_messaging import _change_name_from_target

    assert _change_name_from_target("openspec/changes/my-feature") == "my-feature"
    assert _change_name_from_target("a/openspec/changes/my-feature/tasks.md") == "my-feature"
    assert _change_name_from_target("my-feature") == "my-feature"
    assert _change_name_from_target("") is None
