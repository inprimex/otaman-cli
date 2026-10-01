"""security-gates-hook-c 1.2 — policy wiring over core's gate config.

Core owns parsing and resolution. This is the cli side: evaluating a real
program's config, reporting it, and the one part that is policy rather than
configuration — whether a security suppression justifies itself.

The spec's three scenarios drive the shape: the expensive layer reads only
residual, deterministic beats judgment, and a bare suppression cannot pass. 1.2
owns the third plus the surfacing of the first two's configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.security_gates import (
    FRESH,
    NOT_CHECKED,
    SKIPPED,
    STALE,
    evaluate,
    python_sources,
    scan_suppressions,
)

_LADDER = """project: demo
repos:
  - name: svc
    languages: [python]
  - name: advisory-only
    languages: [docsonly]
  - name: docs
security-gates:
  languages:
    python:
      ci-fast: {tools: [gitleaks, semgrep], blocking: true, timeout: 30}
      ci-medium: {tools: [semgrep], blocking: true, scanner-pair: [trivy, grype]}
    docsonly:
      # No `opt-in` here: core rules that opt-in is a PER-REPO decision and
      # refuses it as a language default. This fixture carried it until core
      # tightened the validation mid-task — the refusal is right, and the
      # fixture was wrong.
      ci-slow: {tools: [deep-sast], blocking: false}
  repos:
    docs:
      opt-out: true
"""


@pytest.fixture
def program(tmp_path):
    (tmp_path / "platform.yaml").write_text(_LADDER, encoding="utf-8")
    return tmp_path


def _by_repo(report):
    return {r.repo: r for r in report.repos}


# ---------------------------------------------------------------------------
# evaluation over the config


def test_a_repo_with_blocking_layers_is_fresh(program):
    rows = _by_repo(evaluate(program))
    assert rows["svc"].verdict == FRESH
    assert "ci-fast" in rows["svc"].blocking


def test_a_repo_whose_layers_are_all_advisory_is_stale(program):
    """Configured is not the same as capable of blocking. A ladder that cannot
    stop a PR is worth saying out loud."""
    rows = _by_repo(evaluate(program))
    assert rows["advisory-only"].verdict == STALE
    assert "nothing here can block" in rows["advisory-only"].reason


def test_an_opt_out_repo_is_rendered_not_dropped(program):
    """The spec requires the opt-out be VISIBLE: a repo missing from the report
    cannot be told from a repo nobody configured."""
    rows = _by_repo(evaluate(program))
    assert rows["docs"].verdict == SKIPPED
    assert rows["docs"].opt_out is True


def test_a_repo_matching_no_language_default_is_not_checked(tmp_path):
    (tmp_path / "platform.yaml").write_text(
        "project: d\nrepos:\n  - name: mystery\n    languages: [cobol]\n"
        "security-gates:\n  languages:\n    python:\n"
        "      ci-fast: {tools: [semgrep], blocking: true}\n",
        encoding="utf-8",
    )
    rows = _by_repo(evaluate(tmp_path))
    assert rows["mystery"].verdict == NOT_CHECKED


def test_no_block_at_all_is_not_a_failure(tmp_path):
    """A program that has declared no ladder has nothing to report — that is
    different from one whose ladder could not be read."""
    (tmp_path / "platform.yaml").write_text("project: d\nrepos: []\n", encoding="utf-8")
    report = evaluate(tmp_path)
    assert report.repos == [] and report.error == ""


def test_an_unparseable_block_is_not_checked_never_no_gates(tmp_path):
    """The fail-open class this repo fixed twice in #223: an unreadable config
    reported as "nothing configured" is a gate that waved itself through."""
    (tmp_path / "platform.yaml").write_text(
        "project: d\nrepos: []\nsecurity-gates:\n  languages:\n    python:\n"
        "      ci-fast: {tools: [semgrep], bogus-key: 1}\n",
        encoding="utf-8",
    )
    report = evaluate(tmp_path)
    assert report.error and "does not parse" in report.error
    assert report.repos == []


def test_an_unreadable_platform_yaml_is_not_checked(tmp_path):
    (tmp_path / "platform.yaml").write_text("project: [unclosed\n", encoding="utf-8")
    assert "could not be read" in evaluate(tmp_path).error


def test_an_absent_core_is_not_checked_rather_than_clean(program, monkeypatch):
    import otaman_cli.security_gates as mod

    monkeypatch.setattr(mod, "_core", lambda: None)
    report = mod.evaluate(program)
    assert report.error and "1.1" in report.error


# ---------------------------------------------------------------------------
# the suppression policy


def test_a_bare_security_suppression_fails(tmp_path):
    """The spec's scenario 3, verbatim: a `# nosemgrep` with no justification."""
    f = tmp_path / "x.py"
    f.write_text("value = compute()  # nosemgrep\n", encoding="utf-8")
    unjustified, _, _ = scan_suppressions([f])
    assert len(unjustified) == 1 and unjustified[0].line == 1


def test_a_justified_suppression_passes(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("v = go()  # nosec - reviewed 2026-10-01, input is operator-set\n", "utf-8")
    unjustified, coded, justified = scan_suppressions([f])
    assert unjustified == [] and justified == 1 and coded == 0


def test_a_rule_code_without_prose_is_counted_not_failed(tmp_path):
    """A code says WHICH rule was silenced, never WHY. Weaker than a
    justification, stronger than a bare marker — so it is named, not failed.

    Uses the `# nosec B101` form: there the code is OPTIONAL extra detail. For
    `# noqa: S310` the code is ruff SYNTAX — required to suppress at all — so it
    is part of the marker and its absence of prose is a bare suppression, which
    the next test pins.
    """
    f = tmp_path / "x.py"
    f.write_text("v = go()  # nosec B101\n", encoding="utf-8")
    unjustified, coded, justified = scan_suppressions([f])
    assert unjustified == [] and coded == 1 and justified == 0


def test_a_security_noqa_with_no_prose_is_bare(tmp_path):
    """`# noqa: S310` carries no justification: ruff REQUIRES the code, so the
    code is syntax rather than a reason. Spec: a bare marker fails."""
    f = tmp_path / "x.py"
    f.write_text("v = go()  # noqa: S310\n", encoding="utf-8")
    unjustified, _, _ = scan_suppressions([f])
    assert len(unjustified) == 1


def test_a_security_rule_noqa_is_a_security_suppression(tmp_path):
    """ruff's `S` rules ARE the bandit port, so in this toolchain a security
    suppression is written `# noqa: S310`. Missing that would make the check
    blind to the form the repo actually uses."""
    f = tmp_path / "x.py"
    f.write_text("urlopen(u)  # noqa: S310 - endpoint is operator-configured\n", "utf-8")
    _, _, justified = scan_suppressions([f])
    assert justified == 1


def test_a_style_suppression_is_not_a_security_suppression(tmp_path):
    """My first version matched `# noqa` and `# type: ignore` wholesale and found
    14 "bare" suppressions here — every one a `# type: ignore` on an import. A
    rule matching what it was not written for is how a gate gets switched off."""
    f = tmp_path / "x.py"
    f.write_text("import yaml  # type: ignore\nx = 1  # noqa\n", encoding="utf-8")
    unjustified, coded, justified = scan_suppressions([f])
    assert (unjustified, coded, justified) == ([], 0, 0)


def test_this_repo_has_no_unjustified_security_suppression():
    """Run against the real source — the measurement that made the rule
    credible, and a regression guard on it."""
    unjustified, _, justified = scan_suppressions(python_sources(Path("src")))
    assert unjustified == [], f"unjustified: {[(s.path, s.line) for s in unjustified]}"
    assert justified >= 1, "the matcher found nothing at all — it is not looking correctly"


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    assert scan_suppressions([tmp_path / "missing.py"]) == ([], 0, 0)


def test_vendored_and_build_paths_are_not_scanned(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "v.py").write_text("x = 1  # nosemgrep\n", encoding="utf-8")
    (tmp_path / "own.py").write_text("y = 2\n", encoding="utf-8")
    assert [p.name for p in python_sources(tmp_path)] == ["own.py"]


# ---------------------------------------------------------------------------
# the doctor surface


def test_doctor_reports_the_verdict_mix(program):
    from otaman_cli.doctor import check_security_gates

    result = check_security_gates(program)
    assert result["details"]["repos"] == 3
    assert "fresh" in result["details"]["verdicts"]
    assert "skipped" in result["details"]["verdicts"]


def test_doctor_fails_on_an_unjustified_suppression(program):
    from otaman_cli.doctor import check_security_gates

    (program / "bad.py").write_text("x = 1  # nosemgrep\n", encoding="utf-8")
    result = check_security_gates(program)
    assert result["status"] == "fail"
    assert any("unjustified suppression" in i["message"] for i in result["issues"])


def test_doctor_says_not_checked_for_an_unparseable_block(tmp_path):
    from otaman_cli.doctor import check_security_gates

    (tmp_path / "platform.yaml").write_text(
        "project: d\nsecurity-gates:\n  languages:\n    python:\n"
        "      ci-fast: {tools: [semgrep], bogus: 1}\n",
        encoding="utf-8",
    )
    result = check_security_gates(tmp_path)
    assert result["status"] == "warn"
    assert "NOT CHECKED" in result["details"]["gates"]


def test_doctor_uses_the_freshness_vocabulary():
    """One doctor run must not speak two dialects — the verdict words are the
    ones the runtime-freshness section already uses."""
    from otaman_cli.console import freshness

    assert FRESH == freshness.VERDICT_FRESH
    assert STALE == freshness.VERDICT_STALE
    assert NOT_CHECKED == freshness.VERDICT_NOT_CHECKED
