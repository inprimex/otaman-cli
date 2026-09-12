"""identity-divergence-hardening 1.5 — the four surfaces that let a phantom
agent live for 17 hours in the pmeets tenant.

1.1 unknown repo owner is an ERROR (validate + doctor share one predicate)
1.2 `project assign` scaffolds the launch block, or refuses naming what's missing
1.4 doctor WARNs on a tmux server-global OTAMAN_AGENT; doctor reports and
    cleanup reaps orphaned `.agents/status/*.yaml`
"""

from __future__ import annotations

from pathlib import Path

import yaml

from otaman_cli.cleanup_bus import reap_orphan_status_files
from otaman_cli.doctor import (
    check_orphan_status_files,
    check_repo_owner_registration,
    check_tmux_global_agent_env,
    find_orphan_status_files,
)
from otaman_cli.project.launch_scaffold import (
    build_launch_block,
    derive_title,
    inject_agent_env,
    owner_refusal,
)


def _program(tmp_path: Path, agents: list[str] | None = None) -> Path:
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\nrepos: []\n", encoding="utf-8")
    if agents is not None:
        root.joinpath(".agents", "agents.yaml").write_text(
            yaml.dump({"agents": [{"name": a, "role": "developer"} for a in agents]}),
            encoding="utf-8",
        )
    return root


def _status(root: Path, agent: str) -> Path:
    d = root / ".agents" / "status"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{agent}.yaml"
    p.write_text(f"agent: {agent}\nstate: working\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1.1 — repos[].owner must be a registered agent


def test_unknown_owner_is_an_error_naming_repo_and_owner():
    repos = [{"name": "svc", "owner": "worker-agent"}]
    r = check_repo_owner_registration(repos, {"core-agent", "cli-agent"})
    assert r["status"] == "fail"  # ERROR, not a warning
    issue = r["issues"][0]
    assert "svc" in issue["issue"] and "worker-agent" in issue["issue"]
    assert "agents.yaml" in issue["fix"]


def test_registered_owner_is_ok():
    r = check_repo_owner_registration([{"name": "svc", "owner": "core-agent"}], {"core-agent"})
    assert r["status"] == "ok" and not r.get("issues")


def test_owner_check_is_a_noop_without_a_registry():
    # no agents.yaml (empty set) → nothing to validate against, never a failure
    r = check_repo_owner_registration([{"name": "svc", "owner": "whoever"}], set())
    assert r["status"] == "ok"


def test_owner_check_reports_every_unknown_owner():
    repos = [
        {"name": "a", "owner": "core-agent"},
        {"name": "b", "owner": "ghost-one"},
        {"name": "c", "owner": "ghost-two"},
    ]
    r = check_repo_owner_registration(repos, {"core-agent"})
    assert r["status"] == "fail"
    assert len(r["issues"]) == 2
    assert r["details"]["unknown_owners"] == ["b -> ghost-one", "c -> ghost-two"]


def test_owner_check_tolerates_missing_and_malformed_entries():
    repos = [{"name": "a"}, "not-a-dict", {"name": "b", "owner": ""}]
    assert check_repo_owner_registration(repos, {"core-agent"})["status"] == "ok"


def test_validate_reports_unknown_owner(tmp_path, capsys):
    from otaman_cli.commands.misc_readonly import _validate_repo_owners

    root = _program(tmp_path, agents=["core-agent"])
    cfg = root / "platform.yaml"
    cfg.write_text(
        yaml.dump({"project": "demo", "repos": [{"name": "svc", "owner": "worker-agent"}]}),
        encoding="utf-8",
    )
    rc = _validate_repo_owners(cfg)
    out = capsys.readouterr().out
    assert rc == 1
    assert "svc" in out and "worker-agent" in out


def test_validate_owner_check_passes_for_registered(tmp_path):
    from otaman_cli.commands.misc_readonly import _validate_repo_owners

    root = _program(tmp_path, agents=["core-agent"])
    cfg = root / "platform.yaml"
    cfg.write_text(
        yaml.dump({"project": "demo", "repos": [{"name": "svc", "owner": "core-agent"}]}),
        encoding="utf-8",
    )
    assert _validate_repo_owners(cfg) == 0


def test_validate_owner_check_is_silent_without_registry(tmp_path):
    from otaman_cli.commands.misc_readonly import _validate_repo_owners

    root = _program(tmp_path, agents=None)  # no agents.yaml at all
    cfg = root / "platform.yaml"
    cfg.write_text(
        yaml.dump({"project": "demo", "repos": [{"name": "svc", "owner": "anyone"}]}),
        encoding="utf-8",
    )
    assert _validate_repo_owners(cfg) == 0


# ---------------------------------------------------------------------------
# 1.2 — assign scaffolds the launch block (or refuses)


def test_owner_refusal_names_the_missing_piece():
    msg = owner_refusal("worker-agent", {"core-agent", "cli-agent"})
    assert msg is not None
    assert "worker-agent" in msg
    assert "cli-agent" in msg and "core-agent" in msg  # what IS registered
    assert "agents.yaml" in msg


def test_owner_refusal_none_for_registered_or_no_registry():
    assert owner_refusal("core-agent", {"core-agent"}) is None
    assert owner_refusal("anyone", set()) is None  # no registry → no refusal


def test_launch_block_carries_agent_env():
    block = build_launch_block({"repos": []}, "otaman-svc", "svc-agent")
    assert block["commands"], "a launch block without commands is not launchable"
    assert "OTAMAN_AGENT=svc-agent" in block["commands"][0]
    assert block["title"] == "Svc"


def test_launch_block_copies_sibling_conventions():
    data = {
        "repos": [
            {
                "name": "otaman-core",
                "launch": {
                    "title": "Core",
                    "shell": "ssh",
                    "commands": [
                        "source ~/.nvm/nvm.sh && OTAMAN_AGENT=core-agent claude "
                        "--continue --plugin-dir ~/.otaman/otaman-plugin-tree"
                    ],
                },
            }
        ]
    }
    block = build_launch_block(data, "otaman-svc", "svc-agent")
    cmd = block["commands"][0]
    assert block["shell"] == "ssh"  # program convention carried over
    assert "source ~/.nvm/nvm.sh" in cmd and "otaman-plugin-tree" in cmd
    assert "OTAMAN_AGENT=svc-agent" in cmd
    assert "core-agent" not in cmd  # the sibling's identity never leaks


def test_launch_scaffold_is_idempotent_on_reassign():
    data = {"repos": []}
    first = build_launch_block(data, "otaman-svc", "svc-agent")
    second = build_launch_block(data, "otaman-svc", "svc-agent", existing=first)
    assert second["commands"] == first["commands"]
    # and a re-assign to a NEW owner rewrites rather than doubling the env
    third = build_launch_block(data, "otaman-svc", "other-agent", existing=first)
    assert "OTAMAN_AGENT=other-agent" in third["commands"][0]
    assert "OTAMAN_AGENT=svc-agent" not in third["commands"][0]
    assert third["commands"][0].count("OTAMAN_AGENT=") == first["commands"][0].count(
        "OTAMAN_AGENT="
    )


def test_existing_block_keeps_hand_tuning():
    existing = {"title": "Custom", "color": "#ABCDEF", "shell": "local", "commands": ["claude -c"]}
    block = build_launch_block({"repos": []}, "otaman-svc", "svc-agent", existing=existing)
    assert block["title"] == "Custom" and block["color"] == "#ABCDEF"
    assert block["commands"] == ["OTAMAN_AGENT=svc-agent claude -c"]


def test_inject_agent_env_rewrites_every_claude_call():
    cmd = "OTAMAN_AGENT=old claude --continue || OTAMAN_AGENT=old claude /otaman:check"
    out = inject_agent_env(cmd, "new")
    assert out.count("OTAMAN_AGENT=new") == 2 and "old" not in out


def test_derive_title_strips_program_prefix():
    assert derive_title("otaman-cli") == "Cli"
    assert derive_title("otaman-my-svc") == "My Svc"
    assert derive_title("standalone") == "Standalone"


# ---------------------------------------------------------------------------
# 1.4 — server-global identity WARN


def _fake_run(rc: int, out: str):
    return lambda cmd, timeout=10: (rc, out, "")


def test_server_global_agent_env_warns(monkeypatch):
    import otaman_cli.doctor as d

    monkeypatch.setattr(d, "_which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(d, "_run", _fake_run(0, "OTAMAN_AGENT=core-agent\n"))
    r = check_tmux_global_agent_env()
    assert r["status"] == "warn"
    assert "core-agent" in r["issues"][0]["issue"]
    assert r["issues"][0]["fix"] == "tmux set-environment -g -u OTAMAN_AGENT"


def test_no_warn_when_unset_or_no_server(monkeypatch):
    import otaman_cli.doctor as d

    monkeypatch.setattr(d, "_which", lambda name: "/usr/bin/tmux")
    # tmux marks an unset global as "-OTAMAN_AGENT"
    monkeypatch.setattr(d, "_run", _fake_run(0, "-OTAMAN_AGENT\n"))
    assert check_tmux_global_agent_env()["status"] == "ok"
    # no server running at all
    monkeypatch.setattr(d, "_run", _fake_run(1, ""))
    assert check_tmux_global_agent_env()["status"] == "ok"


def test_no_warn_when_tmux_absent(monkeypatch):
    import otaman_cli.doctor as d

    monkeypatch.setattr(d, "_which", lambda name: None)
    assert check_tmux_global_agent_env()["status"] == "ok"


# ---------------------------------------------------------------------------
# 1.4 — orphan status files: doctor reports, cleanup reaps


def test_doctor_reports_orphan_status_file(tmp_path):
    root = _program(tmp_path, agents=["core-agent"])
    _status(root, "core-agent")
    _status(root, "worker-agent")  # the phantom
    r = check_orphan_status_files(root, {"core-agent"})
    assert r["status"] == "warn"
    assert r["details"]["orphans"] == ["worker-agent.yaml"]
    assert "worker-agent" in r["issues"][0]["issue"]


def test_no_orphans_when_all_registered(tmp_path):
    root = _program(tmp_path, agents=["core-agent"])
    _status(root, "core-agent")
    assert check_orphan_status_files(root, {"core-agent"})["status"] == "ok"


def test_orphan_scan_skips_temp_files_and_empty_registry(tmp_path):
    root = _program(tmp_path, agents=["core-agent"])
    d = root / ".agents" / "status"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".core-agent.abc123.yaml.tmp").write_text("x", encoding="utf-8")
    assert find_orphan_status_files(root, {"core-agent"}) == []
    # an empty registry can't tell orphan from legitimate
    _status(root, "worker-agent")
    assert find_orphan_status_files(root, set()) == []


def test_human_status_file_is_never_an_orphan(tmp_path):
    """`human` is a first-class identity (bus recipient, AFK state writer, init
    routing target) that is deliberately NOT an agents.yaml entry. Reaping it
    would delete live AFK state — caught against the live fleet, where
    `human.yaml` was the ONLY status file absent from the registry."""
    root = _program(tmp_path, agents=["core-agent"])
    human = _status(root, "human")
    assert find_orphan_status_files(root, {"core-agent"}) == []
    assert check_orphan_status_files(root, {"core-agent"})["status"] == "ok"
    assert reap_orphan_status_files(root) == []
    assert human.exists()


def test_cleanup_reaps_orphan_naming_it(tmp_path):
    root = _program(tmp_path, agents=["core-agent"])
    keep = _status(root, "core-agent")
    phantom = _status(root, "worker-agent")
    reaped = reap_orphan_status_files(root)
    assert reaped == ["worker-agent.yaml"]  # names what it removed
    assert not phantom.exists()
    assert keep.exists()  # registered agent untouched


def test_cleanup_dry_run_reports_without_removing(tmp_path):
    root = _program(tmp_path, agents=["core-agent"])
    phantom = _status(root, "worker-agent")
    assert reap_orphan_status_files(root, dry_run=True) == ["worker-agent.yaml"]
    assert phantom.exists()  # dry run never deletes


def test_cleanup_report_carries_status_orphans(tmp_path):
    from otaman_cli.cleanup_bus import cleanup

    root = _program(tmp_path, agents=["core-agent"])
    (root / ".agents" / "bus" / "active").mkdir(parents=True)
    _status(root, "worker-agent")
    report = cleanup(root)
    assert report["status_orphans"] == ["worker-agent.yaml"]


def test_orphan_reap_survives_a_missing_bus_dir(tmp_path):
    from otaman_cli.cleanup_bus import cleanup

    root = _program(tmp_path, agents=["core-agent"])
    phantom = _status(root, "worker-agent")
    report = cleanup(root)  # no bus dir at all
    assert report["status_orphans"] == ["worker-agent.yaml"]
    assert not phantom.exists()
