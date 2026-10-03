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


def test_every_label_comes_from_cores_resolution(tmp_path, monkeypatch):
    """The same guard as above, over the WRITE path too.

    This replaced a source-text ban on the string `"route"` appearing in the module.
    That proxy held only while the surface was read-only: `declare` has to read the
    field to tell a re-declaration from a change, and write it — so the ban became
    false about a module that still delegates every DECISION. Behaviour is the real
    rule and a stronger one: make core's resolver say something the raw field does
    not, and require both entry points to follow core.
    """
    import otaman_core.llm_router as core

    root = _program(tmp_path, "project: demo\n")
    _registry(root, "agents:\n  - name: cli-agent\n")
    fake = core.Route(family="something-else", model="m9", local=True)
    monkeypatch.setattr(core, "effective_route", lambda config, agent: fake)

    declared = llm_routes.declare(root, "cli-agent", "ollama", model="llama3")
    assert declared.after == "something-else/m9@local (local)", (
        "the label must be core's — the identifier half is now Route.id (core #121)"
    )
    assert llm_routes.load(root).routes[0].family == "something-else"


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


# ---------------------------------------------------------------------------
# Where the declarations actually live (the defect this surface shipped with).


def _registry(root: Path, body: str) -> Path:
    agents = root / ".agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "agents.yaml").write_text(body, encoding="utf-8")
    return agents / "agents.yaml"


def test_declarations_are_read_from_the_agent_registry(tmp_path):
    """The first version read platform.yaml ALONE and found zero agents on a program
    with nineteen in `.agents/agents.yaml` — so "doctor shows effective routing per
    agent" showed nothing at all on the only program that exists."""
    root = _program(tmp_path, "project: demo\nversion: '1.0'\n")
    _registry(
        root,
        "project: demo\nagents:\n"
        "  - name: cli-agent\n    route: {family: llama, model: llama3, local: true}\n"
        "  - name: core-agent\n",
    )
    surface = llm_routes.load(root)
    assert surface.agents_source == llm_routes.REGISTRY_REL
    assert [r.agent for r in surface.routes] == ["cli-agent", "core-agent"]
    assert surface.routes[0].label == "llama/llama3@local (local)"
    assert surface.routes[1].default_path is True


def test_an_inlined_agents_block_wins_over_the_registry(tmp_path):
    """A program that puts its declarations in platform.yaml means them."""
    root = _program(
        tmp_path,
        "project: demo\nagents:\n  - name: web-agent\n    route: anthropic\n",
    )
    _registry(root, "agents:\n  - name: cli-agent\n    route: llama\n")
    surface = llm_routes.load(root)
    assert surface.agents_source == "platform.yaml"
    assert [r.agent for r in surface.routes] == ["web-agent"]


def test_an_empty_listing_says_both_places_were_checked(tmp_path):
    root = _program(tmp_path, "project: demo\n")
    lines = llm_routes.render_lines(llm_routes.load(root))
    empty = next(line for line in lines if "no agents declared" in line)
    assert "platform.yaml" in empty
    assert llm_routes.REGISTRY_REL in empty


def test_an_unreadable_registry_is_not_a_program_without_agents(tmp_path):
    """It yields no declarations, and the source line does not claim platform.yaml."""
    root = _program(tmp_path, "project: demo\n")
    _registry(root, "agents: [unclosed\n")
    surface = llm_routes.load(root)
    assert surface.routes == []
    assert surface.agents_source == ""


# ---------------------------------------------------------------------------
# declare — the write half of "declare/list routes".


def _with_agent(tmp_path, *, name: str = "cli-agent", extra: str = "") -> Path:
    root = _program(tmp_path, "project: demo\nversion: '1.0'\n")
    _registry(root, f"# hand-edited registry\nproject: demo\nagents:\n  - name: {name}\n{extra}")
    return root


def test_the_written_path_is_posix_on_every_platform(tmp_path):
    """The Windows leg of CI caught this: `str(relative_to(...))` renders
    `.agents\\agents.yaml`, which is right for the OS and wrong for a string printed
    beside `REGISTRY_REL` and copied into messages and config."""
    root = _with_agent(tmp_path)
    result = llm_routes.declare(root, "cli-agent", "ollama")
    assert "\\" not in result.where
    assert result.where == llm_routes.REGISTRY_REL


def test_declare_writes_the_route_into_the_file_that_declares_the_agent(tmp_path):
    root = _with_agent(tmp_path)
    result = llm_routes.declare(root, "cli-agent", "ollama", model="llama3.1", local=True)
    assert result.error == ""
    assert result.changed is True
    assert result.where == ".agents/agents.yaml"
    assert result.before == llm_routes.NO_ROUTE
    assert result.after == "ollama/llama3.1@local (local)"
    # and the surface now resolves it through core
    assert (
        llm_routes.load(root, agent="cli-agent").routes[0].label == "ollama/llama3.1@local (local)"
    )


def test_declare_preserves_the_humans_comments(tmp_path):
    """Written through the round-trip loader, not a reflowing safe_dump."""
    root = _with_agent(tmp_path)
    llm_routes.declare(root, "cli-agent", "ollama")
    text = (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")
    assert "# hand-edited registry" in text


def test_a_family_only_route_is_written_as_a_bare_string(tmp_path):
    """What a human writing it by hand would put there; core accepts both forms."""
    root = _with_agent(tmp_path)
    llm_routes.declare(root, "cli-agent", "anthropic")
    text = (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")
    assert "route: anthropic" in text


def test_re_declaring_the_same_route_reports_no_work(tmp_path):
    """nss — a verb that did nothing must not report success as if it had."""
    root = _with_agent(tmp_path)
    llm_routes.declare(root, "cli-agent", "ollama", model="llama3.1", local=True)
    before = (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")
    again = llm_routes.declare(root, "cli-agent", "ollama", model="llama3.1", local=True)
    assert again.changed is False
    assert again.error == ""
    assert again.after == "ollama/llama3.1@local (local)"
    assert (root / ".agents" / "agents.yaml").read_text(encoding="utf-8") == before


def test_declare_replaces_a_prior_route_and_names_both_ends(tmp_path):
    root = _with_agent(tmp_path, extra="    route: anthropic\n")
    result = llm_routes.declare(root, "cli-agent", "ollama", local=True)
    assert result.before == "anthropic (leaves tenant)"
    assert result.after == "ollama@local (local)"


def test_declare_refuses_an_agent_the_program_does_not_declare(tmp_path):
    """Creating a declaration for a misspelled name would route nobody."""
    root = _with_agent(tmp_path)
    result = llm_routes.declare(root, "nope-agent", "ollama")
    assert result.changed is False
    assert "not declared" in result.error
    assert llm_routes.REGISTRY_REL in result.error
    assert "nope-agent" not in (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")


def test_declare_refuses_a_route_with_no_family(tmp_path):
    root = _with_agent(tmp_path)
    result = llm_routes.declare(root, "cli-agent", "")
    assert "family" in result.error
    assert "route" not in (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")


def test_declare_is_validated_by_core_before_the_write(tmp_path, monkeypatch):
    """Core's parser is the gate, so a refused declaration never reaches the file."""
    root = _with_agent(tmp_path)

    class _Refusing:
        DEFAULT_BACKEND = "default"

        def parse_router_config(self, config):  # pragma: no cover - not reached
            raise AssertionError

        def effective_route(self, config, agent):
            raise ValueError("agents[cli-agent].route.family is required")

        def select_backend(self, config):  # pragma: no cover
            raise AssertionError

        def route_leaves_tenant(self, target):  # pragma: no cover
            raise AssertionError

    monkeypatch.setattr(llm_routes, "_core", lambda: _Refusing())
    result = llm_routes.declare(root, "cli-agent", "ollama")
    assert "refused by core" in result.error
    assert "route" not in (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")


def test_declare_on_a_bundle_without_the_seam_writes_nothing(tmp_path, monkeypatch):
    root = _with_agent(tmp_path)
    monkeypatch.setattr(llm_routes, "_core", lambda: None)
    result = llm_routes.declare(root, "cli-agent", "ollama")
    assert "llm-router seam" in result.error
    assert "route" not in (root / ".agents" / "agents.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the command surface for declare


def _wire(monkeypatch, root):
    import otaman_cli.commands.policy as P

    monkeypatch.setattr(P, "find_project_root", lambda: root)
    return P


def test_the_command_declares_and_names_the_file(tmp_path, monkeypatch, capsys):
    root = _with_agent(tmp_path)
    P = _wire(monkeypatch, root)
    rc = P.cmd_policy(["routes", "--declare", "cli-agent", "--family", "ollama", "--local"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ollama@local (local)" in out
    assert ".agents/agents.yaml" in out
    # The opt-in scenario, stated: a route without a router: block stays native.
    assert "native path" in out


def test_the_command_refuses_an_unknown_agent_with_exit_two(tmp_path, monkeypatch, capsys):
    root = _with_agent(tmp_path)
    P = _wire(monkeypatch, root)
    rc = P.cmd_policy(["routes", "--declare", "nope", "--family", "ollama"])
    assert rc == 2
    assert "Not declared" in capsys.readouterr().out


def test_route_flags_without_declare_are_refused(tmp_path, monkeypatch, capsys):
    """`--family` alone would silently list instead of declaring."""
    root = _with_agent(tmp_path)
    P = _wire(monkeypatch, root)
    rc = P.cmd_policy(["routes", "--family", "ollama"])
    assert rc != 0
    assert "only apply with --declare" in capsys.readouterr().out


def test_the_declare_form_is_advertised_in_the_usage(capsys):
    import otaman_cli.commands.policy as P

    P.cmd_policy(["--help"])
    assert "--declare" in capsys.readouterr().out


def test_the_json_form_names_where_the_declarations_came_from(tmp_path, monkeypatch, capsys):
    import json

    root = _with_agent(tmp_path, extra="    route: anthropic\n")
    P = _wire(monkeypatch, root)
    P.cmd_policy(["routes", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents_source"] == llm_routes.REGISTRY_REL
    assert payload["routes"][0]["family"] == "anthropic"


# ---------------------------------------------------------------------------
# core #121 — the route KEY is core's `Route.id`, the suffix is cli's display


def test_the_key_is_cores_route_id_verbatim(tmp_path):
    """cli must not compose the identifier half any more.

    plugin's lrb-1.6 call site needed a string for `record_critic_cost(route=...)`,
    found no canonical rendering of a `Route`, and had to invent `family/model` —
    which would have made every record comparable only within whatever each caller
    picked. core answered with `Route.id` (#121, e2a8ef8) and told cli to render that
    key plus its own where-suffix "instead of the two renderings drifting".
    """
    from otaman_core.llm_router import Route

    for route in (
        Route(family="anthropic", model="claude-opus-4"),
        Route(family="openai"),
        Route(family="ollama", model="llama3", local=True),
        Route(family="ollama", local=True),
    ):
        got = llm_routes._agent_route("a", route)
        assert got.key == route.id, f"cli re-derived a key: {got.key} != {route.id}"


def test_local_and_cloud_routes_do_not_collapse_to_one_key(tmp_path):
    """THE property spec 2.1's gate measures: "telemetry distinguishes routes".

    The same family/model run tenant-local vs off-tenant is a different route for
    cost and sensitivity. The flat `family/model` rendering this replaces collapsed
    them into one telemetry bucket.
    """
    root = _program(
        tmp_path,
        "project: demo\nversion: '1.0'\n"
        "router:\n  backend: litellm-proxy\n  base_url: http://x\n"
        "agents:\n"
        "  - name: a-local\n    role: d\n"
        "    route: {family: ollama, model: llama3, local: true}\n"
        "  - name: a-cloud\n    role: d\n"
        "    route: {family: ollama, model: llama3}\n",
    )
    surface = llm_routes.load(root)
    keys = {r.agent: r.key for r in surface.routes}

    assert keys["a-local"] != keys["a-cloud"], f"the locality bit collapsed: {keys}"
    assert keys["a-local"].endswith("@local")
    assert not keys["a-cloud"].endswith("@local")


def test_the_label_is_the_key_plus_cli_s_own_suffix(tmp_path):
    """The split core asked for: `id` is the telemetry key and may not move for
    presentation; the `(local)` / `(leaves tenant)` gloss is cli's and may."""
    from otaman_core.llm_router import Route

    local = llm_routes._agent_route("a", Route(family="ollama", model="llama3", local=True))
    cloud = llm_routes._agent_route("a", Route(family="anthropic", model="claude-opus-4"))

    assert local.label == "ollama/llama3@local (local)"
    assert cloud.label == "anthropic/claude-opus-4 (leaves tenant)"
    assert local.label.startswith(local.key) and cloud.label.startswith(cloud.key)


def test_an_old_core_gets_NO_KEY_and_the_label_says_why(tmp_path):
    """cli may not compose a route key — not even a correctly-shaped one.

    My first version kept a fallback that reproduced `Route.id`'s form for a core
    predating #121, reasoning a correct-shaped key beat a missing one. lrb gate 2.1
    then pinned the opposite as a grep-guard: "Route.id is the ONLY route-to-string
    producer ... no hand-formatted route key exists outside core's llm_router.py".

    The guard is right and my reasoning was wrong in a specific way: a second producer
    agrees with core right up to the release where it does not, and agreeing is what it
    was written to do — so the divergence is undetectable. "I cannot name this route"
    is a third state, and it renders with its remedy rather than as a guess.
    """

    class _OldRoute:  # no `id` property — a core that predates #121
        family = "ollama"
        model = "llama3"
        local = True

    got = llm_routes._agent_route("a", _OldRoute())

    assert got.route_id == "", "nothing to carry from an old bundle"
    assert got.key == "", "cli must not invent a key"
    assert got.label == f"{llm_routes.NO_ROUTE_KEY} (local)"
    assert "update otaman-core" in got.label, "the state must carry its remedy"
    assert got.label != llm_routes.NO_ROUTE, (
        "a declared-but-unnameable route is NOT the default path"
    )


def test_the_default_path_has_no_key_and_says_so(tmp_path):
    """No route declared is not a route named "default" — the same distinction
    `CriticCost.route=None` carries on the telemetry side."""
    route = llm_routes.AgentRoute(agent="a")

    assert route.default_path is True
    assert route.key == ""
    assert route.label == llm_routes.NO_ROUTE


def test_the_key_is_CARRIED_from_core_not_recomputed(tmp_path):
    """Provenance, not just shape — and this is the test that actually guards it.

    `test_the_key_is_cores_route_id_verbatim` compares against real `Route`s, and
    cli's old-bundle fallback reproduces `Route.id`'s form exactly, so that test
    passes whether cli CONSUMES core's id or recomputes an identical string. It
    cannot distinguish the two, which is the whole property core asked for ("instead
    of the two renderings drifting").

    So: a route whose `id` is deliberately NOT what any local derivation would
    produce. If cli ever goes back to composing the key itself, this fails and the
    other tests do not.
    """

    class _RouteWithOpaqueId:
        family = "ollama"
        model = "llama3"
        local = True
        id = "core-says-this-is-the-key"

    got = llm_routes._agent_route("a", _RouteWithOpaqueId())

    assert got.key == "core-says-this-is-the-key", (
        "cli recomputed the key instead of carrying core's Route.id"
    )
    assert got.label == "core-says-this-is-the-key (local)", (
        "the label must be core's key plus cli's suffix"
    )


def test_cli_contains_no_second_route_key_producer(tmp_path):
    """lrb gate 2.1's single-home grep-guard, proven HERE rather than at the gate.

    The gate clause (core 20261003T022740): "Route.id is the ONLY route-to-string
    producer — no hand-formatted route key (f-string/join over .family/.model) exists
    outside core's llm_router.py". specs runs that over the fleet; this runs it over
    cli on every commit, so the repo cannot regrow one between gates.

    Prose that NAMES a family in a sentence is not a key, and the two sites that do
    that carry a comment saying so — which is also what makes them readable as
    deliberate rather than as leftovers.
    """
    import re
    from pathlib import Path as _Path

    src_root = _Path(__file__).resolve().parent.parent / "src" / "otaman_cli"
    # A key producer looks like an f-string or join that puts a ROUTE's family/model
    # together. Scoped to route-ish subjects (`route.`, `r.`, `rt.`) on purpose:
    # `args.model` in models_report.py is an argparse field, not a Route, and a guard
    # that flags it would be re-tuned carelessly the first time it cried wolf.
    # What a KEY producer does, precisely: it puts a route's `family` and `model`
    # together, or glues a separator from core's id form (`/`, `@local`) onto one of
    # them. What it is NOT: prose that mentions a family in a sentence — doctor's
    # "routes to anthropic which leaves the tenant" names a target, and a guard that
    # flagged it would be loosened the first time it cried wolf.
    #
    # `self` is in the subject list deliberately: the likeliest place a producer
    # regrows is INSIDE `AgentRoute` (that is exactly where mine was), and the first
    # version of this guard watched only `route.`/`r.` and missed that sabotage.
    subj = r"(?:route|rt|r|self)"
    both = re.compile(rf"""\b{subj}\.family\b.*\b{subj}\.model\b""")
    glued = re.compile(rf"""\{{{subj}\.(?:family|model)\}}\s*(?:/|@)""")
    offenders = []
    for path in sorted(src_root.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if both.search(line) or glued.search(line):
                offenders.append(f"{path.relative_to(src_root)}:{n}: {line.strip()[:70]}")
    assert not offenders, (
        "a route key is being composed outside core's llm_router — use Route.id "
        "(carried as AgentRoute.key):\n  " + "\n  ".join(offenders)
    )


def test_the_opaque_id_survives_the_REAL_path_not_just_the_helper(tmp_path, monkeypatch):
    """lrb 2.1's opaque-id probe, run through `effective_route` as the clause specifies.

    My first version of this probe called `_agent_route` directly. That proves the
    helper carries core's id and nothing about the path a user actually takes —
    `load()` resolves through `_core().effective_route` and could drop or re-derive
    the id anywhere between. plugin's #114 injects through `effective_route` "so it
    runs the real call path" (20261003T030520) and the gate clause now names that
    method; this is cli's equivalent.

    Only reading core's `.id` can produce the opaque string, so a recomputation —
    which reproduces `Route.id`'s form exactly and therefore passes every
    form-comparing test — fails here.
    """

    class _OpaqueRoute:
        family = "ollama"
        model = "llama3"
        local = True
        id = "core-says-this-is-the-key"

    class _StubCore:
        """Stands in for otaman_core.llm_router on the path `load()` really uses."""

        @staticmethod
        def parse_router_config(raw):  # noqa: D102 - signature parity only
            return real_core.parse_router_config(raw)

        @staticmethod
        def effective_route(config, agent):  # noqa: D102
            return _OpaqueRoute() if agent == "a-opaque" else None

        @staticmethod
        def select_backend(*a, **k):  # noqa: D102
            return real_core.select_backend(*a, **k)

        @staticmethod
        def route_leaves_tenant(*a, **k):  # noqa: D102
            return real_core.route_leaves_tenant(*a, **k)

    from otaman_core import llm_router as real_core

    root = _program(
        tmp_path,
        "project: demo\nversion: '1.0'\n"
        "router:\n  backend: litellm-proxy\n  base_url: http://x\n"
        "agents:\n  - name: a-opaque\n    role: d\n"
        "    route: {family: ollama, model: llama3, local: true}\n",
    )
    monkeypatch.setattr(llm_routes, "_core", lambda: _StubCore)

    surface = llm_routes.load(root)

    (route,) = surface.routes
    assert route.key == "core-says-this-is-the-key", (
        "the real load() path recomputed the key instead of carrying core's Route.id"
    )
    assert route.label == "core-says-this-is-the-key (local)"
    assert "core-says-this-is-the-key" in "\n".join(llm_routes.render_lines(surface)), (
        "the rendered surface must show core's key, not a local rendering"
    )
