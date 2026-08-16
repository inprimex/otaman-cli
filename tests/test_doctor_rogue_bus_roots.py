"""bus-test-isolation task 2.2 (rogue-root scan half) — doctor check.

Detects the retired org-level ``.agents`` bus root: warn on existence,
fail on fresh message writes, critical on privileged-type files there
(the 2026-08-16 fake-emergency-halt incident class).
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.doctor import check_rogue_bus_roots


def _mk_layout(tmp_path: Path) -> tuple[Path, Path]:
    """orgs/acme/programs/alpha/alpha-otaman + return (program_root, org_root)."""
    org = tmp_path / "orgs" / "acme"
    meta = org / "programs" / "alpha" / "alpha-otaman"
    (meta / ".agents" / "bus" / "active").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: alpha\nrepos: []\n", encoding="utf-8")
    return meta, org


def _rogue_msg(org: Path, stem: str, msg_type: str) -> Path:
    active = org / ".agents" / "bus" / "active"
    active.mkdir(parents=True, exist_ok=True)
    f = active / f"{stem}.md"
    f.write_text(
        f"---\nid: {stem}\nfrom: human\nto: all\npriority: urgent\n"
        f"type: {msg_type}\ntimestamp: 2026-08-16T18:48:15Z\nstatus: pending\n---\n\n"
        "## Subject: x\n",
        encoding="utf-8",
    )
    return f


def test_ok_when_no_org_level_agents(tmp_path: Path):
    meta, _org = _mk_layout(tmp_path)
    result = check_rogue_bus_roots(meta)
    assert result["status"] == "ok"
    assert result["details"]["scanned"] is True
    assert result["details"]["exists"] is False


def test_skips_gracefully_outside_declared_layout(tmp_path: Path):
    loose = tmp_path / "somewhere" / "meta"
    loose.mkdir(parents=True)
    result = check_rogue_bus_roots(loose)
    assert result["status"] == "ok"
    assert result["details"]["scanned"] is False


def test_existence_alone_warns(tmp_path: Path):
    meta, org = _mk_layout(tmp_path)
    (org / ".agents").mkdir()
    result = check_rogue_bus_roots(meta)
    assert result["status"] == "warn"
    assert any("retired org-level bus root exists" in i["issue"] for i in result["issues"])
    assert result["details"]["fresh_message_files"] == 0


def test_fresh_writes_fail_high(tmp_path: Path):
    meta, org = _mk_layout(tmp_path)
    _rogue_msg(org, "20260816T190000-cli-agent-to-spec-agent-hello", "info")
    result = check_rogue_bus_roots(meta)
    assert result["status"] == "fail"
    highs = [i for i in result["issues"] if i["severity"] == "high"]
    assert highs and "still writing there" in highs[0]["issue"]
    assert result["details"]["fresh_message_files"] == 1


def test_privileged_type_in_rogue_bus_is_critical_with_quarantine(tmp_path: Path):
    meta, org = _mk_layout(tmp_path)
    _rogue_msg(org, "20260816T184815-human-to-all-emergency-halt", "emergency-halt")
    result = check_rogue_bus_roots(meta)
    assert result["status"] == "fail"
    crit = [i for i in result["issues"] if i["severity"] == "critical"]
    assert crit
    assert "PRIVILEGED" in crit[0]["issue"]
    assert "uarantine" in crit[0]["fix"]
    assert result["details"]["privileged_files"] == [
        "20260816T184815-human-to-all-emergency-halt.md"
    ]


def test_unparseable_rogue_file_still_counts_as_fresh(tmp_path: Path):
    meta, org = _mk_layout(tmp_path)
    active = org / ".agents" / "bus" / "active"
    active.mkdir(parents=True)
    (active / "garbage.md").write_bytes(b"\xff\xfe not text")
    result = check_rogue_bus_roots(meta)
    assert result["status"] == "fail"
    assert result["details"]["fresh_message_files"] == 1
    assert result["details"]["privileged_files"] == []
