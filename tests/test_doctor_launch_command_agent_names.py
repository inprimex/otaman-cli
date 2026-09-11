"""identity-divergence D3 — launch commands must set OTAMAN_AGENT to an agent
name from agents.yaml, not a repo name (the phantom-agent drift class)."""

from __future__ import annotations

from otaman_cli.doctor import check_launch_command_agent_names

_KNOWN = {"cli-agent", "core-agent"}


def _repo(name, cmd):
    return {"name": name, "launch_commands": [cmd]}


def test_agent_name_is_ok():
    r = check_launch_command_agent_names(
        [_repo("otaman-cli", "OTAMAN_AGENT=cli-agent claude -c")], _KNOWN
    )
    assert r["status"] == "ok"


def test_repo_name_drift_is_flagged():
    # a launch command that sets OTAMAN_AGENT to a repo name (not an agent)
    r = check_launch_command_agent_names(
        [_repo("otaman-cli", "OTAMAN_AGENT=otaman-cli claude -c")], _KNOWN
    )
    assert r["status"] == "warn"
    assert "otaman-cli" in r["issues"][0]["issue"]
    assert "not an agent" in r["issues"][0]["issue"]


def test_nested_launch_commands_form_is_checked():
    repo = {"name": "r", "launch": {"commands": ["OTAMAN_AGENT=ghost-agent claude"]}}
    r = check_launch_command_agent_names([repo], _KNOWN)
    assert r["status"] == "warn" and "ghost-agent" in r["issues"][0]["issue"]


def test_no_known_agents_is_noop():
    # without agents.yaml there's nothing to validate against
    r = check_launch_command_agent_names(
        [_repo("otaman-cli", "OTAMAN_AGENT=anything claude")], set()
    )
    assert r["status"] == "ok"


def test_no_otaman_agent_prefix_is_ok():
    r = check_launch_command_agent_names([_repo("r", "claude -c --plugin-dir x")], _KNOWN)
    assert r["status"] == "ok"
