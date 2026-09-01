"""Generated launch scripts start real agent sessions (landing 20260817T200932).

The old templates sent the nonexistent `otaman start <agent>` into every
tmux pane — fresh users got empty panes. Panes now cd into the agent's
repo (platform.yaml owner→path, resolved against the meta dir the
launcher/ folder lives in) and run claude; unknown agents or missing dirs
degrade to visible in-pane guidance, never an empty pane.

The POSIX assertions EXECUTE the rendered launch.sh against stubbed
tmux/otaman/claude binaries and inspect what was actually typed into each
pane — behavioral proof, not string matching.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.init.generator import generate
from otaman_cli.init.schema import AgentEntry, Connection, SSHParams
from otaman_cli.init.wizard import default_settings


def _settings(agent_names: list[str], connection: Connection | None = None):
    """default_settings with every agent enabled (extras default to off)."""
    s = default_settings(project_name="proj", extra_agent_names=["spec-agent", *agent_names])
    update: dict = {"agents": [AgentEntry(name=a.name, enabled=True) for a in s.agents]}
    if connection is not None:
        update["connection"] = connection
    return s.model_copy(update=update)


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="executes the rendered bash script"
)


def _meta_with_launcher(tmp_path: Path, agent_repos: dict[str, str]) -> Path:
    """Meta dir + launcher/ + real repo dirs for the mapped agents."""
    meta = tmp_path / "proj-otaman"
    meta.mkdir()
    for rel in agent_repos.values():
        (meta / rel).resolve().mkdir(parents=True, exist_ok=True)
    generate(_settings(list(agent_repos.keys())), meta / "launcher", agent_repos=agent_repos)
    return meta


def _run_launch(meta: Path, tmp_path: Path) -> str:
    """Execute launcher/launch.sh with stubbed tmux/otaman/claude; return the
    send-keys log (one line per pane command)."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    log = tmp_path / "send-keys.log"
    (stub_bin / "tmux").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  has-session) exit 1 ;;\n"  # nothing running yet
        "  new-session) exit 0 ;;\n"
        f'  send-keys) shift; echo "$@" >> "{log}" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    for name in ("otaman", "claude"):
        (stub_bin / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for f in stub_bin.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_bin}:{os.environ['PATH']}"}
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT", "OTAMAN_REPOS_BASE"):
        env.pop(var, None)
    r = subprocess.run(
        ["bash", str(meta / "launcher" / "launch.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return log.read_text(encoding="utf-8") if log.is_file() else ""


@_POSIX_ONLY
def test_pane_cds_into_agent_repo_and_runs_claude(tmp_path: Path):
    meta = _meta_with_launcher(tmp_path, {"backend-agent": "../svc-api"})
    sent = _run_launch(meta, tmp_path)
    assert "otaman start" not in sent
    backend_line = next(
        line for line in sent.splitlines() if "backend-agent" in line or "svc-api" in line
    )
    assert "cd " in backend_line and "svc-api" in backend_line and "claude" in backend_line


@_POSIX_ONLY
def test_unmapped_agent_gets_visible_guidance_not_empty_pane(tmp_path: Path):
    # spec-agent has no repo mapping here -> guidance echo, never empty/broken
    meta = _meta_with_launcher(tmp_path, {"backend-agent": "../svc-api"})
    sent = _run_launch(meta, tmp_path)
    spec_lines = [line for line in sent.splitlines() if "spec-agent" in line]
    assert spec_lines, sent
    assert "echo" in spec_lines[0] and "claude" in spec_lines[0]


@_POSIX_ONLY
def test_mapped_agent_with_missing_dir_gets_guidance(tmp_path: Path):
    meta = _meta_with_launcher(tmp_path, {"backend-agent": "../svc-api"})
    import shutil

    shutil.rmtree((meta / "../svc-api").resolve())
    sent = _run_launch(meta, tmp_path)
    backend_lines = [line for line in sent.splitlines() if "backend-agent" in line]
    assert backend_lines and "echo" in backend_lines[0]
    assert "cd " not in backend_lines[0].split("echo")[0]


def test_sh_and_ps1_no_longer_reference_otaman_start(tmp_path: Path):
    s = _settings([])
    r = generate(s, tmp_path / "launcher", agent_repos={"spec-agent": "../otaman-specs"})
    for f in (r.launch_sh, r.launch_ps1):
        assert "otaman start" not in f.read_text(encoding="utf-8")


def test_ps1_carries_repo_map_and_pane_function(tmp_path: Path):
    s = _settings([])
    r = generate(s, tmp_path / "launcher", agent_repos={"spec-agent": "../otaman-specs"})
    ps1 = r.launch_ps1.read_text(encoding="utf-8")
    assert '"spec-agent" = "../otaman-specs"' in ps1
    assert "Get-PaneCommand" in ps1


def test_ssh_mode_uses_remote_root_and_schema_accepts_it(tmp_path: Path):
    s = _settings(
        [],
        connection=Connection(
            mode="ssh",
            ssh=SSHParams(host="h.example", user="dev", remote_root="/home/dev/orgs/o/proj-otaman"),
        ),
    )
    r = generate(s, tmp_path / "launcher", agent_repos={"spec-agent": "../otaman-specs"})
    sh = r.launch_sh.read_text(encoding="utf-8")
    assert "/home/dev/orgs/o/proj-otaman" in sh
    assert "otaman start" not in sh


def test_ssh_mode_without_remote_root_degrades_to_guidance(tmp_path: Path):
    s = _settings(
        [], connection=Connection(mode="ssh", ssh=SSHParams(host="h.example", user="dev"))
    )
    r = generate(s, tmp_path / "launcher", agent_repos={"spec-agent": "../otaman-specs"})
    sh = r.launch_sh.read_text(encoding="utf-8")
    assert "remote_root" in sh  # guidance names the missing setting
    assert "otaman start" not in sh


def test_generate_without_agent_repos_backward_compatible(tmp_path: Path):
    """Existing callers that don't pass agent_repos keep working — panes get
    guidance instead of the old broken 'otaman start'."""
    s = _settings([])
    r = generate(s, tmp_path / "launcher")
    sh = r.launch_sh.read_text(encoding="utf-8")
    assert "otaman start" not in sh
    assert "pane_cmd" in sh


def test_attach_hint_uses_first_enabled_agent_not_hardcoded(tmp_path: Path):
    """The 'Attach:' hint must name a real enabled agent, not a hardcoded
    'spec-agent' — a program without spec-agent (e.g. a solo-dev
    frontend+backend setup) otherwise gets an attach hint pointing at a
    session that never exists (found servicing landing-agent's docs render)."""
    s = default_settings(
        project_name="todo-app", extra_agent_names=["frontend-agent", "backend-agent"]
    )
    s = s.model_copy(
        update={
            "agents": [
                AgentEntry(name="frontend-agent", enabled=True),
                AgentEntry(name="backend-agent", enabled=True),
            ]
        }
    )
    r = generate(
        s,
        tmp_path / "launcher",
        agent_repos={"frontend-agent": "../fe", "backend-agent": "../be"},
    )
    sh = r.launch_sh.read_text(encoding="utf-8")
    ps1 = r.launch_ps1.read_text(encoding="utf-8")
    # attaches to the first enabled agent
    assert "Attach: tmux attach -t $PREFIX-frontend-agent" in sh
    assert "Attach: tmux attach -t $Prefix-frontend-agent" in ps1
    # and never the old hardcoded spec-agent session
    assert "$PREFIX-spec-agent" not in sh
    assert "$Prefix-spec-agent" not in ps1


def test_attach_hint_first_agent_when_spec_present(tmp_path: Path):
    """When spec-agent IS first (the fleet case), the hint still resolves to it
    — the fix references the first enabled agent, not a constant."""
    s = default_settings(project_name="otaman", extra_agent_names=["cli-agent"])
    s = s.model_copy(
        update={
            "agents": [
                AgentEntry(name="spec-agent", enabled=True),
                AgentEntry(name="cli-agent", enabled=True),
            ]
        }
    )
    r = generate(s, tmp_path / "launcher", agent_repos={"spec-agent": "../otaman-specs"})
    assert "Attach: tmux attach -t $PREFIX-spec-agent" in r.launch_sh.read_text(encoding="utf-8")
