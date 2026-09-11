"""spec-gate-hardening 1.3 — append-only gate-waiver audit trail + status annotation.

A waived dispatch leaves a durable `.agents/audit/gate-waivers.jsonl` entry per
violation; `otaman spec status` annotates the affected change with its waived
events.
"""

from __future__ import annotations

import json

import pytest

from otaman_cli import gate_audit
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


def _change(root, name, *, stage=None, tasks=False):
    d = root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    if stage:
        import yaml

        (d / ".openspec.yaml").write_text(yaml.safe_dump({"stage": stage}), encoding="utf-8")
    if tasks:
        # an unticked task makes the change an in-flight row in `spec status`
        (d / "tasks.md").write_text("- [ ] 1.1 @otaman-cli do it\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# the audit module


def test_append_and_read_roundtrip(tmp_path):
    gate_audit.append_waivers(
        tmp_path, change="ch1", action="dispatch",
        violations=["not spec-approved"], actor="cli-agent", at="2026-09-11T12:00:00Z",
    )  # fmt: skip
    rows = gate_audit.read_waivers(tmp_path)
    assert len(rows) == 1
    assert rows[0]["change"] == "ch1" and rows[0]["action"] == "dispatch"
    assert rows[0]["violation"] == "not spec-approved" and rows[0]["actor"] == "cli-agent"


def test_append_is_append_only(tmp_path):
    gate_audit.append_waivers(
        tmp_path, change="a", action="dispatch", violations=["v1"], actor="x", at="t1"
    )
    gate_audit.append_waivers(
        tmp_path, change="b", action="archive", violations=["v2", "v3"], actor="y", at="t2"
    )
    rows = gate_audit.read_waivers(tmp_path)
    assert len(rows) == 3  # 1 + 2, nothing overwritten
    path = tmp_path / ".agents" / "audit" / "gate-waivers.jsonl"
    # each line is valid standalone JSON
    for line in path.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_annotate_counts_by_action(tmp_path):
    for _ in range(2):
        gate_audit.append_waivers(
            tmp_path, change="ch", action="dispatch", violations=["v"], actor="x", at="t"
        )
    assert gate_audit.annotate(tmp_path, "ch") == " (2 waived dispatch)"
    assert gate_audit.annotate(tmp_path, "other") == ""


# ---------------------------------------------------------------------------
# wired into the dispatch gate + spec status


def test_waived_dispatch_appends_audit_entry(root):
    _platform(root)  # default warn
    _change(root, "wip", stage="authored")  # not spec-approved → dispatch violation
    allowed, _lines = spec_cmd.dispatch_gate_check(root, "wip", audit_actor="cli-agent")
    assert allowed is True  # warn → waived
    rows = gate_audit.read_waivers(root)
    assert len(rows) == 1 and rows[0]["change"] == "wip" and rows[0]["action"] == "dispatch"


def test_pure_check_does_not_log(root):
    _platform(root)
    _change(root, "wip", stage="authored")
    spec_cmd.dispatch_gate_check(root, "wip")  # no audit_actor → no log
    assert gate_audit.read_waivers(root) == []


def test_blocked_dispatch_does_not_log(root):
    _platform(root, enforcement="block")
    _change(root, "wip", stage="authored")
    allowed, _ = spec_cmd.dispatch_gate_check(root, "wip", audit_actor="cli-agent")
    assert allowed is False
    assert gate_audit.read_waivers(root) == []  # blocked ≠ waived


def test_spec_status_annotates_waived_change(root, capsys):
    _platform(root)
    _change(root, "wip", stage="authored", tasks=True)  # tasks.md → an in-flight row
    spec_cmd.dispatch_gate_check(root, "wip", audit_actor="cli-agent")
    capsys.readouterr()  # clear
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "waived dispatch" in out


def test_assign_sets_gate_waived_env_on_waived_dispatch(root, monkeypatch):
    # 1.3c: a warn-mode (waived) dispatch hands map-tasks OTAMAN_GATE_WAIVED so it
    # can stamp x-gate-waived; the env is scoped to the call and restored after.
    import os
    from types import SimpleNamespace

    from otaman_cli.commands import bus_messaging

    _platform(root)  # warn → waived
    _change(root, "wip", stage="authored", tasks=True)
    monkeypatch.setattr(bus_messaging, "find_project_root", lambda: root)
    monkeypatch.delenv("OTAMAN_GATE_WAIVED", raising=False)

    captured = {}

    def fake_run_script(name, *a, **k):
        captured["env"] = os.environ.get("OTAMAN_GATE_WAIVED")
        return SimpleNamespace(returncode=0, stdout="{}", stderr=None)

    monkeypatch.setattr(bus_messaging, "run_script", fake_run_script)
    bus_messaging.cmd_assign(["openspec/changes/wip"])
    assert captured["env"] == "not-spec-approved"
    assert os.environ.get("OTAMAN_GATE_WAIVED") is None  # restored after the call
