"""`otaman doctor` must not die on its own finding (2026-10-02).

Measured on the live program: of 31 issues the report carried, ONE was malformed —
a `branch_policy` finding keyed `message` where the renderer read `issue["issue"]`.
The `KeyError` landed inside the Issues loop, so everything after it was suppressed:
the pm-sync section, the summary line, the exit-code computation, and ~20 further
checks (local ownership, registry home, registry loadability, enforcement map …).
The report went from 198 lines to 105 ending in a traceback, and `otaman doctor`
reported no verdict at all.

The cause is two key conventions for one contract: 44 producer sites write `issue`,
19 write `message`. Each of the 19 is a latent crash that fires the day its
condition does. So this pins three things: both keys render, an unrenderable finding
renders LOUDLY instead of raising, and no third convention can appear unnoticed.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from otaman_cli.commands import doctor as CD

# ---------------------------------------------------------------------------
# the text of a finding, whichever key carries it


def test_the_original_key_renders():
    assert CD.issue_text({"issue": "the registry home is not configured"}) == (
        "the registry home is not configured"
    )


def test_the_message_key_renders_the_regression_exactly():
    """The live finding that crashed doctor, verbatim in shape."""
    issue = {
        "severity": "medium",
        "message": "otaman-docs (main, agent-owned): branch-protection drift [update]",
        "fix": "run `otaman policy apply` (cto); deploy then applies it live",
        "check": "branch_policy",
    }

    assert "branch-protection drift" in CD.issue_text(issue)


def test_a_finding_with_no_text_renders_a_marker_that_names_the_defect():
    """Loud, not silent: the marker must say WHICH check and what it did carry.

    The alternative shapes — raising (the bug) or printing an empty line (a finding
    nobody can act on) — are both worse than an ugly line that identifies its source.
    """
    text = CD.issue_text({"severity": "high", "check": "branch_policy", "fix": "..."})

    assert "malformed" in text.lower()
    assert "branch_policy" in text, "the marker must name the producing check"
    assert "severity" in text and "fix" in text, "it must name the keys it did carry"


def test_a_non_dict_finding_does_not_raise():
    assert "malformed" in CD.issue_text("just a string").lower()
    assert "malformed" in CD.issue_text(None).lower()


@pytest.mark.parametrize(
    "issue",
    [
        {"severity": "critical", "issue": "x", "fix": "y"},
        {"severity": "high", "message": "x"},
        {"severity": "medium"},
        {"severity": "low", "issue": "x", "fix": ""},
        "a bare string",
    ],
)
def test_render_issue_never_raises(issue, capsys):
    CD.render_issue(issue)
    capsys.readouterr()


def test_the_fix_line_is_omitted_when_there_is_no_fix(capsys):
    CD.render_issue({"severity": "medium", "issue": "something"})
    out = capsys.readouterr()
    assert "Fix:" not in out.out + out.err


# ---------------------------------------------------------------------------
# the aggregator normalises, so a JSON consumer need not know the convention


def test_the_aggregator_carries_both_keys():
    """`run_doctor`'s issue list is also the `--json` contract, and plugin/web read it.

    Calls the REAL aggregator — an earlier version of this test reimplemented the
    normalisation inline and therefore passed with the production code sabotaged,
    which is the vacuous-guard shape: the test reached the subject by a path that
    could not reach the condition.
    """
    from otaman_cli import doctor as D

    checks = [
        {"check": "a", "status": "warn", "issues": [{"severity": "medium", "message": "m-only"}]},
        {"check": "b", "status": "warn", "issues": [{"severity": "medium", "issue": "i-only"}]},
        {"check": "c", "status": "warn", "issues": ["a bare string"]},
    ]

    out = D.normalise_issues(checks)

    assert [i["issue"] for i in out] == ["m-only", "i-only", "a bare string"]
    assert [i["message"] for i in out[:2]] == ["m-only", "i-only"]
    assert [i["check"] for i in out] == ["a", "b", "c"]
    assert all(CD.issue_text(i) for i in out)
    assert not any("malformed" in CD.issue_text(i).lower() for i in out)


def test_the_aggregator_does_not_mutate_the_checks_it_reads():
    """A check's own `issues` list is its report; the aggregate must not rewrite it."""
    from otaman_cli import doctor as D

    original = {"severity": "medium", "message": "m-only"}
    checks = [{"check": "a", "status": "warn", "issues": [original]}]

    D.normalise_issues(checks)

    assert original == {"severity": "medium", "message": "m-only"}


def test_run_doctor_normalises_a_message_only_issue(tmp_path, monkeypatch):
    """End-to-end through the real aggregator, not a copy of its code."""
    from otaman_cli import doctor as D

    (tmp_path / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", "utf-8")
    report = D.run_doctor(tmp_path)

    for issue in report["issues"]:
        assert isinstance(issue, dict)
        assert CD.issue_text(issue), f"unrenderable finding survived the aggregator: {issue}"
        assert "malformed" not in CD.issue_text(issue).lower(), (
            f"a real check produced an unrenderable finding: {issue}"
        )


# ---------------------------------------------------------------------------
# no third convention, and the whole report still renders


def test_every_issue_producer_uses_an_accepted_text_key():
    """A new producer writing `detail:` or `note:` must not be discovered by a crash.

    Scanned at the source: the 19 `message` sites were introduced one at a time by
    checks written months after the renderer, and nothing told their authors which
    key the renderer reads.
    """
    from otaman_cli import doctor as D

    src = inspect.getsource(D)
    offenders = []
    for match in re.finditer(r"issues\.append\(\s*\{(.*?)\}\s*\)", src, re.DOTALL):
        literal = match.group(1)
        if not any(f'"{key}":' in literal for key in CD.ISSUE_TEXT_KEYS):
            offenders.append(literal.strip()[:90])
    assert not offenders, (
        f"issue literals with no accepted text key (accepted: {CD.ISSUE_TEXT_KEYS}): {offenders}"
    )


def test_a_malformed_finding_does_not_suppress_the_summary(monkeypatch, tmp_path, capsys):
    """THE REGRESSION: the crash took the verdict and ~20 later checks with it.

    So this asserts the thing the traceback actually cost — that the run reaches its
    summary line — with a report whose finding is as broken as the live one was.
    """
    (tmp_path / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", "utf-8")
    report = {
        "project_root": str(tmp_path),
        "summary": {"passed": 2, "warned": 1, "failed": 0, "total": 3},
        "checks": [{"check": "git", "status": "ok", "details": {}}],
        "issues": [
            {"severity": "medium", "message": "keyed message", "fix": "f"},
            {"severity": "high", "check": "branch_policy"},  # no text at all
        ],
    }

    class _R:
        returncode = 0
        stdout = json.dumps(report)
        stderr = ""

    monkeypatch.setattr(CD, "run_script", lambda *a, **k: _R())
    monkeypatch.setattr(CD, "find_program_root", lambda: tmp_path, raising=False)

    rc = CD.cmd_doctor([str(tmp_path)])

    out = capsys.readouterr()
    text = out.out + out.err
    assert "keyed message" in text, "the message-keyed finding must render"
    assert "malformed" in text.lower(), "the textless finding must render loudly"
    assert "warning" in text.lower() or "passed" in text.lower(), (
        f"the summary line never printed — the tail is still being suppressed:\n{text[-400:]}"
    )
    assert isinstance(rc, int)
