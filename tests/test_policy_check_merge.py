"""policy-engine 2.1 — `otaman policy check-merge` (the agent merge guard).

Read-only pre-merge check: refuse (exit non-zero) an agent session merging into
a human-owned or owner-less branch. Owner intent comes from the branch
convention + branch-owners.yaml, never from live protection state (D4a).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands.policy import _GUARD_REFUSED, cmd_policy  # noqa: E402


def _program(tmp_path, monkeypatch, roster=("roman",), branch_owners=None):
    (tmp_path / ".agents").mkdir()
    lines = ["project: shop", "version: '1.0'", "policies:", "  git: standard", "repos: []"]
    if roster:
        lines.append("human-roster:")
        for h in roster:
            lines.append(f"  - {{name: {h}, email: {h}@x.com, roles: [cto]}}")
    (tmp_path / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if branch_owners:
        d = tmp_path / "policy" / "git"
        d.mkdir(parents=True)
        body = "\n".join(f"{k}: {v}" for k, v in branch_owners.items())
        (d / "branch-owners.yaml").write_text(body + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    return tmp_path


def _as_agent(monkeypatch):
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)


def _as_human(monkeypatch, name="roman"):
    monkeypatch.setenv("OTAMAN_HUMAN", name)
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)


def test_agent_into_human_owned_branch_refused(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_agent(monkeypatch)
    # convention <type>/<owner>/<topic> — owner "roman" is a roster human
    rc = cmd_policy(["check-merge", "feat/roman/pagination"])
    out = capsys.readouterr().out
    assert rc == _GUARD_REFUSED
    assert "human-owned" in out and "roman" in out


def test_agent_into_agent_owned_branch_allowed(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_agent(monkeypatch)
    # owner "cli-agent" is not in the roster → agent-owned → the owning agent merges
    rc = cmd_policy(["check-merge", "fix/cli-agent/bug"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK to merge" in out and "agent" in out


def test_human_into_human_owned_branch_allowed(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_human(monkeypatch, "roman")
    rc = cmd_policy(["check-merge", "feat/roman/pagination"])
    assert rc == 0
    assert "OK to merge" in capsys.readouterr().out


def test_owner_less_branch_refused(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_agent(monkeypatch)
    # bare branch name: no convention, no registry, no default-branch mapping
    rc = cmd_policy(["check-merge", "scratchpad"])
    out = capsys.readouterr().out
    assert rc == _GUARD_REFUSED
    assert "owner-less" in out


def test_branch_owners_registry_overrides_to_human(tmp_path, monkeypatch, capsys):
    # a branch with no convention owner, but the registry maps it to a human
    _program(tmp_path, monkeypatch, roster=["roman"], branch_owners={"release": "roman"})
    _as_agent(monkeypatch)
    rc = cmd_policy(["check-merge", "release"])
    out = capsys.readouterr().out
    assert rc == _GUARD_REFUSED
    assert "roman" in out


def test_usage_without_branch(tmp_path, monkeypatch):
    _program(tmp_path, monkeypatch)
    _as_agent(monkeypatch)
    assert cmd_policy(["check-merge"]) == 2


def test_agent_into_own_repo_default_branch_allowed(tmp_path, monkeypatch, capsys):
    """Regression for the 5.1 gate blocker: an agent merging into its own repo's
    DEFAULT branch must be allowed. Previously check-merge resolved the default
    branch via a fragile gh call and, on any failure, called every default branch
    owner-less. resolve_branch_owner now gets is_default_branch from robust
    detection, so the default branch resolves to repos[].owner."""
    from otaman_cli.commands import policy

    (tmp_path / ".agents").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\npolicies:\n  git: standard\n"
        "repos:\n  - {name: otaman-specs, path: specs, owner: spec-agent}\n"
        "human-roster:\n  - {name: roman, email: roman@x.com, roles: [cto]}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "spec-agent")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    monkeypatch.setattr(policy, "_repo_default_branch", lambda repo_dir, slug: "main")

    rc = cmd_policy(["check-merge", "main", "--repo", "otaman-specs"])
    out = capsys.readouterr().out
    assert rc == 0, out  # agent self-merges its own repo's default branch
    assert "OK to merge" in out and "spec-agent" in out and "agent" in out


def test_agent_into_human_repo_default_branch_refused(tmp_path, monkeypatch, capsys):
    """The mirror: an agent merging into a HUMAN-owned repo's default branch is
    still refused (owner resolves to the human repo owner)."""
    from otaman_cli.commands import policy

    (tmp_path / ".agents").mkdir()
    (tmp_path / "site").mkdir()
    (tmp_path / "platform.yaml").write_text(
        "project: shop\nversion: '1.0'\npolicies:\n  git: standard\n"
        "repos:\n  - {name: site, path: site, owner: roman}\n"
        "human-roster:\n  - {name: roman, email: roman@x.com, roles: [cto]}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    monkeypatch.setattr(policy, "_repo_default_branch", lambda repo_dir, slug: "main")

    rc = cmd_policy(["check-merge", "main", "--repo", "site"])
    out = capsys.readouterr().out
    assert rc == _GUARD_REFUSED
    assert "human-owned" in out and "roman" in out


def test_agent_into_other_agents_branch_refused(tmp_path, monkeypatch, capsys):
    """Canon gap fix (spec-agent 2026-09-03): only the OWNER (or a delegate)
    admits — a DIFFERENT agent merging into another agent's owned branch is
    refused, not just the human-owned case."""
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_agent(monkeypatch)  # caller cli-agent
    rc = cmd_policy(["check-merge", "fix/web-agent/thing"])  # owner web-agent (agent) != caller
    out = capsys.readouterr().out
    assert rc == _GUARD_REFUSED
    assert "owned by agent web-agent" in out and "not the owner" in out


def test_agent_self_merge_still_allowed(tmp_path, monkeypatch, capsys):
    """The self-merge special case (caller == owner) stays allowed."""
    _program(tmp_path, monkeypatch, roster=["roman"])
    _as_agent(monkeypatch)  # caller cli-agent
    rc = cmd_policy(["check-merge", "fix/cli-agent/thing"])  # owner cli-agent == caller
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK to merge" in out and "self-admit" in out
