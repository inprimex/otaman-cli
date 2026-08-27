"""console-roster-verification 1.2 — two-roster drift (enrolled-but-unverifiable).

The console verifies against platform.yaml ``human-roster`` (name/email); the
enrollment tooling maintains ``/etc/otaman/human-roster.yaml`` (roster_id →
fingerprint). If an enrolled roster_id has no platform.yaml entry the human is
enrolled-but-unverifiable — Roman's exact failure. ``otaman doctor`` WARNs.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands import doctor
from otaman_cli.console import identity


def _program(tmp_path: Path, roster_names: list[str]) -> Path:
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True)
    body = "project: p\nversion: '1.0'\nrepos: []\n"
    if roster_names:
        rows = "".join(
            f"  - name: {n}\n    email: {n}@example.com\n    roles: [founder]\n"
            for n in roster_names
        )
        body += "human-roster:\n" + rows
    (root / "platform.yaml").write_text(body, encoding="utf-8")
    return root


def _tenant(tmp_path: Path, roster_ids: list[str]) -> Path:
    p = tmp_path / "tenant-human-roster.yaml"
    if roster_ids:
        rows = "".join(f"  - roster_id: {i}\n    fingerprint: SHA256:fp-{i}\n" for i in roster_ids)
        p.write_text("humans:\n" + rows, encoding="utf-8")
    else:
        p.write_text("humans: []\n", encoding="utf-8")
    return p


# --- roster_drift (identity) --------------------------------------------------


def test_no_drift_when_enrolled_id_in_platform_roster(tmp_path):
    root = _program(tmp_path, ["roman"])
    tp = _tenant(tmp_path, ["roman"])
    assert identity.roster_drift(root, tenant_path=tp) == []


def test_drift_when_enrolled_id_absent_from_platform(tmp_path):
    root = _program(tmp_path, ["roman"])
    tp = _tenant(tmp_path, ["roman", "ghost"])
    drift = identity.roster_drift(root, tenant_path=tp)
    assert [d["roster_id"] for d in drift] == ["ghost"]
    assert drift[0]["fingerprint"] == "SHA256:fp-ghost"  # fingerprints aren't secrets


def test_empty_platform_roster_flags_all_enrolled(tmp_path):
    # Roman's exact failure: enrolled, but the platform.yaml roster is empty.
    root = _program(tmp_path, [])
    tp = _tenant(tmp_path, ["roman"])
    assert [d["roster_id"] for d in identity.roster_drift(root, tenant_path=tp)] == ["roman"]


def test_no_tenant_roster_is_no_drift(tmp_path):
    root = _program(tmp_path, ["roman"])
    assert identity.roster_drift(root, tenant_path=tmp_path / "absent.yaml") == []


def test_drift_matches_on_email_too(tmp_path):
    root = _program(tmp_path, ["roman"])  # name roman, email roman@example.com
    tp = _tenant(tmp_path, ["roman@example.com"])  # roster_id carried as the email
    assert identity.roster_drift(root, tenant_path=tp) == []


# --- doctor integration -------------------------------------------------------


def test_doctor_check_returns_drift(tmp_path, monkeypatch):
    root = _program(tmp_path, ["roman"])
    tp = _tenant(tmp_path, ["roman", "ghost"])
    monkeypatch.setattr("otaman_cli.console.identity.tenant_roster_path", lambda home=None: tp)
    rc, drift = doctor._check_roster_sync(root)
    assert rc == 0  # WARN-only, never fails doctor
    assert [d["roster_id"] for d in drift] == ["ghost"]


def test_print_roster_sync_warns_naming_both_sides(capsys):
    doctor._print_roster_sync_report([{"roster_id": "ghost", "fingerprint": "SHA256:fp-ghost"}])
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "ghost" in out  # names the enrolled identity
    assert "human-roster" in out  # names the missing platform side


def test_print_roster_sync_silent_when_in_sync(capsys):
    doctor._print_roster_sync_report([])
    assert capsys.readouterr().out == ""
