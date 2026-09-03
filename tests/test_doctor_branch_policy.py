"""policy-engine 4.2 — `otaman doctor` branch-policy section.

Ownership + delegation are local; drift is a best-effort live read that is
SKIPPED (not false-reported) when gh is unavailable. gh + policy diff are
monkeypatched — no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands import policy  # noqa: E402
from otaman_cli.doctor import check_branch_policy  # noqa: E402


def _setup(tmp_path, repos, *, roster=("roman",), deprecated=False, delegation=None):
    (tmp_path / ".agents").mkdir()
    lines = ["project: shop", "version: '1.0'", "policies:", "  git: standard"]
    if deprecated:
        lines += ["standards:", "  git:", "    branching: trunk-based"]
    lines.append("repos:")
    if not repos:
        lines[-1] = "repos: []"
    for r in repos:
        lines.append(f"  - {{name: {r['name']}, path: {r['name']}, owner: {r['owner']}}}")
    if roster:
        lines.append("human-roster:")
        for h in roster:
            lines.append(f"  - {{name: {h}, email: {h}@x.com, roles: [cto]}}")
    (tmp_path / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if delegation:
        d = tmp_path / "policy" / "git"
        d.mkdir(parents=True)
        (d / "branch-owners.yaml").write_text(
            "\n".join(f"{k}: {v}" for k, v in delegation.items()) + "\n", encoding="utf-8"
        )
    cfg = yaml.safe_load((tmp_path / "platform.yaml").read_text(encoding="utf-8"))
    return tmp_path, cfg


def test_no_repos_ok(tmp_path):
    root, cfg = _setup(tmp_path, [])
    res = check_branch_policy(cfg, root)
    assert res["check"] == "branch_policy" and res["status"] == "ok"
    assert res["details"]["skipped"]


def test_deprecated_standards_flagged(tmp_path, monkeypatch):
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}], deprecated=True)
    monkeypatch.setattr(policy, "_gh_available", lambda: False)
    res = check_branch_policy(cfg, root)
    assert any("deprecated" in i["message"] for i in res["issues"])
    assert res["status"] == "warn"


def test_ownership_and_delegation_details(tmp_path, monkeypatch):
    root, cfg = _setup(
        tmp_path,
        [{"name": "site", "owner": "roman"}, {"name": "cli", "owner": "cli-agent"}],
        roster=["roman"],
        delegation={"release": "roman"},
    )
    monkeypatch.setattr(policy, "_gh_available", lambda: False)
    res = check_branch_policy(cfg, root)
    kinds = {o["repo"]: o["kind"] for o in res["details"]["ownership"]}
    assert kinds == {"site": "human", "cli": "agent"}
    assert res["details"]["delegation"] == {"release": "roman"}
    assert res["details"]["drift"] == "not checked (gh unavailable/unauthenticated)"


def test_drift_skipped_when_gh_unavailable_no_false_drift(tmp_path, monkeypatch):
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}])
    monkeypatch.setattr(policy, "_gh_available", lambda: False)
    res = check_branch_policy(cfg, root)
    assert res["status"] == "ok"  # no false drift
    assert not any("drift" in i["message"] for i in res["issues"])


def test_drift_reported_warn_mode(tmp_path, monkeypatch):
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}])
    monkeypatch.setattr(policy, "_gh_available", lambda: True)
    monkeypatch.setattr(
        policy,
        "_plan_repos",
        lambda root, config: [
            {
                "repo": "foo",
                "slug": "inprimex/foo",
                "branch": "main",
                "kind": "agent",
                "desired": {},
                "changes": ["force pushes allowed -> forbidden"],
                "mode": "update",
            }
        ],
    )
    res = check_branch_policy(cfg, root)
    assert res["status"] == "warn"
    drift_issues = [i for i in res["issues"] if "drift" in i["message"]]
    assert drift_issues and drift_issues[0]["severity"] == "medium"


def test_drift_reported_block_mode_is_high(tmp_path, monkeypatch):
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}])
    cfg["policies"]["enforcement"] = "block"
    monkeypatch.setattr(policy, "_gh_available", lambda: True)
    monkeypatch.setattr(
        policy,
        "_plan_repos",
        lambda root, config: [
            {
                "repo": "foo",
                "slug": "inprimex/foo",
                "branch": "main",
                "kind": "agent",
                "desired": {},
                "changes": ["required checks missing: ci-ok"],
                "mode": "create-from-nothing",
            }
        ],
    )
    res = check_branch_policy(cfg, root)
    assert res["status"] == "fail"
    assert any(i["severity"] == "high" for i in res["issues"])


def test_policy_source_implicit_when_no_files(tmp_path, monkeypatch):
    # D11: doctor must STATE implicit defaults rather than staying silent
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}])
    monkeypatch.setattr(policy, "_gh_available", lambda: False)
    res = check_branch_policy(cfg, root)
    assert res["details"]["policy_source"] == "implicit (shipped in-code standard)"
    # informational only — does not flip status to warn on its own
    assert res["status"] == "ok"


def test_policy_source_on_disk_when_standard_present(tmp_path, monkeypatch):
    root, cfg = _setup(tmp_path, [{"name": "foo", "owner": "cli-agent"}])
    (root / "policy" / "git").mkdir(parents=True, exist_ok=True)
    (root / "policy" / "git" / "standard.yaml").write_text(
        "pack: git\nname: standard\nrules: {}\n", encoding="utf-8"
    )
    monkeypatch.setattr(policy, "_gh_available", lambda: False)
    res = check_branch_policy(cfg, root)
    assert res["details"]["policy_source"] == "on-disk"
