"""bus-test-isolation task 2.2 (provenance-audit half) — doctor check.

Post-cutoff privileged bus files must verify against the confirmation
ledger (either key: stem or frontmatter id; byte-exact hash, mirroring
bridge 3.1). Unverified -> critical + quarantine guidance; pre-ledger
history is grandfathered; bridge's quarantine dir is surfaced.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core.confirmations import append_confirmation, hash_message

from otaman_cli.doctor import check_privileged_provenance


def _project(tmp_path: Path) -> Path:
    meta = tmp_path / "meta"
    (meta / ".agents" / "bus" / "active").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: t\nrepos: []\n", encoding="utf-8")
    return meta


def _privileged(meta: Path, stem: str, msg_type: str = "emergency-halt", msg_id: str = "") -> str:
    content = (
        f"---\nid: {msg_id or stem}\nfrom: human\nto: all\npriority: urgent\n"
        f"type: {msg_type}\ntimestamp: 2026-08-17T09:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: x\n"
    )
    (meta / ".agents" / "bus" / "active" / f"{stem}.md").write_text(content, encoding="utf-8")
    return content


def test_ok_when_no_privileged_files(tmp_path: Path):
    meta = _project(tmp_path)
    (meta / ".agents" / "bus" / "active" / "20260817T090000-a-to-b-info.md").write_text(
        "---\nid: x\nfrom: a\nto: b\ntype: info\nstatus: pending\n---\n\n## Subject: s\n",
        encoding="utf-8",
    )
    result = check_privileged_provenance(meta)
    assert result["status"] == "ok"
    assert result["details"]["privileged_checked"] == 0


def test_ledgered_file_verifies_by_frontmatter_id(tmp_path: Path, _isolated_ledger):
    meta = _project(tmp_path)
    stem = "20260817T090000-human-to-all-emergency-halt"
    content = _privileged(meta, stem, msg_id="20260817T090000-emergency-halt-drill")
    append_confirmation(
        message_id="20260817T090000-emergency-halt-drill",
        content_hash=hash_message(content),
        command="emergency-halt",
        agent="human",
        path=_isolated_ledger,
    )
    result = check_privileged_provenance(meta)
    assert result["status"] == "ok"
    assert result["details"]["unverified"] == []


def test_ledgered_file_verifies_by_stem(tmp_path: Path, _isolated_ledger):
    meta = _project(tmp_path)
    stem = "20260817T091500-human-to-all-emergency-halt"
    content = _privileged(meta, stem, msg_id=stem)
    append_confirmation(
        message_id=stem,
        content_hash=hash_message(content),
        command="emergency-halt",
        agent="human",
        path=_isolated_ledger,
    )
    assert check_privileged_provenance(meta)["status"] == "ok"


def test_unledgered_post_cutoff_file_is_critical(tmp_path: Path):
    meta = _project(tmp_path)
    stem = "20260817T090000-human-to-all-emergency-halt"
    _privileged(meta, stem)
    result = check_privileged_provenance(meta)
    assert result["status"] == "fail"
    crit = [i for i in result["issues"] if i["severity"] == "critical"]
    assert crit and "NO confirmation-ledger record" in crit[0]["issue"]
    assert "uarantine" in crit[0]["fix"]
    assert result["details"]["unverified"] == [f"{stem}.md"]


def test_tampered_content_fails_even_with_record(tmp_path: Path, _isolated_ledger):
    meta = _project(tmp_path)
    stem = "20260817T090000-human-to-all-emergency-halt"
    content = _privileged(meta, stem)
    append_confirmation(
        message_id=stem,
        content_hash=hash_message(content),
        command="emergency-halt",
        agent="human",
        path=_isolated_ledger,
    )
    # Post-hoc tamper: the hash no longer matches the record
    f = meta / ".agents" / "bus" / "active" / f"{stem}.md"
    f.write_text(content.replace("## Subject: x", "## Subject: tampered"), encoding="utf-8")
    assert check_privileged_provenance(meta)["status"] == "fail"


def test_pre_cutoff_history_is_grandfathered(tmp_path: Path):
    meta = _project(tmp_path)
    _privileged(meta, "20260714T120000-human-to-all-spec-change-approved", "spec-change-approved")
    result = check_privileged_provenance(meta)
    assert result["status"] == "ok"
    assert result["details"]["grandfathered_unverified"] == 1


def test_quarantine_dir_surfaced_as_warning(tmp_path: Path):
    meta = _project(tmp_path)
    q = meta / ".agents" / "bus" / "quarantine"
    q.mkdir(parents=True)
    (q / "20260817T080000-human-to-all-emergency-halt.md").write_text("x", encoding="utf-8")
    result = check_privileged_provenance(meta)
    assert result["status"] == "warn"
    assert result["details"]["quarantined"] == ["20260817T080000-human-to-all-emergency-halt.md"]
