"""init-wizard-mesh-mode — mesh connection mode, end to end (schema + wizard + templates).

The wizard offered `mesh` with zero backing implementation; this closes that gap.
Coverage: MeshParams validation (runner_uri XOR host+port; required-when-mesh),
the wizard mesh prompt branch, and rendered-template assertions for both
launchers (a runner /spawn call is present, no secret material is embedded, and
the mesh branch carries no tmux/ssh).
"""

from __future__ import annotations

import builtins

import pytest

pytest.importorskip("pydantic")

from otaman_cli.init.schema import Connection, MeshParams  # noqa: E402

# ---------------------------------------------------------------------------
# schema — MeshParams + Connection validator


def test_mesh_params_runner_uri_form():
    m = MeshParams(runner_uri="runner://r.mesh:8200")
    assert m.resolved_uri == "runner://r.mesh:8200"
    assert m.http_base == "http://r.mesh:8200"


def test_mesh_params_host_port_form():
    m = MeshParams(host="r.mesh", port=8200)
    assert m.resolved_uri == "runner://r.mesh:8200"
    assert m.http_base == "http://r.mesh:8200"


def test_mesh_params_rejects_both():
    with pytest.raises(ValueError, match="not both"):
        MeshParams(runner_uri="runner://r:8200", host="r", port=8200)


def test_mesh_params_rejects_neither():
    with pytest.raises(ValueError, match="requires runner_uri"):
        MeshParams(token_source="OTAMAN_RUNNER_TOKEN")


def test_connection_requires_mesh_when_mode_mesh():
    with pytest.raises(ValueError, match="requires connection.mesh"):
        Connection(mode="mesh")
    # valid when mesh provided
    c = Connection(mode="mesh", mesh=MeshParams(host="r", port=8200))
    assert c.mesh.http_base == "http://r:8200"


# ---------------------------------------------------------------------------
# wizard — the mesh prompt branch


def test_wizard_mesh_branch(monkeypatch):
    from otaman_cli.init.wizard import run_wizard

    # prompt order: project name, mode, runner host, port, token source, agents, layout
    answers = iter(["", "mesh", "runner.mesh.internal", "", "", "", ""])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(answers))
    settings = run_wizard(project_name="demo")
    assert settings.connection.mode == "mesh"
    assert settings.connection.mesh is not None
    assert settings.connection.mesh.host == "runner.mesh.internal"
    assert settings.connection.mesh.port == 8200  # ce-bootstrap ws-port default
    assert settings.connection.mesh.token_source == "OTAMAN_RUNNER_TOKEN"


# ---------------------------------------------------------------------------
# templates — rendered launcher for mesh mode


def _render(name, mode_settings, agent_repos):
    from otaman_cli.init.generator import _render_template

    return _render_template(name, mode_settings, agent_repos)


def _mesh_settings():
    from otaman_cli.init.schema import AgentEntry, LaunchSettings, TmuxLayoutConfig

    return LaunchSettings(
        version=1,
        connection=Connection(mode="mesh", mesh=MeshParams(host="r.mesh", port=8200)),
        agents=[AgentEntry(name="spec-agent", enabled=True)],
        tmux=TmuxLayoutConfig(session_prefix="demo", layout="tiled"),
    )


@pytest.mark.parametrize("template", ["launch.sh.j2", "launch.ps1.j2"])
def test_mesh_template_posts_spawn_without_embedding_secret(template):
    out = _render(template, _mesh_settings(), {"spec-agent": "../specs"})
    # the runner /spawn call is present against the mesh endpoint
    assert "/spawn" in out
    assert "http://r.mesh:8200" in out
    assert "Bearer" in out
    # the token is read at RUNTIME from the source, never embedded as a literal
    assert "OTAMAN_RUNNER_TOKEN" in out  # the source name, not a value
    # no tmux/ssh in the mesh spawn path (D4)
    assert "tmux new-session" not in out
    assert "ssh -q" not in out
    # bounded request timeout — a black-holed endpoint must fail finite+loud,
    # never hang forever (gate 2.1 finding)
    if template.endswith(".sh.j2"):
        assert "--max-time" in out and "--connect-timeout" in out
        assert "|| echo 000" not in out  # the double-000 cosmetic bug is gone
    else:
        assert "-TimeoutSec" in out


def test_ssh_mode_still_renders_tmux():
    from otaman_cli.init.schema import AgentEntry, LaunchSettings, SSHParams, TmuxLayoutConfig

    settings = LaunchSettings(
        version=1,
        connection=Connection(mode="ssh", ssh=SSHParams(host="h", user="u")),
        agents=[AgentEntry(name="spec-agent", enabled=True)],
        tmux=TmuxLayoutConfig(session_prefix="demo", layout="tiled"),
    )
    out = _render("launch.sh.j2", settings, {"spec-agent": "../specs"})
    assert "tmux" in out and "/spawn" not in out  # unchanged ssh path
