"""policy-engine 2.1 — `otaman policy apply` (generate-and-diff, cto gate, HUMAN-DECISION).

apply computes the same plan as diff, then: requires the roster `cto` role;
requires a HUMAN-DECISION when it would tighten a human-owned branch; and writes
the plan to policy/generated/branch-protection.json for deploy (step 3) to push
live. cli never PUTs protection itself. gh + human-decision are monkeypatched.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli import safety  # noqa: E402
from otaman_cli.commands import policy  # noqa: E402
from otaman_cli.commands.policy import cmd_policy  # noqa: E402


def _program(tmp_path, monkeypatch, repos, roster=()):
    (tmp_path / ".agents").mkdir()
    lines = ["project: shop", "version: '1.0'", "policies:", "  git: standard", "repos:"]
    for r in repos:
        (tmp_path / r["path"]).mkdir(parents=True, exist_ok=True)
        lines.append(f"  - {{name: {r['name']}, path: {r['path']}, owner: {r['owner']}}}")
    if roster:
        lines.append("human-roster:")
        for name, roles in roster:
            lines.append(f"  - {{name: {name}, email: {name}@x.com, roles: [{', '.join(roles)}]}}")
    (tmp_path / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    return tmp_path


def _mock_gh(monkeypatch, *, live, contexts=("ci-ok",), slug="inprimex/foo"):
    from otaman_core import git_host

    monkeypatch.setattr(
        git_host,
        "detect_remote_for_repo",
        lambda p: types.SimpleNamespace(provider="github", slug=slug),
    )
    monkeypatch.setattr(policy, "_default_branch", lambda s: "main")
    monkeypatch.setattr(policy, "_read_live_protection", lambda s, b: live)
    monkeypatch.setattr(policy, "_live_check_contexts", lambda s, b: list(contexts))


def _plan_path(root):
    return root / "policy" / "generated" / "branch-protection.json"


def test_apply_all_conformant_writes_nothing(tmp_path, monkeypatch, capsys):
    root = _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(
        monkeypatch,
        live={
            "allow_force_pushes": {"enabled": False},
            "required_status_checks": {"contexts": ["ci-ok"]},
        },
    )
    rc = cmd_policy(["apply"])
    assert rc == 0
    assert "nothing to apply" in capsys.readouterr().out
    assert not _plan_path(root).exists()


def test_apply_dry_run_previews_without_cto_or_write(tmp_path, monkeypatch, capsys):
    root = _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None)  # unprotected → create-from-nothing
    rc = cmd_policy(["apply", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in out and "create-from-nothing" in out
    assert not _plan_path(root).exists()  # dry-run never writes


def test_apply_refuses_non_cto(tmp_path, monkeypatch, capsys):
    root = _program(tmp_path, monkeypatch, [{"name": "foo", "path": "foo", "owner": "cli-agent"}])
    _mock_gh(monkeypatch, live=None)
    # no OTAMAN_HUMAN resolved → not cto
    rc = cmd_policy(["apply"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "cto" in out
    assert not _plan_path(root).exists()


def test_apply_cto_agent_owned_writes_plan(tmp_path, monkeypatch, capsys):
    root = _program(
        tmp_path,
        monkeypatch,
        [{"name": "foo", "path": "foo", "owner": "cli-agent"}],
        roster=[("roman", ["cto"])],
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _mock_gh(monkeypatch, live=None)  # agent-owned, unprotected → tighten, no human-decision needed
    rc = cmd_policy(["apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote apply plan" in out
    plan = json.loads(_plan_path(root).read_text(encoding="utf-8"))
    assert plan["acting_cto"] == "roman"
    assert plan["repos"][0]["repo"] == "foo" and plan["repos"][0]["owner_kind"] == "agent"


def test_apply_human_owned_requires_human_decision(tmp_path, monkeypatch, capsys):
    root = _program(
        tmp_path,
        monkeypatch,
        [{"name": "site", "path": "site", "owner": "roman"}],
        roster=[("roman", ["cto"])],
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _mock_gh(monkeypatch, live=None)  # human-owned + would tighten → HUMAN-DECISION
    monkeypatch.setattr(safety, "confirm_human_decision", lambda desc, **k: False)
    rc = cmd_policy(["apply"])
    assert rc == 2
    assert not _plan_path(root).exists()  # refused → no plan


def test_apply_human_owned_confirmed_writes_plan(tmp_path, monkeypatch):
    root = _program(
        tmp_path,
        monkeypatch,
        [{"name": "site", "path": "site", "owner": "roman"}],
        roster=[("roman", ["cto"])],
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _mock_gh(monkeypatch, live=None)
    monkeypatch.setattr(safety, "confirm_human_decision", lambda desc, **k: True)
    rc = cmd_policy(["apply"])
    assert rc == 0
    plan = json.loads(_plan_path(root).read_text(encoding="utf-8"))
    assert plan["repos"][0]["owner_kind"] == "human"
