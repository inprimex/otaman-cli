"""SLE 2.1 — `otaman spec status` (the truthful spec-lifecycle surface, D3).

Renders the effective policy, the month's ratification count, and per-change
stage/state/age/severity/next-actor, archive-aware and shared with the console
derivation. Exit 1 when any change is ERROR-stalled (script/CI signal).
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
    (r / "platform.yaml").write_text("project: demo\nspecs:\n  path: specs\n", encoding="utf-8")
    monkeypatch.setattr(spec_cmd, "find_project_root", lambda: r)
    monkeypatch.setattr(
        "otaman_cli.main._resolve_bus_paths",
        lambda root: (
            root / ".agents" / "bus" / "active",
            root / ".agents" / "bus" / "active" / "acks",
        ),
    )
    return r


def _approved(root, title, *, ts):
    stem = f"{ts.replace('-', '').replace(':', '')}-human-to-all-spec-change-approved"
    (root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nfrom: human\nto: all\ntype: spec-change-approved\ntimestamp: {ts}\n"
        f"status: pending\n---\n\n## Subject: Approved: {title}\n\nApproved.\n",
        encoding="utf-8",
    )


def _change(root, name, *, ticks, stage=None, ratified_at=None):
    folder = root / "specs" / "openspec" / "changes" / name
    folder.mkdir(parents=True)
    body = "## Tasks\n\n" + "\n".join(
        f"- [{'x' if t else ' '}] t{i} @otaman-cli" for i, t in enumerate(ticks)
    )
    (folder / "tasks.md").write_text(body + "\n", encoding="utf-8")
    oy = {}
    if stage:
        oy["stage"] = stage
    if ratified_at:
        oy["ratified"] = True
        oy["ratified_at"] = ratified_at
    if oy:
        import yaml

        (folder / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    return folder


def test_status_empty_is_all_clear(root, capsys):
    rc = spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Spec lifecycle" in out and "all clear" in out
    assert "level=spec-first" in out and "enforcement=warn" in out  # L1 defaults


def test_status_flags_stale_approval_error_and_exits_1(root, capsys):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _approved(root, "stuck change", ts=old)
    rc = spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert rc == 1  # ERROR-stalled → non-zero
    assert "stuck change" in out and "ERROR" in out and "approved-unauthored" in out


def test_status_renders_stage_for_in_flight(root, capsys):
    _change(root, "wip", ticks=[False], stage="authored")
    rc = spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert rc == 0  # in-flight is not an alarm
    assert "wip" in out and "stage=authored" in out and "in-flight" in out


def test_status_counts_ratifications_this_month(root, capsys):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    this_month = now.strftime("%Y-%m-15T00:00:00Z")
    _change(root, "ratified-one", ticks=[True], stage="approved", ratified_at=this_month)
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "ratifications this month: 1" in out


def test_status_json_structure(root, capsys):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _approved(root, "j-change", ts=old)
    rc = spec_cmd.cmd_spec(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["policy"]["process_level"] == "spec-first"
    assert data["ratifications_this_month"] == 0
    (c,) = data["changes"]
    assert c["state"] == "approved-unauthored" and c["severity"] == "error"


def test_spec_help_and_unknown_action(root, capsys):
    assert spec_cmd.cmd_spec(["--help"]) == 0
    assert spec_cmd.cmd_spec(["bogus"]) == 2
    assert spec_cmd.cmd_spec([]) == 1
