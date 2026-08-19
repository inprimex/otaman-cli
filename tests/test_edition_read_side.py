"""ce-ee-release-channels 3.2 — edition read-side + absent-runner UX.

Contract under test (archived design Q3a, co-signed deploy/bridge):
  - edition.yaml is identity, not enforcement
  - missing/unparseable file => edition UNKNOWN; raw errors preserved
  - readers ignore unknown keys
  - probe-vs-file mismatch => one-line diagnostic, never enforcement
  - runner-assuming commands on CE explain the CE state with the
    hosted-tier pointer instead of erroring raw
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import edition as edition_mod
from otaman_cli.edition import (
    absent_runner_notice,
    edition_mismatch_diagnostic,
    get_edition,
    read_edition_file,
)
from otaman_cli.watchdog import _print_payload_or_error

# ---------------------------------------------------------------------------
# Helpers


def _write_edition(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "edition.yaml"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def ce_edition(tmp_path: Path, monkeypatch) -> Path:
    p = _write_edition(tmp_path, "edition: ce\nchannel: ce\nversion: '0.3.0'\n")
    monkeypatch.setattr(edition_mod, "DEFAULT_EDITION_PATH", p)
    return p


# ---------------------------------------------------------------------------
# read_edition_file / get_edition — Q3a parsing rules


def test_missing_file_is_unknown(tmp_path: Path):
    p = tmp_path / "edition.yaml"
    assert read_edition_file(p) == {}
    assert get_edition(p) == "unknown"


def test_unparseable_file_is_unknown(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: [unclosed\n")
    assert read_edition_file(p) == {}
    assert get_edition(p) == "unknown"


def test_non_mapping_file_is_unknown(tmp_path: Path):
    p = _write_edition(tmp_path, "- just\n- a list\n")
    assert get_edition(p) == "unknown"


def test_unknown_keys_are_ignored(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: ee\nfuture_key: whatever\nnested:\n  x: 1\n")
    assert get_edition(p) == "ee"


def test_invalid_edition_value_is_unknown(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: enterprise-plus\n")
    assert get_edition(p) == "unknown"


def test_edition_value_is_case_and_space_tolerant(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: ' CE '\n")
    assert get_edition(p) == "ce"


# ---------------------------------------------------------------------------
# edition_mismatch_diagnostic — diagnostic, never enforcement


def test_ce_with_runner_package_warns(tmp_path: Path, monkeypatch):
    p = _write_edition(tmp_path, "edition: ce\n")
    monkeypatch.setattr(edition_mod, "runner_package_present", lambda: True)
    diag = edition_mismatch_diagnostic(p)
    assert diag is not None and "ce" in diag


def test_ee_without_runner_package_warns(tmp_path: Path, monkeypatch):
    p = _write_edition(tmp_path, "edition: ee\n")
    monkeypatch.setattr(edition_mod, "runner_package_present", lambda: False)
    diag = edition_mismatch_diagnostic(p)
    assert diag is not None and "ee" in diag


def test_consistent_editions_have_no_diagnostic(tmp_path: Path, monkeypatch):
    p = _write_edition(tmp_path, "edition: ce\n")
    monkeypatch.setattr(edition_mod, "runner_package_present", lambda: False)
    assert edition_mismatch_diagnostic(p) is None


def test_unknown_edition_has_no_diagnostic(tmp_path: Path):
    assert edition_mismatch_diagnostic(tmp_path / "missing.yaml") is None


# ---------------------------------------------------------------------------
# absent_runner_notice — CE explains, others keep raw errors


def test_ce_notice_names_command_and_hosted_tier(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: ce\n")
    notice = absent_runner_notice("otaman spawn", p)
    assert notice is not None
    text = "\n".join(notice)
    assert "otaman spawn" in text
    assert edition_mod.HOSTED_TIER_POINTER in text


def test_ee_gets_no_notice(tmp_path: Path):
    p = _write_edition(tmp_path, "edition: ee\n")
    assert absent_runner_notice("otaman spawn", p) is None


def test_unknown_gets_no_notice(tmp_path: Path):
    assert absent_runner_notice("otaman spawn", tmp_path / "missing.yaml") is None


# ---------------------------------------------------------------------------
# watchdog print path — CE swap happens only for the missing-endpoint case


def test_watchdog_missing_endpoint_on_ce_prints_notice(ce_edition, capsys):
    rc = _print_payload_or_error(
        "status",
        0,
        {"error": "runner endpoint file missing or malformed: /x", "endpoint_missing": True},
        json_out=False,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR" not in out
    assert "Otaman CE" in out
    assert edition_mod.HOSTED_TIER_POINTER in out


def test_watchdog_unreachable_on_ce_keeps_raw_error(ce_edition, capsys):
    # Endpoint file EXISTS but the runner is down — a real error even on CE.
    rc = _print_payload_or_error("status", 0, {"error": "connection refused"}, json_out=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR: connection refused" in out


def test_watchdog_missing_endpoint_unknown_edition_keeps_raw_error(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(edition_mod, "DEFAULT_EDITION_PATH", tmp_path / "missing.yaml")
    rc = _print_payload_or_error(
        "status",
        0,
        {"error": "runner endpoint file missing or malformed: /x", "endpoint_missing": True},
        json_out=False,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR: runner endpoint file missing" in out


def test_watchdog_json_output_unaffected_by_ce(ce_edition, capsys):
    rc = _print_payload_or_error(
        "status",
        0,
        {"error": "runner endpoint file missing or malformed: /x", "endpoint_missing": True},
        json_out=True,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert '"http_status": 0' in out
    assert "Otaman CE" not in out


# ---------------------------------------------------------------------------
# session spawn — same swap on the spawn error path


def test_spawn_missing_endpoint_on_ce_prints_notice(ce_edition, tmp_path, monkeypatch, capsys):
    from otaman_cli import session_spawn

    monkeypatch.setattr(session_spawn, "load_token", lambda _p: "tok")
    monkeypatch.setattr(session_spawn, "jwt_sub", lambda _t: "user-1")
    rc = session_spawn.main(
        [
            "--agent",
            "cli-agent",
            "--repo",
            "otaman-cli",
            "--project-root",
            str(tmp_path),
            "--runner-endpoint",
            str(tmp_path / "no.endpoint"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "Otaman CE" in err
    assert edition_mod.HOSTED_TIER_POINTER in err


def test_spawn_missing_endpoint_unknown_edition_keeps_raw_error(
    tmp_path: Path, monkeypatch, capsys
):
    from otaman_cli import session_spawn

    monkeypatch.setattr(edition_mod, "DEFAULT_EDITION_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(session_spawn, "load_token", lambda _p: "tok")
    monkeypatch.setattr(session_spawn, "jwt_sub", lambda _t: "user-1")
    rc = session_spawn.main(
        [
            "--agent",
            "cli-agent",
            "--repo",
            "otaman-cli",
            "--project-root",
            str(tmp_path),
            "--runner-endpoint",
            str(tmp_path / "no.endpoint"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR: runner endpoint file missing" in err


# ---------------------------------------------------------------------------
# doctor — probe-vs-file mismatch is a warn-level diagnostic


def test_doctor_edition_check_ok_when_consistent(tmp_path: Path, monkeypatch):
    from otaman_cli.doctor import check_edition_consistency

    p = _write_edition(tmp_path, "edition: ce\n")
    monkeypatch.setattr(edition_mod, "DEFAULT_EDITION_PATH", p)
    monkeypatch.setattr(edition_mod, "runner_package_present", lambda: False)
    result = check_edition_consistency()
    assert result["status"] == "ok"
    assert result["details"]["edition"] == "ce"


def test_doctor_edition_check_warns_on_mismatch(tmp_path: Path, monkeypatch):
    from otaman_cli.doctor import check_edition_consistency

    p = _write_edition(tmp_path, "edition: ce\n")
    monkeypatch.setattr(edition_mod, "DEFAULT_EDITION_PATH", p)
    monkeypatch.setattr(edition_mod, "runner_package_present", lambda: True)
    result = check_edition_consistency()
    assert result["status"] == "warn"
    assert result["issues"][0]["severity"] == "low"
