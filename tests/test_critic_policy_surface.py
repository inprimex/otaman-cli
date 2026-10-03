"""critic-selection-policy 1.3 — the config surface and doctor view.

Core owns the engine; this is the surface over it. The properties under test are
the ones that decide whether the surface can be trusted: it must not invent a
selection context, it must not report an unreadable config as an absent one, and
it must render a dropped critic rather than quietly showing a shorter list.

Three invariants belong to core and are deliberately not re-implemented here —
the clearance gate, the raise on an unconfigured hook, and every result naming
its policy. A test asserts the surface does not reach past `select_critics`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import critic_policy

_PLATFORM = """project: demo
repos:
  - {name: otaman-core, owner: core-agent}
  - {name: otaman-cli, owner: cli-agent}
"""

_GATES = """clearances:
  cofounder-agent: [cofounder-only, commercial]
  cli-agent: [commercial]
hooks:
  scr-critique:
    primary: stakeholder-affected
    fallback: role-based
    target-role: reviewer
    sensitivity-overrides: {cofounder-only: sensitivity-scoped}
  outcome-review:
    primary: consumer-chain
    fallback: role-based
    target-role: reviewer
roles:
  cofounder-agent: [reviewer]
"""


def _ROLE_BASED_ARM(config: str) -> str:
    """Add the `role-based` arm and roles table core #122 requires of every hook.

    core refuses a pairing that cannot select an independent critic for a self-owned
    proposal (D4), so a bare `primary:` no longer parses. The arm is appended rather
    than replacing the primary, because what each of these fixtures is about is the
    PRIMARY's behaviour — the fallback only runs when the primary empties, and none of
    these cases gets that far.
    """
    return config + (
        "    fallback: role-based\n    target-role: reviewer\n"
        "roles:\n  cofounder-agent: [reviewer]\n"
    )


@pytest.fixture
def program(tmp_path):
    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(_GATES, encoding="utf-8")
    return tmp_path


def _hook(surface, name):
    return next(h for h in surface.hooks if h.hook == name)


# ---------------------------------------------------------------------------
# the configured chain


def test_the_effective_policy_per_hook_is_shown(program):
    surface = critic_policy.load(program)
    assert _hook(surface, "scr-critique").primary == "stakeholder-affected"
    assert _hook(surface, "outcome-review").primary == "consumer-chain"


def test_the_fallback_and_overrides_are_shown(program):
    """A chain whose fallback is invisible reads as a single policy — and the
    fallback is what runs when the primary selects nobody."""
    hook = _hook(program and critic_policy.load(program), "scr-critique")
    assert hook.fallback == "role-based"
    assert hook.overrides == {"cofounder-only": "sensitivity-scoped"}


def test_the_clearance_roster_is_listed(program):
    surface = critic_policy.load(program)
    assert dict(surface.roster.rows) == {
        "cli-agent": ("commercial",),
        "cofounder-agent": ("cofounder-only", "commercial"),
    }


# ---------------------------------------------------------------------------
# evaluated only where the inputs are real


def test_a_locally_derivable_policy_is_evaluated(program):
    """The spec's scenario: stakeholder-affected selects each affected repo's
    owner-agent. platform.yaml already declares them, so this is answerable
    without a live gate."""
    hook = _hook(critic_policy.load(program), "scr-critique")
    assert hook.evaluated is True
    assert set(hook.critics) == {"cli-agent", "core-agent"}


def test_a_policy_needing_live_inputs_is_not_invented(program):
    """consumer-chain selects over inputs only dispatch has. Showing a result
    built from a made-up context would be a surface that looks authoritative
    and is fiction."""
    hook = _hook(critic_policy.load(program), "outcome-review")
    assert hook.evaluated is False
    assert hook.critics == ()
    assert "only a live gate has" in hook.note


def test_stakeholder_affected_with_no_owners_says_so(tmp_path):
    (tmp_path / "platform.yaml").write_text("project: d\nrepos: []\n", encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(
        _ROLE_BASED_ARM("hooks:\n  h:\n    primary: stakeholder-affected\n"), encoding="utf-8"
    )
    hook = _hook(critic_policy.load(tmp_path), "h")
    assert hook.evaluated is False and "no repo owners" in hook.note


# ---------------------------------------------------------------------------
# absent vs unreadable


def test_no_config_is_not_a_failure(tmp_path):
    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    surface = critic_policy.load(tmp_path)
    assert surface.configured is False and surface.error == "" and surface.hooks == []


def test_an_unparseable_config_is_not_checked_never_absent(tmp_path):
    """The conflation corrected in #223 and #227, third surface: a config that
    exists and does not parse means the gates could not be evaluated."""
    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(
        "hooks:\n  h:\n    primary: not-a-real-policy\n", encoding="utf-8"
    )
    surface = critic_policy.load(tmp_path)
    assert surface.configured is True
    assert surface.error and "does not parse" in surface.error


def test_unreadable_yaml_is_not_checked(tmp_path):
    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text("hooks: [unclosed\n", encoding="utf-8")
    assert "could not be read" in critic_policy.load(tmp_path).error


def test_an_absent_core_is_not_checked_rather_than_clean(program, monkeypatch):
    monkeypatch.setattr(critic_policy, "_core", lambda: None)
    surface = critic_policy.load(program)
    assert surface.error and "1.1" in surface.error


# ---------------------------------------------------------------------------
# core's invariants are consumed, not re-implemented


def test_the_surface_does_not_reach_past_select_critics():
    """The clearance gate, the unconfigured-hook raise, and policy-naming are
    core's. A second implementation is the drift `select_critics` exists as a
    single home to prevent."""
    src = Path(critic_policy.__file__).read_text(encoding="utf-8")
    for forbidden in ("cleared_for(", "_run_policy", "dropped_uncleared =", "clearances["):
        assert forbidden not in src, f"re-implements core: {forbidden}"
    assert "select_critics" in src


def test_an_unconfigured_hook_raises_in_core_and_is_surfaced_not_swallowed(program, monkeypatch):
    """core raises rather than returning no critics, because "not configured"
    and "selected nobody" mean opposite things. The surface must say so."""
    import otaman_core.verification_gates as vg

    def boom(config, hook, ctx):
        raise vg.VerificationGatesError("hook 'ghost' is not configured")

    monkeypatch.setattr(vg, "select_critics", boom)
    hook = _hook(critic_policy.load(program), "scr-critique")
    assert hook.evaluated is False
    assert "not configured" in hook.note


# ---------------------------------------------------------------------------
# the doctor view


def test_doctor_reports_the_effective_policy(program):
    from otaman_cli.doctor import check_critic_policy

    result = check_critic_policy(program)
    assert result["details"]["hooks"] == 2
    assert "scr-critique=stakeholder-affected" in result["details"]["effective"]


def test_doctor_warns_when_no_clearances_are_declared(tmp_path):
    """sensitivity-scoped with no clearances drops every critic — a gate that
    selects nobody, which reads as a gate that passed."""
    from otaman_cli.doctor import check_critic_policy

    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(
        _ROLE_BASED_ARM("hooks:\n  h:\n    primary: stakeholder-affected\n"), encoding="utf-8"
    )
    result = check_critic_policy(tmp_path)
    assert result["status"] == "warn"
    assert any("no clearances" in i["message"] for i in result["issues"])


def test_doctor_says_not_checked_for_an_unparseable_config(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(
        "hooks:\n  h:\n    primary: nonsense\n", encoding="utf-8"
    )
    result = check_critic_policy(tmp_path)
    assert result["status"] == "warn" and "NOT CHECKED" in result["details"]["gates"]


def test_doctor_is_quiet_when_nothing_is_declared(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    (tmp_path / "platform.yaml").write_text(_PLATFORM, encoding="utf-8")
    result = check_critic_policy(tmp_path)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# the clearance gate, previewed


def test_a_sensitivity_preview_applies_the_override(tmp_path):
    """`--sensitivity` answers "what does this hook do if the content is
    cofounder-only?", which is what the clearance roster is for.

    The override is proven by a SELECTION, not by an empty one: this roster gives the
    cleared agent a repo, so `sensitivity-scoped` picks them and the primary demonstrably
    did not run. The older form asserted `critics == ()` against the shared fixture,
    where nobody holds the class — an empty set that the primary, the override, or a
    fallback could each have produced, so it never pinned the override at all. core #122
    then made it worse by requiring a `role-based` fallback: the empty override result
    falls through to a fallback a checkout cannot evaluate, and the chain's honest answer
    became "could not know".
    """
    (tmp_path / "platform.yaml").write_text(
        "project: d\nrepos:\n  - {name: a, owner: core-agent}\n"
        "  - {name: b, owner: cofounder-agent}\n",
        encoding="utf-8",
    )
    (tmp_path / "verification-gates.yaml").write_text(
        "clearances:\n  cofounder-agent: [cofounder-only]\n"
        "hooks:\n  h:\n    primary: stakeholder-affected\n"
        "    sensitivity-overrides: {cofounder-only: sensitivity-scoped}\n"
        "    fallback: role-based\n    target-role: reviewer\n"
        "roles:\n  cofounder-agent: [reviewer]\n",
        encoding="utf-8",
    )

    hook = _hook(critic_policy.load(tmp_path, sensitivity="cofounder-only"), "h")

    assert hook.evaluated is True
    assert hook.critics == ("cofounder-agent",), "the override's policy chose, not the primary"
    assert "cofounder-only" in hook.note


def test_selecting_nobody_is_never_rendered_as_an_empty_list(program):
    """An empty critic list with no reason is the silent case core's invariant
    forbids — whether the policy dropped someone or simply found none."""
    hook = _hook(critic_policy.load(program, sensitivity="cofounder-only"), "scr-critique")
    assert hook.note, "a gate that selects nobody must say why"


def test_a_dropped_critic_is_named(tmp_path):
    """The other shape: a policy PICKS someone and the clearance gate removes
    them. core records it in dropped_uncleared and it must be rendered.

    This branch was unreachable until `--sensitivity` existed — a sabotage run
    on it changed nothing, because no test could get there.
    """
    (tmp_path / "platform.yaml").write_text(
        "project: d\nrepos:\n  - {name: a, owner: core-agent}\n", encoding="utf-8"
    )
    (tmp_path / "verification-gates.yaml").write_text(
        "clearances:\n  cofounder-agent: [cofounder-only]\n"
        + _ROLE_BASED_ARM("hooks:\n  h:\n    primary: stakeholder-affected\n"),
        encoding="utf-8",
    )
    hook = _hook(critic_policy.load(tmp_path, sensitivity="cofounder-only"), "h")
    assert "dropped for missing clearance" in hook.note
    assert "core-agent" in hook.note


def test_no_sensitivity_means_no_clearance_gate(program):
    """Without a sensitivity class the gate does not run, and everyone the
    policy picked stands."""
    hook = _hook(critic_policy.load(program), "scr-critique")
    assert set(hook.critics) == {"cli-agent", "core-agent"} and "dropped" not in hook.note
