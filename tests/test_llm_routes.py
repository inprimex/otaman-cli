"""llm-router-backend 1.4, cli half — the route surfaces over core's resolution.

Core shipped the seam as #99 and answered the question I asked before building
(20261001T135210): **route resolution is core's.** `effective_route(config, agent)`
is the single resolution point, so this surface and the bridge's dispatch cannot
disagree about which route an agent is on.

That is the first guard below, and it is not pedantry. What the bridge enforces at
dispatch is a sensitivity guard: a doctor view that resolved `agents[].route`
itself could show a human a route the guard would refuse, and the human would
believe the view.

The three rendering positions, each the opposite of a plausible shortcut:

* an agent with no route is RENDERED, not omitted — "default path", "not
  configured" and "I did not look" are three facts and an absent row is all three
* an unreadable `router:` block is NOT CHECKED, never "no routing configured"
* a route that leaves the tenant under a local-only class is reported BEFORE
  dispatch, because a config finding beats a runtime refusal
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("otaman_core.llm_router")

from otaman_cli import llm_routes  # noqa: E402


def _program(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "prog"
    root.mkdir(exist_ok=True)
    (root / "platform.yaml").write_text(body, encoding="utf-8")
    return root


ROUTED = """\
project: demo
version: '1.0'
router:
  backend: litellm-proxy
  base_url: http://localhost:4000
  local_only_classes: [cofounder-only]
agents:
  - name: cli-agent
    route: {family: llama, model: llama3, local: true}
  - name: core-agent
    route: anthropic
  - name: web-agent
"""


# ---------------------------------------------------------------------------
# Resolution is core's.


def test_resolution_is_delegated_to_core_not_reimplemented(tmp_path, monkeypatch):
    """The guard core asked for. Make core's resolver return something the raw
    field does not say, and require the surface to follow core.

    A surface that parsed `agents[].route` itself passes every other test in this
    file and fails this one — which is the failure mode that matters, because the
    bridge enforces a sensitivity guard off the same resolution."""
    import otaman_core.llm_router as core

    root = _program(tmp_path, ROUTED)
    fake = core.Route(family="something-else", model="m9", local=False)
    monkeypatch.setattr(core, "effective_route", lambda config, agent: fake)

    surface = llm_routes.load(root)

    assert [r.family for r in surface.routes] == ["something-else"] * 3
    assert all(r.local is False for r in surface.routes)


def test_the_module_never_parses_the_raw_route_field():
    """Structural, so the delegation cannot be quietly undone by a later edit."""
    src = Path(llm_routes.__file__).read_text(encoding="utf-8")
    assert '"route"' not in src, "reading agents[].route here is a second resolution point"
    assert "effective_route" in src


def test_the_tenant_question_is_cores_bit_not_a_local_guess():
    """`local` comes off core's Route; nothing here infers residency from a family
    name or a base_url."""
    src = Path(llm_routes.__file__).read_text(encoding="utf-8")
    assert "route_leaves_tenant" in src or "route.local" in src


# ---------------------------------------------------------------------------
# Rendering: an agent with no route is a row, not an absence.


def test_an_agent_with_no_route_is_rendered_as_the_default_path(tmp_path):
    root = _program(tmp_path, ROUTED)
    surface = llm_routes.load(root)

    by_agent = {r.agent: r for r in surface.routes}
    assert set(by_agent) == {"cli-agent", "core-agent", "web-agent"}
    assert by_agent["web-agent"].default_path is True
    assert by_agent["web-agent"].label == llm_routes.NO_ROUTE

    rendered = "\n".join(llm_routes.render_lines(surface))
    assert "web-agent" in rendered, "an agent missing from the listing is indistinguishable"


def test_a_local_route_and_a_cloud_route_read_differently(tmp_path):
    root = _program(tmp_path, ROUTED)
    by_agent = {r.agent: r for r in llm_routes.load(root).routes}

    assert "local" in by_agent["cli-agent"].label
    assert "leaves tenant" in by_agent["core-agent"].label


def test_a_bare_string_route_resolves_to_a_family(tmp_path):
    root = _program(tmp_path, ROUTED)
    by_agent = {r.agent: r for r in llm_routes.load(root).routes}

    assert by_agent["core-agent"].family == "anthropic"
    assert by_agent["core-agent"].default_path is False


def test_the_mapping_agents_shape_is_read_too(tmp_path):
    """core accepts both shapes. A surface that only understood the list form would
    silently show zero agents for a program using the mapping form."""
    root = _program(
        tmp_path,
        "project: demo\nversion: '1.0'\nagents:\n  cli-agent:\n    route: llama\n",
    )
    surface = llm_routes.load(root)

    assert [r.agent for r in surface.routes] == ["cli-agent"]
    assert surface.routes[0].family == "llama"


def test_a_single_agent_can_be_asked_for(tmp_path):
    root = _program(tmp_path, ROUTED)
    surface = llm_routes.load(root, agent="core-agent")

    assert [r.agent for r in surface.routes] == ["core-agent"]


# ---------------------------------------------------------------------------
# Opt-in means zero change, and it is SAID.


def test_no_router_block_is_the_native_path_stated_not_inferred(tmp_path):
    """The spec's first scenario. `configured` False is not an error state."""
    root = _program(tmp_path, "project: demo\nversion: '1.0'\nagents: []\n")
    surface = llm_routes.load(root)

    assert surface.error is None
    assert surface.configured is False
    rendered = "\n".join(llm_routes.render_lines(surface))
    assert "native path, unchanged" in rendered
    assert "to declare a route" in rendered, "the surface must say how to opt in"


# ---------------------------------------------------------------------------
# Unreadable is not unconfigured.


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("project: demo\nrouter: [: : :\n", "could not be read"),
        ("just a string\n", "not a mapping"),
        ("project: demo\nrouter:\n  backend: nope\n", "invalid"),
        ("project: demo\nrouter:\n  backend: litellm-proxy\n", "invalid"),
    ],
)
def test_an_unresolvable_config_is_not_checked_never_unconfigured(tmp_path, body, fragment):
    root = _program(tmp_path, body)
    surface = llm_routes.load(root)

    assert surface.error is not None, "this must not read as an opt-out"
    assert fragment in surface.error
    assert llm_routes.NOT_CHECKED in llm_routes.render_lines(surface)[0]


def test_an_absent_platform_yaml_is_not_checked(tmp_path):
    root = tmp_path / "nothing"
    root.mkdir()
    surface = llm_routes.load(root)

    assert surface.error is not None and "platform.yaml" in surface.error


def test_a_bundle_without_the_seam_cannot_resolve_and_does_not_claim_otherwise(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(llm_routes, "_core", lambda: None)
    surface = llm_routes.load(_program(tmp_path, ROUTED))

    assert surface.error is not None
    assert "llm-router-backend 1.1" in surface.error
    assert surface.configured is False


def test_one_bad_route_declaration_is_named_not_fatal(tmp_path):
    """A single malformed declaration must not blank the whole listing — the other
    agents' routes are still facts a reader needs."""
    root = _program(
        tmp_path,
        "project: demo\nversion: '1.0'\nagents:\n"
        "  - name: good\n    route: llama\n"
        "  - name: bad\n    route: 42\n",
    )
    surface = llm_routes.load(root)

    assert surface.error is None
    labels = {r.agent for r in surface.routes}
    assert "good" in labels
    assert any("[invalid:" in a for a in labels)


# ---------------------------------------------------------------------------
# The guard finding, before dispatch.


def test_a_cloud_route_under_a_local_only_class_is_reported(tmp_path):
    """Every guarded call on that agent refuses at dispatch (bridge 1.3). A config
    finding beats a runtime refusal."""
    root = _program(tmp_path, ROUTED)
    surface = llm_routes.load(root)

    guarded = [r.agent for r in surface.guarded_routes]
    assert guarded == ["core-agent"], "the local route and the unrouted agent are not guarded"
    rendered = "\n".join(llm_routes.render_lines(surface))
    assert "refuse at dispatch" in rendered


def test_no_local_only_classes_means_no_route_is_guarded(tmp_path):
    """Without a declared class there is nothing to guard, so a cloud route is
    simply a cloud route — warning on it would be noise."""
    root = _program(
        tmp_path,
        "project: demo\nversion: '1.0'\nrouter:\n"
        "  backend: litellm-proxy\n  base_url: http://x\n"
        "agents:\n  - name: a\n    route: anthropic\n",
    )
    surface = llm_routes.load(root)

    assert surface.guarded_routes == []
    assert "no route is guarded" in "\n".join(llm_routes.render_lines(surface))


# ---------------------------------------------------------------------------
# The doctor check.


def test_the_doctor_check_reports_the_effective_route(tmp_path):
    from otaman_cli.doctor import check_llm_routing

    result = check_llm_routing(_program(tmp_path, ROUTED))

    assert result["check"] == "llm_routing"
    assert result["details"]["backend"] == "litellm-proxy"
    assert result["details"]["routed"] == 2
    assert "core-agent" in result["details"]["effective"]
    assert result["status"] == "warn", "the guarded cloud route must surface"
    assert any("refuse at dispatch" in i["message"] for i in result["issues"])


def test_the_doctor_check_is_ok_on_an_opted_out_program(tmp_path):
    from otaman_cli.doctor import check_llm_routing

    result = check_llm_routing(_program(tmp_path, "project: demo\nagents: []\n"))

    assert result["status"] == "ok"
    assert "unchanged" in result["details"]["routing"]


def test_the_doctor_check_warns_not_checked_on_an_unreadable_block(tmp_path):
    from otaman_cli.doctor import check_llm_routing

    result = check_llm_routing(_program(tmp_path, "project: demo\nrouter: [: : :\n"))

    assert result["status"] == "warn"
    assert "NOT CHECKED" in result["details"]["routing"]


def test_the_doctor_check_is_in_the_run(tmp_path):
    """A check nothing calls is a function, not a check."""
    src = Path(__import__("otaman_cli.doctor", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert "check_llm_routing(project_root)," in src


# ---------------------------------------------------------------------------
# The command surface.


def test_the_command_renders_and_exits_zero(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, ROUTED)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    assert policy_cmd.cmd_policy(["routes"]) == 0
    out = capsys.readouterr().out

    assert "litellm-proxy" in out
    assert "web-agent" in out
    assert "refuse at dispatch" in out


def test_the_command_exits_two_when_routing_cannot_be_resolved(tmp_path, monkeypatch, capsys):
    """Non-zero, because a caller must not read an unresolvable config as an
    unconfigured one."""
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, "project: demo\nrouter: [: : :\n")
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    assert policy_cmd.cmd_policy(["routes"]) == 2
    out = capsys.readouterr().out
    assert "not 'no routing configured'" in out


def test_the_json_form_carries_the_same_facts(tmp_path, monkeypatch, capsys):
    import json

    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, ROUTED)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    assert policy_cmd.cmd_policy(["routes", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["backend"] == "litellm-proxy"
    assert data["guarded"] == ["core-agent"]
    assert {r["agent"] for r in data["routes"]} == {"cli-agent", "core-agent", "web-agent"}
    assert data["error"] is None


def test_the_action_is_advertised_in_the_usage(capsys):
    from otaman_cli.commands import policy as policy_cmd

    policy_cmd.cmd_policy([])
    assert "policy routes" in capsys.readouterr().out
