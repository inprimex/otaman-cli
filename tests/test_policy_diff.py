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


# ---- ci-ok from local workflow (deploy incident: check-runs unreliable) ----


def _write_ci_ok_workflow(repo_dir):
    wf = repo_dir / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "test.yml").write_text(
        "jobs:\n"
        "  test:\n    runs-on: ubuntu-latest\n"
        "  ci-ok:\n    needs: [test]\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )


def _cfg(root):
    import yaml

    return yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8"))


def test_repo_has_ci_ok_detects_job(tmp_path):
    _write_ci_ok_workflow(tmp_path)
    assert policy._repo_has_ci_ok(tmp_path) is True
    assert policy._repo_has_ci_ok(tmp_path / "nope") is False


def test_plan_ci_ok_repo_requires_only_ci_ok(tmp_path, monkeypatch):
    # the workflow defines ci-ok → require ONLY ci-ok, even though live check-runs
    # enumerate the individual jobs (and even when ci-ok isn't among them)
    root = _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _write_ci_ok_workflow(tmp_path / "foo")
    _mock_gh(monkeypatch, live=None, contexts=("lint", "test-ubuntu", "test-macos"))
    rec = next(r for r in policy._plan_repos(root, _cfg(root)) if r["repo"] == "foo")
    assert rec["desired"]["required_status_checks"]["contexts"] == ["ci-ok"]


def test_plan_non_ci_ok_repo_no_phantom_ci_ok(tmp_path, monkeypatch):
    # no ci-ok job + unreadable/empty live check-runs → NO required_status_checks
    # (never invent a phantom ci-ok that can't be satisfied)
    root = _program(tmp_path, monkeypatch, [{"name": "bar", "path": "bar", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None, contexts=())  # no workflow written → no ci-ok
    rec = next(r for r in policy._plan_repos(root, _cfg(root)) if r["repo"] == "bar")
    assert "required_status_checks" not in rec["desired"]


# ---- default-branch: live-preferred in the plan, local-first for check-merge ----
# (deploy 2026-09-03: a stale local origin/HEAD 'master' targeted a nonexistent
# branch in the apply plan for otaman-landing, whose live default is 'dev'.)


def test_repo_default_branch_prefer_live_beats_stale_local(monkeypatch):
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: "master")  # stale symref
    monkeypatch.setattr(policy, "_default_branch", lambda s: "dev")  # live truth
    # plan path prefers live; check-merge keeps local-first (availability > freshness)
    assert policy._repo_default_branch("d", "slug", prefer_live=True) == "dev"
    assert policy._repo_default_branch("d", "slug") == "master"


def test_repo_default_branch_prefer_live_falls_back_when_gh_down(monkeypatch):
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: "main")
    monkeypatch.setattr(policy, "_default_branch", lambda s: None)  # gh unavailable
    assert policy._repo_default_branch("d", "slug", prefer_live=True) == "main"


def test_repo_default_branch_last_resort_main(monkeypatch):
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: None)
    monkeypatch.setattr(policy, "_default_branch", lambda s: None)
    assert policy._repo_default_branch(None, "slug", prefer_live=True) == "main"
    assert policy._repo_default_branch(None, "slug") == "main"


def test_plan_prefers_live_branch_and_warns_on_stale_local(tmp_path, monkeypatch):
    root = _program(
        tmp_path, monkeypatch, [{"name": "landing", "path": "landing", "owner": "cli-agent"}]
    )
    _mock_gh(monkeypatch, live=None)
    monkeypatch.setattr(policy, "_default_branch", lambda s: "dev")  # live default
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: "master")  # stale local
    rec = next(r for r in policy._plan_repos(root, _cfg(root)) if r["repo"] == "landing")
    assert rec["branch"] == "dev"  # live wins, not the stale 'master'
    assert rec["warning"] and "master" in rec["warning"] and "dev" in rec["warning"]
    assert "set-head" in rec["warning"]  # actionable re-sync hint


def test_plan_no_warning_when_local_and_live_agree(tmp_path, monkeypatch):
    root = _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None)
    monkeypatch.setattr(policy, "_default_branch", lambda s: "main")
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: "main")
    rec = next(r for r in policy._plan_repos(root, _cfg(root)) if r["repo"] == "foo")
    assert rec["branch"] == "main" and rec["warning"] is None


def test_cmd_diff_surfaces_stale_branch_warning(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, [{"name": "landing", "path": "landing", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None)
    monkeypatch.setattr(policy, "_default_branch", lambda s: "dev")
    monkeypatch.setattr(policy, "_local_default_branch", lambda d: "master")
    cmd_policy(["diff"])
    out = capsys.readouterr().out
    assert "stale" in out and "dev" in out and "set-head" in out


def test_desired_omits_required_checks_when_contexts_empty():
    d = _desired_protection(
        {"require_status_checks": True, "force_push_forbidden": True},
        is_human_owned=False,
        contexts=[],
    )
    assert "required_status_checks" not in d
    assert d["allow_force_pushes"] is False
