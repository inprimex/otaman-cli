"""policy-engine 4.2 — `otaman policy annotate` (PR admissibility annotation).

Read-only, ALWAYS exit 0 — a display line for a PR comment / CI annotation, not
a gate (the gate is check-merge). Owner intent from convention + branch-owners.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands.policy import cmd_policy  # noqa: E402


def _program(tmp_path, monkeypatch, roster=("roman",)):
    (tmp_path / ".agents").mkdir()
    lines = ["project: shop", "version: '1.0'", "policies:", "  git: standard", "repos: []"]
    if roster:
        lines.append("human-roster:")
        for h in roster:
            lines.append(f"  - {{name: {h}, email: {h}@x.com, roles: [cto]}}")
    (tmp_path / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return tmp_path


def test_annotate_owner_less(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch)
    rc = cmd_policy(["annotate", "scratchpad"])
    out = capsys.readouterr().out
    assert rc == 0  # annotation, never a gate
    assert "UNADMITTABLE" in out and "owner-less" in out


def test_annotate_human_owned(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    rc = cmd_policy(["annotate", "feat/roman/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "roman" in out and "human" in out


def test_annotate_agent_owned(tmp_path, monkeypatch, capsys):
    _program(tmp_path, monkeypatch, roster=["roman"])
    rc = cmd_policy(["annotate", "fix/cli-agent/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agent" in out and "self-merges" in out


def test_annotate_usage_without_branch(tmp_path, monkeypatch):
    _program(tmp_path, monkeypatch)
    assert cmd_policy(["annotate"]) == 2
