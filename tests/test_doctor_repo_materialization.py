"""repo-registration-materialization 1.2 — doctor repo-materialization drift.

Registration edits platform.yaml but does not create the checkout; doctor must
flag the drift. A missing registered path FAILs; a present path missing its
`.otaman` marker or generated `CLAUDE.local.md` WARNs. Both hint `sync-repos`.

Unit-tests the CLI-side helper directly — the full `cmd_doctor` path delegates
its base checks to the plugin doctor script, which is out of scope here.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands.doctor import (
    _check_repo_materialization,
    _print_repo_materialization_report,
)


def _program(tmp_path: Path, repos_yaml: str) -> Path:
    root = tmp_path / "meta"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n" + repos_yaml, encoding="utf-8"
    )
    return root


def _materialized_repo(root: Path, rel: str) -> Path:
    repo = (root / rel).resolve()
    repo.mkdir(parents=True)
    (repo / ".otaman").write_text("../meta\nagent: x\n", encoding="utf-8")
    (repo / "CLAUDE.local.md").write_text("rules\n", encoding="utf-8")
    return repo


def test_missing_path_fails(tmp_path):
    root = _program(tmp_path, "  - name: svc\n    path: ../svc\n    owner: x\n")
    rc, results = _check_repo_materialization(root)
    assert rc == 1
    assert results == [{"name": "svc", "path": "../svc", "status": "missing"}]


def test_present_but_unmaterialized_warns_not_fails(tmp_path):
    root = _program(tmp_path, "  - name: svc\n    path: ../svc\n    owner: x\n")
    (root.parent / "svc").mkdir()  # dir exists, but no marker / rules
    rc, results = _check_repo_materialization(root)
    assert rc == 0  # WARN, not FAIL
    assert results[0]["status"] == "unmaterialized"
    assert set(results[0]["missing"]) == {".otaman", "CLAUDE.local.md"}


def test_partial_artifacts_warn(tmp_path):
    root = _program(tmp_path, "  - name: svc\n    path: ../svc\n    owner: x\n")
    repo = root.parent / "svc"
    repo.mkdir()
    (repo / ".otaman").write_text("../meta\n", encoding="utf-8")  # marker only
    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "unmaterialized"
    assert results[0]["missing"] == ["CLAUDE.local.md"]


def test_fully_materialized_is_ok(tmp_path):
    root = _program(tmp_path, "  - name: svc\n    path: ../svc\n    owner: x\n")
    _materialized_repo(root, "../svc")
    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "ok"


def test_missing_dominates_rc_in_a_mix(tmp_path):
    root = _program(
        tmp_path,
        "  - name: ok-svc\n    path: ../ok-svc\n    owner: x\n"
        "  - name: gone-svc\n    path: ../gone-svc\n    owner: x\n",
    )
    _materialized_repo(root, "../ok-svc")
    rc, results = _check_repo_materialization(root)
    assert rc == 1  # the absent repo FAILs the whole check
    by_name = {r["name"]: r["status"] for r in results}
    assert by_name == {"ok-svc": "ok", "gone-svc": "missing"}


def test_repo_without_path_is_skipped(tmp_path):
    root = _program(tmp_path, "  - name: unpathed\n    owner: x\n")
    rc, results = _check_repo_materialization(root)
    assert rc == 0 and results == []


def test_no_platform_yaml_is_noop(tmp_path):
    rc, results = _check_repo_materialization(tmp_path / "nope")
    assert rc == 0 and results == []


def test_report_shows_fix_hint_for_fail_and_warn(tmp_path, capsys):
    results = [
        {"name": "gone", "path": "../gone", "status": "missing"},
        {"name": "bare", "path": "../bare", "status": "unmaterialized", "missing": [".otaman"]},
        {"name": "good", "path": "../good", "status": "ok"},
    ]
    _print_repo_materialization_report(results)
    out = capsys.readouterr().out
    assert "FAIL" in out and "WARN" in out and "OK" in out
    assert out.count("otaman sync-repos") == 2  # hint on FAIL + WARN, not OK
