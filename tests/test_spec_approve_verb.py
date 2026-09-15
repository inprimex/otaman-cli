"""ratify-spec-approve-split 1.3 — `otaman spec approve <change>`.

The pmeets tenant was hard-blocked because `spec-approved` was mintable ONLY
from the console b-screen: a program whose human works from a terminal had no
path to it at all, and under `enforcement=block` that is a dead stop "fixed" by
hand-editing `.openspec.yaml` (4/4 of their ratifications). This verb closes it
by delegating to the console's own `advance_to_spec_approved`, so the two
interfaces run the same rules and cannot drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.commands.spec import _cmd_approve, agent_actor_refusal, cmd_spec


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program with a specs repo, one authored change, and a roster approver."""
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "specs": {"path": "../specs"},
                "human-roster": [{"name": "roman", "email": "r@x.io", "roles": ["cto"]}],
            }
        ),
        encoding="utf-8",
    )
    changes = tmp_path / "specs" / "openspec" / "changes" / "my-change"
    changes.mkdir(parents=True)
    (changes / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    (changes / "proposal.md").write_text("# Proposal\n", encoding="utf-8")
    (changes / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    return root, changes


def _stage(change_dir: Path) -> str:
    return (yaml.safe_load((change_dir / ".openspec.yaml").read_text(encoding="utf-8")) or {}).get(
        "stage", ""
    )


# ---------------------------------------------------------------------------
# the verb is registered and discoverable


def test_approve_is_a_known_spec_action():
    from otaman_cli.commands.spec import _ACTIONS

    assert "approve" in _ACTIONS


def test_spec_help_lists_approve(capsys):
    cmd_spec(["--help"])
    out = capsys.readouterr().out
    assert "approve" in out  # a terminal-only human must be able to FIND it


def test_unknown_action_still_refused(capsys):
    assert cmd_spec(["nonsense"]) == 2
    assert "approve" in capsys.readouterr().out  # names the real actions


# ---------------------------------------------------------------------------
# the happy path — a terminal-only program completes its lifecycle


def test_approve_advances_authored_to_spec_approved(program, capsys):
    root, change_dir = program
    assert _stage(change_dir) == "authored"
    rc = _cmd_approve(root, ["my-change"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert _stage(change_dir) == "spec-approved"
    assert "roman" in out  # names the approver (the audit record)


def test_approve_records_the_reason(program, capsys):
    root, change_dir = program
    rc = _cmd_approve(root, ["my-change", "--reason", "reviewed the delta"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "reviewed the delta" in out
    assert _stage(change_dir) == "spec-approved"


def test_approve_broadcasts_to_the_bus(program):
    """The advance is audited — a derived notification lands on the bus."""
    root, _ = program
    (root / ".agents" / "bus" / "active").mkdir(parents=True, exist_ok=True)
    assert _cmd_approve(root, ["my-change"]) == 0
    msgs = list((root / ".agents" / "bus" / "active").glob("*.md"))
    assert msgs, "advancing to spec-approved must leave an audit trail on the bus"
    assert any("my-change" in m.read_text(encoding="utf-8") for m in msgs)


# ---------------------------------------------------------------------------
# refusals name the failing condition (never a dead end)


def test_refuses_unknown_change(program, capsys):
    root, _ = program
    rc = _cmd_approve(root, ["no-such-change"])
    assert rc == 1
    assert "no-such-change" in capsys.readouterr().out


def test_bare_invocation_prints_usage(program, capsys):
    root, _ = program
    assert _cmd_approve(root, []) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_refuses_flags_without_a_change_name(program, capsys):
    root, _ = program
    assert _cmd_approve(root, ["--reason", "looks fine"]) == 1
    assert "change name" in capsys.readouterr().out.lower()


def test_help_exits_zero(program, capsys):
    root, _ = program
    assert _cmd_approve(root, ["--help"]) == 0
    assert "spec approve" in capsys.readouterr().out


def test_refuses_when_already_spec_approved(program, capsys):
    root, change_dir = program
    (change_dir / ".openspec.yaml").write_text("stage: spec-approved\n", encoding="utf-8")
    rc = _cmd_approve(root, ["my-change"])
    assert rc == 1
    assert "already" in capsys.readouterr().out.lower()


def test_refuses_a_non_authored_stage_naming_it(program, capsys):
    root, change_dir = program
    (change_dir / ".openspec.yaml").write_text("stage: drafting\n", encoding="utf-8")
    rc = _cmd_approve(root, ["my-change"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "drafting" in out  # names the actual stage, not a generic failure
    assert _stage(change_dir) == "drafting"  # no wrong-stage write


def test_refuses_a_human_without_the_hat(program, capsys, monkeypatch):
    root, change_dir = program
    root.joinpath("platform.yaml").write_text(
        yaml.dump(
            {
                "project": "demo",
                "version": "1.0",
                "repos": [],
                "specs": {"path": "../specs"},
                "human-roster": [{"name": "sam", "email": "s@x.io", "roles": ["developer"]}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_HUMAN", "sam")
    rc = _cmd_approve(root, ["my-change"])
    out = capsys.readouterr().out.lower()
    assert rc == 1
    assert "approver" in out or "cto" in out  # names the missing hat
    assert _stage(change_dir) == "authored"  # unchanged


# ---------------------------------------------------------------------------
# human-only: an agent may never mint a human attestation


def test_agent_session_is_refused(program, capsys, monkeypatch):
    root, change_dir = program
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    monkeypatch.setattr(
        "otaman_cli.identity.resolve_agent_identity", lambda *a, **k: "cli-agent", raising=False
    )
    rc = _cmd_approve(root, ["my-change"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "cli-agent" in out and "agents cannot" in out.lower()
    assert _stage(change_dir) == "authored"  # never written by an agent


def test_agent_actor_refusal_none_when_human_present(program):
    root, _ = program  # fixture sets OTAMAN_HUMAN=roman
    assert agent_actor_refusal(root) is None


def test_agent_actor_refusal_names_missing_human(program, monkeypatch):
    root, _ = program
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    monkeypatch.setattr(
        "otaman_cli.identity.resolve_agent_identity", lambda *a, **k: None, raising=False
    )
    msg = agent_actor_refusal(root)
    assert msg and "OTAMAN_HUMAN" in msg
