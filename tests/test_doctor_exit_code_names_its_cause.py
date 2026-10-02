"""`otaman doctor` exits 1 and says WHICH checks set it (CTO-review standing item).

Eight independent conditions folded into one `base_rc` and were returned bare. The
operator — and any automation gating on the exit code — saw `1` and had to scan ~200
lines to guess the cause, in output that also contains several WARN-only sections
which look just as alarming and do NOT affect the code. A non-zero exit nobody can
attribute is a health signal nobody can act on.

Measured on the live program after the fix: `Exit 1 — failing: 2 environment
check(s)` — the other seven conditions were clean, which the bare `1` could not say.

The end-to-end cases drive `cmd_doctor` with one contributor failing at a time, so
the accumulator is proven to be wired to each site rather than merely to exist.
"""

from __future__ import annotations

import json

import pytest

from otaman_cli.commands import doctor as CD

# ---------------------------------------------------------------------------
# the renderer's own contract


def test_no_contributors_exits_zero_and_says_nothing(capsys):
    """The summary line above has already reported the good news."""
    rc = CD._exit_with_cause([])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_one_contributor_is_named(capsys):
    rc = CD._exit_with_cause(["approver config"])

    assert rc == 1
    assert "Exit 1 — failing: approver config" in capsys.readouterr().out


def test_every_contributor_is_named_not_just_the_first(capsys):
    rc = CD._exit_with_cause(["2 environment check(s)", "registry home", "enforcement map"])

    out = capsys.readouterr().out
    assert rc == 1
    for name in ("2 environment check(s)", "registry home", "enforcement map"):
        assert name in out


# ---------------------------------------------------------------------------
# end to end: each site feeds the accumulator


_CLEAN_REPORT = {
    "project_root": "x",
    "summary": {"passed": 3, "warned": 0, "failed": 0, "total": 3},
    "checks": [{"check": "git", "status": "ok", "details": {}}],
    "issues": [],
}


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """cmd_doctor with every fold contributor CLEAN and the report healthy."""
    (tmp_path / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", "utf-8")

    class _R:
        returncode = 0
        stdout = json.dumps(_CLEAN_REPORT)
        stderr = ""

    monkeypatch.setattr(CD, "run_script", lambda *a, **k: _R())
    monkeypatch.setattr(CD, "_check_repo_materialization", lambda root: (0, []))
    monkeypatch.setattr(CD, "_check_approver_config", lambda root: (0, []))
    monkeypatch.setattr(CD, "_check_local_ownership", lambda deep=False: {})
    monkeypatch.setattr(
        CD, "_check_strategy_repo", lambda root: {"applicable": False, "ok": False, "detail": ""}
    )
    monkeypatch.setattr(
        CD,
        "_check_registry_loadability",
        lambda root: {"applicable": False, "ok": False, "detail": ""},
    )
    monkeypatch.setattr(
        CD, "_check_enforcement_map", lambda root: {"applicable": False, "ok": False, "detail": ""}
    )
    return tmp_path


def test_a_healthy_program_exits_zero_with_no_cause_line(quiet, capsys):
    rc = CD.cmd_doctor([str(quiet)])

    out = capsys.readouterr()
    assert rc == 0
    assert "Exit 1" not in out.out + out.err


@pytest.mark.parametrize(
    ("attr", "value", "expected"),
    [
        ("_check_repo_materialization", lambda root: (1, []), "repo materialization"),
        ("_check_approver_config", lambda root: (1, []), "approver config"),
        ("_check_local_ownership", lambda deep=False: {"foreign": 3}, "~/.local ownership"),
        (
            "_check_strategy_repo",
            lambda root: {"applicable": True, "ok": False, "detail": "x"},
            "registry home",
        ),
        (
            "_check_registry_loadability",
            lambda root: {"applicable": True, "ok": False, "detail": "x"},
            "registry loadability",
        ),
        (
            "_check_enforcement_map",
            lambda root: {"applicable": True, "ok": False, "detail": "x"},
            "enforcement map",
        ),
    ],
)
def test_each_failing_check_names_itself_in_the_exit_line(
    quiet, capsys, monkeypatch, attr, value, expected
):
    """One contributor at a time: proves the accumulator is wired to every site.

    A test that only exercised `_exit_with_cause` would pass with any of the eight
    sites left writing to nothing.
    """
    monkeypatch.setattr(CD, attr, value)

    rc = CD.cmd_doctor([str(quiet)])

    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 1, f"{attr} failing must exit 1"
    assert f"Exit 1 — failing: {expected}" in text, text[-500:]


def test_a_failing_environment_report_is_counted_and_named(quiet, capsys, monkeypatch):
    class _R:
        returncode = 0
        stdout = json.dumps(
            {**_CLEAN_REPORT, "summary": {"passed": 1, "warned": 0, "failed": 2, "total": 3}}
        )
        stderr = ""

    monkeypatch.setattr(CD, "run_script", lambda *a, **k: _R())

    rc = CD.cmd_doctor([str(quiet)])

    out = capsys.readouterr()
    assert rc == 1
    assert "2 environment check(s)" in out.out + out.err


def test_two_contributors_are_both_named_end_to_end(quiet, capsys, monkeypatch):
    monkeypatch.setattr(CD, "_check_approver_config", lambda root: (1, []))
    monkeypatch.setattr(
        CD, "_check_enforcement_map", lambda root: {"applicable": True, "ok": False, "detail": "x"}
    )

    rc = CD.cmd_doctor([str(quiet)])

    text = "".join(capsys.readouterr())
    assert rc == 1
    assert "approver config" in text and "enforcement map" in text


def test_a_warn_only_check_does_not_change_the_exit_code(quiet, capsys, monkeypatch):
    """The distinction the bare code erased: several sections warn and must NOT fail.

    `_check_roster_sync`, identity chain, plugin wiring, docs format, tenant
    consistency, stale presence and solution disposition are all deliberately
    outside the exit code. If one of them started folding in, doctor would go
    permanently red on a healthy program — which trains everyone to ignore it.
    """
    monkeypatch.setattr(
        CD, "_check_roster_sync", lambda root: (1, [{"status": "error", "error": "drifted"}])
    )

    rc = CD.cmd_doctor([str(quiet)])

    out = capsys.readouterr()
    assert rc == 0, "a WARN-only check must not set the exit code"
    assert "Exit 1" not in out.out + out.err
