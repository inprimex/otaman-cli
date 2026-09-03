"""policy-engine 2.1 — `otaman policy diff` (branch-protection desired vs live).

Read-only drift: the branch protection the effective git policy wants, compared
to what is live on the host, per repo, with the D4a failure-mode classification.
The gh-calling helpers are monkeypatched — no network.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands import policy  # noqa: E402
from otaman_cli.commands.policy import (  # noqa: E402
    _desired_protection,
    _diff_protection,
    cmd_policy,
)


def _program(tmp_path, monkeypatch, repos, roster=()):
    (tmp_path / ".agents").mkdir()
    lines = ["project: shop", "version: '1.0'", "policies:", "  git: standard", "repos:"]
    for r in repos:
        (tmp_path / r["path"]).mkdir(parents=True, exist_ok=True)
        lines.append(f"  - {{name: {r['name']}, path: {r['path']}, owner: {r['owner']}}}")
    if roster:
        lines.append("human-roster:")
        for h in roster:
            lines.append(f"  - {{name: {h}, email: {h}@x.com, roles: [cto]}}")
    (tmp_path / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return tmp_path


def _mock_gh(monkeypatch, *, live, contexts=("ci-ok",), slug="inprimex/foo", detect=True):
    from otaman_core import git_host

    monkeypatch.setattr(
        git_host,
        "detect_remote_for_repo",
        (lambda p: types.SimpleNamespace(provider="github", slug=slug))
        if detect
        else (lambda p: None),
    )
    monkeypatch.setattr(policy, "_default_branch", lambda s: "main")
    monkeypatch.setattr(policy, "_read_live_protection", lambda s, b: live)
    monkeypatch.setattr(policy, "_live_check_contexts", lambda s, b: list(contexts))


# ---- unit: desired protection ----


def test_desired_agent_owned_has_no_required_reviews():
    rules = {
        "force_push_forbidden": True,
        "require_status_checks": True,
        "owner_admission_required": True,
    }
    d = _desired_protection(rules, is_human_owned=False, contexts=["ci-ok"])
    assert d["allow_force_pushes"] is False
    assert d["required_status_checks"]["contexts"] == ["ci-ok"]
    assert "required_pull_request_reviews" not in d  # agent self-merges


def test_desired_human_owned_requires_a_reviewer():
    rules = {"owner_admission_required": True, "require_status_checks": True}
    d = _desired_protection(rules, is_human_owned=True, contexts=["lint-and-test"])
    assert d["required_pull_request_reviews"]["required_approving_review_count"] == 1


# ---- unit: diff classification (D4a) ----


def test_diff_create_from_nothing():
    desired = {
        "allow_force_pushes": False,
        "required_status_checks": {"strict": False, "contexts": ["ci-ok"]},
    }
    changes, mode = _diff_protection(desired, None)
    assert mode == "create-from-nothing" and changes


def test_diff_raise_approvals_0to1():
    desired = {"required_pull_request_reviews": {"required_approving_review_count": 1}}
    live = {"required_pull_request_reviews": {"required_approving_review_count": 0}}
    changes, mode = _diff_protection(desired, live)
    assert mode == "raise-approvals-0to1"
    assert "0 -> 1" in changes[0]


def test_diff_conformant():
    desired = {
        "allow_force_pushes": False,
        "required_status_checks": {"strict": False, "contexts": ["ci-ok"]},
    }
    live = {
        "allow_force_pushes": {"enabled": False},
        "required_status_checks": {"contexts": ["ci-ok"]},
    }
    changes, mode = _diff_protection(desired, live)
    assert mode == "conformant" and changes == []


# ---- integration: cmd diff ----


def test_cmd_diff_conformant_exit_zero(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(
        monkeypatch,
        live={
            "allow_force_pushes": {"enabled": False},
            "required_status_checks": {"contexts": ["ci-ok"]},
        },
    )
    rc = cmd_policy(["diff"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "conformant" in out and "agent-owned" in out


def test_cmd_diff_unprotected_drifts(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None)
    rc = cmd_policy(["diff"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "create-from-nothing" in out


def test_cmd_diff_human_owned_raise_approvals(tmp_path, monkeypatch, capsys):
    _program(
        tmp_path,
        monkeypatch,
        [{"name": "site", "path": "site", "owner": "roman"}],
        roster=["roman"],
    )
    _mock_gh(
        monkeypatch,
        live={
            "allow_force_pushes": {"enabled": False},
            "required_status_checks": {"contexts": ["ci-ok"]},
            "required_pull_request_reviews": {"required_approving_review_count": 0},
        },
    )
    rc = cmd_policy(["diff"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "human-owned" in out and "raise-approvals-0to1" in out


def test_cmd_diff_skips_repo_without_remote(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None, detect=False)
    rc = cmd_policy(["diff"])
    out = capsys.readouterr().out
    assert rc == 0  # skipped repos are not drift
    assert "skipped" in out


# ---- _live_check_contexts: ci-ok aggregator special-case (deploy incident) ----


def test_live_check_contexts_prefers_ci_ok_aggregator(monkeypatch):
    # ci-ok present among live checks → require ONLY ci-ok (not the individual
    # jobs, some of which may be continue-on-error and report failure)
    monkeypatch.setattr(
        policy,
        "_gh_json",
        lambda args: ["ci-ok", "lint", "test-ubuntu", "test-macos", "test-windows"],
    )
    assert policy._live_check_contexts("inprimex/foo", "main") == ["ci-ok"]


def test_live_check_contexts_enumerates_when_no_aggregator(monkeypatch):
    monkeypatch.setattr(policy, "_gh_json", lambda args: ["lint", "test-ubuntu"])
    assert sorted(policy._live_check_contexts("inprimex/foo", "main")) == ["lint", "test-ubuntu"]


def test_live_check_contexts_empty_when_unreadable(monkeypatch):
    monkeypatch.setattr(policy, "_gh_json", lambda args: None)
    assert policy._live_check_contexts("inprimex/foo", "main") == []
