"""console-ia-consolidation P4 — absorb the pending surfaces (5.1/5.2, D7).

5.1 the four dispatched registries render as COLLAPSED SIBLING ROOTS in
    Artifacts when their process is enabled — no new top-level door.
5.2 the recorded-but-unbuilt surfaces (policy, connections, blocked,
    watchdog) are reachable from Setup, not from the top level.

The four registries are dispatched and UNBUILT, so these tests pin the SLOT and
its gating — never a schema. Nothing here names a registry the console knows
about: the fixtures invent keys precisely to prove no key is special-cased.
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli.console import bus
from otaman_cli.console.extra_registries import (
    MAX_ENTRIES,
    SPINE_PROCESSES,
    discover,
    registry_roots,
)
from otaman_cli.console.tree import build_artifact_tree


def _program(tmp_path, processes: dict, monkeypatch, files: dict | None = None):
    root = tmp_path / "meta"
    root.mkdir(exist_ok=True)
    # `processes:` lives UNDER `program:` — load_program_extensions reads that
    # block, so a top-level `processes:` key is silently ignored.
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "d", "version": "1.0", "program": {"processes": processes}}),
        encoding="utf-8",
    )
    strat = tmp_path / "strategy"
    strat.mkdir(exist_ok=True)
    for name, payload in (files or {}).items():
        strat.joinpath(name).write_text(
            payload if isinstance(payload, str) else yaml.dump(payload), encoding="utf-8"
        )
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    return bus.Program(name="d", root=root)


# ---------------------------------------------------------------------------
# 5.1 — the slot, and what gates it


def test_an_enabled_unknown_registry_becomes_a_root(tmp_path, monkeypatch):
    p = _program(
        tmp_path,
        {"vocabulary": {"enabled": True}},
        monkeypatch,
        {"vocabulary.yaml": {"vocabulary": [{"term": "outcome", "definition": "a change"}]}},
    )
    (root,) = registry_roots(p)
    assert root.kind == "registry" and root.id == "vocabulary"
    assert "1 entry" in root.title


def test_the_root_is_collapsed_not_closed(tmp_path, monkeypatch):
    """D7 grows the tree SIDEWAYS: a new root must not cost vertical space. And
    `collapsed` is not `closed` — closed HIDES a node until `f`."""
    p = _program(
        tmp_path, {"vocabulary": {"enabled": True}}, monkeypatch, {"vocabulary.yaml": {"v": []}}
    )
    (root,) = registry_roots(p)
    assert root.collapsed is True
    assert root.closed is False


def test_a_disabled_process_gets_no_root(tmp_path, monkeypatch):
    p = _program(
        tmp_path, {"glossary": {"enabled": False}}, monkeypatch, {"glossary.yaml": {"s": []}}
    )
    assert registry_roots(p) == []


def test_an_enabled_but_unbuilt_registry_says_so(tmp_path, monkeypatch):
    """These registries are dispatched and unbuilt. Enabling the process is the
    human's declaration that it belongs to the program — showing nothing would
    leave them without feedback on a switch they just set."""
    p = _program(tmp_path, {"user-flows": {"enabled": True}}, monkeypatch)
    (root,) = registry_roots(p)
    assert "not created yet" in root.title
    assert root.children == []


def test_a_bare_key_defaults_to_enabled(tmp_path, monkeypatch):
    """Declaring the process at all is the opt-in, matching the modelled
    processes' `enabled: bool = True`."""
    p = _program(tmp_path, {"vocabulary": {}}, monkeypatch)
    assert [r.key for r in discover(p)] == ["vocabulary"]


@pytest.mark.parametrize("spine", sorted(SPINE_PROCESSES))
def test_spine_processes_never_become_sibling_roots(tmp_path, monkeypatch, spine):
    """outcomes/solutions/personas already have structural homes; a node drawn
    twice makes counts lie (D4)."""
    p = _program(tmp_path, {spine: {"enabled": True}}, monkeypatch, {f"{spine}.yaml": {"x": []}})
    assert registry_roots(p) == []


def test_no_registry_key_is_special_cased(tmp_path, monkeypatch):
    """The four are unbuilt and their KEYS are not settled. Any key works, so
    whatever spec-agent picks lands with no console change."""
    p = _program(
        tmp_path,
        {"a-key-nobody-has-specified-yet": {"enabled": True}},
        monkeypatch,
        {"a-key-nobody-has-specified-yet.yaml": {"items": [{"id": "X"}]}},
    )
    (root,) = registry_roots(p)
    assert root.id == "a key nobody has specified yet"


def test_a_custom_path_is_honoured(tmp_path, monkeypatch):
    p = _program(
        tmp_path,
        {"risks": {"enabled": True, "path": "risks-and-assumptions.yaml"}},
        monkeypatch,
        {"risks-and-assumptions.yaml": {"risks": [{"id": "RISK-1", "title": "a risk"}]}},
    )
    (root,) = registry_roots(p)
    assert "1 entry" in root.title
    assert root.children[0].id == "RISK-1"


# ---------------------------------------------------------------------------
# 5.1 — schema-agnostic reading


def test_a_top_level_list_registry_reads(tmp_path, monkeypatch):
    p = _program(
        tmp_path,
        {"glossary": {"enabled": True}},
        monkeypatch,
        {"glossary.yaml": [{"name": "python"}]},
    )
    (root,) = registry_roots(p)
    assert root.children[0].id == "python"


def test_an_unknown_wrapper_key_still_reads(tmp_path, monkeypatch):
    p = _program(
        tmp_path,
        {"flows": {"enabled": True}},
        monkeypatch,
        {"flows.yaml": {"business_processes": [{"id": "F-1", "summary": "checkout"}]}},
    )
    (root,) = registry_roots(p)
    assert (root.children[0].id, root.children[0].title) == ("F-1", "checkout")


def test_an_unparseable_registry_still_renders_its_root(tmp_path, monkeypatch):
    """Degrade to "no preview", never raise into the TUI."""
    p = _program(
        tmp_path, {"vocabulary": {"enabled": True}}, monkeypatch, {"vocabulary.yaml": "{[not yaml"}
    )
    (root,) = registry_roots(p)
    assert root.kind == "registry" and root.children == []


def test_a_long_registry_announces_what_it_withheld(tmp_path, monkeypatch):
    """No silent caps: a list showing 50 of 60 that reads as complete is worse
    than a long list."""
    rows = [{"id": f"R-{i}", "title": f"row {i}"} for i in range(MAX_ENTRIES + 10)]
    p = _program(
        tmp_path, {"risks": {"enabled": True}}, monkeypatch, {"risks.yaml": {"risks": rows}}
    )
    (root,) = registry_roots(p)
    assert len(root.children) == MAX_ENTRIES + 1
    assert "10 more not shown" in root.children[-1].id


def test_no_processes_block_means_no_extra_roots(tmp_path, monkeypatch):
    """The live program declares only `outcomes`, and must gain nothing."""
    p = _program(tmp_path, {"outcomes": {"path": "outcomes/declared.yaml"}}, monkeypatch)
    assert registry_roots(p) == []


def test_an_unreadable_platform_yaml_degrades(tmp_path, monkeypatch):
    root = tmp_path / "broken"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("{[nope", encoding="utf-8")
    assert registry_roots(bus.Program(name="b", root=root)) == []


# ---------------------------------------------------------------------------
# 5.1 — wired into the tree, as SIBLINGS


def test_registry_roots_are_siblings_in_the_artifact_tree(tmp_path, monkeypatch):
    p = _program(
        tmp_path,
        {"outcomes": {"enabled": False}, "vocabulary": {"enabled": True}},
        monkeypatch,
        {"vocabulary.yaml": {"v": [{"term": "t"}]}},
    )
    roots = build_artifact_tree(p)
    assert [r.id for r in roots if r.kind == "registry"] == ["vocabulary"]


def test_the_capability_lens_gets_no_registry_roots(tmp_path, monkeypatch):
    """A registry is not a capability — a root there would be a category error."""
    from otaman_cli.console.tree import LENS_CAPABILITY

    p = _program(
        tmp_path,
        {"vocabulary": {"enabled": True}},
        monkeypatch,
        {"vocabulary.yaml": {"v": [{"term": "t"}]}},
    )
    roots = build_artifact_tree(p, lens=LENS_CAPABILITY)
    assert not [r for r in roots if r.kind == "registry"]


def test_no_new_top_level_door_for_registries():
    """The whole point of D7: registries grow the TREE, not the door budget."""
    from otaman_cli.console.app import HomeScreen

    keys = {b.key for b in HomeScreen.BINDINGS}
    assert keys == {"d", "m", "t", "l", "b", "a", "s", "r", "escape", "q"}


# ---------------------------------------------------------------------------
# 5.2 — the recorded-but-unbuilt surfaces live in Setup


@pytest.mark.parametrize("verb", ["policy", "connection", "blocked", "watchdog"])
def test_each_recorded_surface_is_reachable_from_setup(verb):
    from otaman_cli.console.setup import SETUP_VERBS

    assert any(v.argv[0] == verb for v in SETUP_VERBS), verb


def test_no_recorded_surface_took_a_top_level_door():
    from otaman_cli.console.app import HomeScreen

    labels = " ".join((b.description or "").lower() for b in HomeScreen.BINDINGS)
    for surface in ("policy", "connection", "blocked", "watchdog"):
        assert surface not in labels, surface


def test_setup_uses_the_blocked_read_verb_not_the_registering_one():
    """`otaman blocked list` REGISTERED a blocked entry named "list" — bare
    `blocked <slug>` registers. A menu entry that writes on a read is the
    `connection check` footgun again."""
    from otaman_cli.console.setup import SETUP_VERBS

    (blocked,) = [v for v in SETUP_VERBS if v.argv[0] == "blocked"]
    assert blocked.argv == ("blocked", "--list")


def test_an_environment_dependent_verb_carries_its_caveat():
    from otaman_cli.console.setup import SETUP_VERBS

    (watchdog,) = [v for v in SETUP_VERBS if v.argv[0] == "watchdog"]
    assert watchdog.note


def test_setup_notes_are_rendered():
    """`note` existed on SetupVerb but was never drawn, so a caveat set here was
    invisible to the human it was written for."""
    from otaman_cli.console.setup import SetupVerb, setup_item_label

    row = setup_item_label(SetupVerb("X", ("watchdog", "status"), note="needs a runner"))
    assert "needs a runner" in row
    assert "otaman watchdog status" in row  # the command stays visible
    # and a verb without a note gets no dangling tail
    assert setup_item_label(SetupVerb("X", ("policy", "list"))).endswith(")")


def test_every_setup_verb_is_a_real_registered_command():
    """A menu entry pointing at a verb that does not exist is a dead door."""
    import otaman_cli.commands as commands
    from otaman_cli.console.setup import SETUP_VERBS

    known = commands.registered_names()
    for verb in SETUP_VERBS:
        assert verb.argv[0] in known, verb.argv


# ---------------------------------------------------------------------------
# the `blocked list` footgun itself


def test_blocked_list_lists_instead_of_registering(tmp_path, monkeypatch, capsys):
    """Found live: `blocked list` created an entry named "list" in the real
    program. Every other noun uses `<noun> list`."""
    from otaman_cli.commands.blocked import cmd_blocked

    root = tmp_path / "proj"
    (root / ".agents" / "blocked").mkdir(parents=True)
    (root / ".otaman").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr("otaman_cli.commands.blocked.find_project_root", lambda: root)
    monkeypatch.setattr(
        "otaman_cli.commands.blocked.resolve_agent_identity", lambda _r: "cli-agent"
    )

    assert cmd_blocked(["list"]) == 0
    written = root / ".agents" / "blocked" / "cli-agent.md"
    assert not written.is_file() or "## Blocked: list" not in written.read_text(encoding="utf-8")


def test_a_real_slug_still_registers(tmp_path, monkeypatch):
    """The fix must not break registration — only the word `list` is claimed."""
    from otaman_cli.commands.blocked import cmd_blocked

    root = tmp_path / "proj"
    (root / ".agents" / "blocked").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr("otaman_cli.commands.blocked.find_project_root", lambda: root)
    monkeypatch.setattr(
        "otaman_cli.commands.blocked.resolve_agent_identity", lambda _r: "cli-agent"
    )

    assert cmd_blocked(["waiting-on-core-api"]) == 0
    text = (root / ".agents" / "blocked" / "cli-agent.md").read_text(encoding="utf-8")
    assert "waiting-on-core-api" in text


def test_a_config_process_is_not_a_registry_root(tmp_path, monkeypatch):
    """`program.processes.skills` carries {profile, extra} for the skill-pack
    resolver — there is no skills.yaml of rows behind it. Rendering it as a
    registry produced a phantom "skills — enabled · registry home unset" root
    on every wizard-generated program, once the wizard started writing the key
    where the resolver reads it (cofounder-agent 20260919T232423).

    The collision is NOT settled here: "per-project skills" is also one of the
    four dispatched registries, so the same key would mean two things. Excluded
    until spec-agent and plugin-agent rule.
    """
    from otaman_cli.console.extra_registries import NON_REGISTRY_PROCESSES

    assert "skills" in NON_REGISTRY_PROCESSES
    p = _program(
        tmp_path,
        {"skills": {"profile": "tech-startup-cofounder", "extra": ["risk-reviewer"]}},
        monkeypatch,
    )
    assert registry_roots(p) == []


def test_a_wizard_generated_program_grows_no_phantom_root(tmp_path, monkeypatch):
    """End-to-end against the real generator, since that is the path that
    introduced the phantom."""
    from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml
    from otaman_cli.yaml_fast import clear_cache

    root = tmp_path / "wizard"
    root.mkdir()
    doc = _build_platform_yaml(
        {
            "program_name": "demo",
            "primary_repo": ".",
            "skill_profile": "tech-startup-cofounder",
            "extra_skills": [],
        }
    )
    root.joinpath("platform.yaml").write_text(yaml.dump(doc), encoding="utf-8")
    clear_cache()
    assert registry_roots(bus.Program(name="d", root=root)) == []
