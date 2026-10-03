"""A self-owned proposal can resolve to NO critic, and only the fallback decides.

plugin-agent's csp 1.2 finding (20261003T032605), reproduced here through core's
committed engine rather than taken from the message:

    primary=stakeholder-affected  fallback=sensitivity-scoped  -> critics=()
    primary=stakeholder-affected  fallback=role-based          -> critics=('b-agent',)

Same invariant (`excluded_proposer: True`), same primary, same inputs, same proposer.
An agent proposing a change to the repo it owns is the commonest proposal shape in the
fleet: the primary selects that repo's owner, D4's invariant excludes the proposer, and
`sensitivity-scoped` returns nothing when no sensitivity class is set — which is the
ordinary case.

**core #122 closed this at the parse** (csp 1.4): a hook with no `role-based` arm and no
`roles:` table is refused outright, so the uncovered pairing can no longer be written.
That is the better fix — cli warned about a config, core deleted the possibility of it.

Two duties survive for cli, and this file covers both:

1. Surface core's refusal with its cause, rather than swallowing it into "no policy
   declared" (the fail-open conflation).
2. Keep the probe, because cli must not assume core's version — an older core parses
   those files happily, and then cli's warning is the only thing standing between the
   tenant and silent non-coverage. The probe is exercised against a bundle built from
   core's own dataclasses (`older_gates_core`), since no config file can reach it now.

The probe measures by ASKING the engine, never by pattern-matching policy names, so a
policy added later is covered without touching cli.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli import critic_policy

#: Valid under core #122: a role-based arm plus the roles table it resolves from.
_VALID = (
    "hooks:\n  spec-proposal:\n    primary: stakeholder-affected\n    fallback: role-based\n"
    "roles:\n  b-agent: [reviewer]\n"
)


def _program(tmp_path: Path, config: str = _VALID) -> Path:
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n"
        "  - name: a\n    path: ../a\n    owner: a-agent\n"
        "  - name: b\n    path: ../b\n    owner: b-agent\n",
        encoding="utf-8",
    )
    (root / critic_policy.CONFIG_NAME).write_text(config, encoding="utf-8")
    return root


def _hook(root: Path):
    surface = critic_policy.load(root)
    assert surface.configured and not surface.error, surface.error
    (hook,) = [h for h in surface.hooks if h.hook == "spec-proposal"]
    return hook


def _uncovered(root: Path, older_gates_core, *, fallback, **kw):
    older_gates_core(hooks={"spec-proposal": ("stakeholder-affected", fallback)}, **kw)
    return _hook(root)


# ---------------------------------------------------------------------------
# core's refusal is the primary fix, and it must reach the operator


def test_core_REFUSES_the_uncovered_pairing_and_cli_names_the_cause(tmp_path):
    """The pairing plugin measured is no longer configurable. cli's job is the message."""
    root = _program(
        tmp_path,
        "hooks:\n  spec-proposal:\n    primary: stakeholder-affected\n"
        "    fallback: sensitivity-scoped\n",
    )

    surface = critic_policy.load(root)

    assert surface.error, "a refused config must be an error, not silence"
    assert "role-based" in surface.error, "the remedy must name the arm that works"
    assert surface.hooks == []


# ---------------------------------------------------------------------------
# the finding, both arms — on a bundle an older core accepted


def test_a_sensitivity_scoped_fallback_leaves_self_owned_proposals_uncovered(
    tmp_path, older_gates_core
):
    hook = _uncovered(_program(tmp_path), older_gates_core, fallback="sensitivity-scoped")

    assert set(hook.self_owned_uncovered) == {"a-agent", "b-agent"}, (
        "both owners' own proposals select nobody under this pairing"
    )


def test_no_fallback_at_all_is_also_uncovered(tmp_path, older_gates_core):
    """Nothing decides them — the case the surface's `single_candidate` note predicts."""
    hook = _uncovered(_program(tmp_path), older_gates_core, fallback=None)

    assert "a-agent" in hook.self_owned_uncovered


def test_a_role_based_fallback_yields_NO_FINDING_because_cli_cannot_know(tmp_path):
    """The honest answer, and it corrected a pre-existing false report.

    plugin measured `fallback: role-based` SELECTING a critic — with `agent_roles` and
    `target_role` supplied, which a live gate has and a repo checkout does not. core #122
    moved the roles table into the config, so the table resolves locally; `target_role`
    still does not, so the chain's answer remains unknowable here — and an unknowable
    chain must not be reported as uncovered.
    """
    hook = _hook(_program(tmp_path))

    assert hook.self_owned_uncovered == (), (
        f"cli claimed knowledge it does not have: {hook.self_owned_uncovered}"
    )


def test_a_role_based_hook_is_reported_as_unknowable_not_as_empty(tmp_path):
    """The pre-existing false report, pinned so it cannot come back.

    cli once listed `role-based` as locally evaluable, so a `role-based` hook rendered
    `critics=()` / "role-based selected nobody" / `evaluated=True` — asserting the hook
    selects nobody when the truth was that cli could not know. core #122 reports the
    missing input by name, which is what the surface now renders.
    """
    root = _program(
        tmp_path,
        "hooks:\n  spec-proposal:\n    primary: role-based\nroles:\n  b-agent: [reviewer]\n",
    )

    hook = _hook(root)

    assert hook.evaluated is False, "cli cannot evaluate role-based from a checkout"
    assert hook.critics == ()
    assert hook.could_not_know == ("target_role",)
    assert "could not know" in hook.note, hook.note
    assert "selected nobody" not in hook.note, (
        "an unanswerable policy must not be reported as having selected nobody"
    )


# ---------------------------------------------------------------------------
# it is ASKED, not pattern-matched


def test_the_probe_runs_the_engine_rather_than_matching_policy_names(tmp_path, older_gates_core):
    """A policy core adds later must be covered without touching cli.

    So the probe must call `select_critics`, not compare `primary`/`fallback` against a
    hard-coded list. Sabotage-proof in the useful direction: a stub engine that covers
    everything yields no finding even for the pairing that is known-bad by name.
    """
    hook = _uncovered(
        _program(tmp_path),
        older_gates_core,
        fallback="sensitivity-scoped",
        select=lambda real, config, hook_, ctx: type(real.select_critics(config, hook_, ctx))(
            **{**real.select_critics(config, hook_, ctx).__dict__, "critics": ("someone",)}
        ),
    )

    assert hook.self_owned_uncovered == (), (
        "the probe matched policy NAMES instead of asking the engine"
    )


def test_an_engine_that_raises_yields_no_finding_rather_than_a_false_one(
    tmp_path, older_gates_core
):
    """An unconfigured hook raises by design and is reported elsewhere; this probe
    must not turn that into a coverage claim in either direction."""

    def _raise(real, config, hook_, ctx):
        raise RuntimeError("hook not configured")

    hook = _uncovered(
        _program(tmp_path), older_gates_core, fallback="sensitivity-scoped", select=_raise
    )

    assert hook.self_owned_uncovered == ()


# ---------------------------------------------------------------------------
# the operator is told, on both surfaces


def test_doctor_reports_the_pairing_with_its_cause_and_remedy(tmp_path, older_gates_core):
    from otaman_cli.doctor import check_critic_policy

    root = _program(tmp_path)
    older_gates_core(hooks={"spec-proposal": ("stakeholder-affected", "sensitivity-scoped")})

    result = check_critic_policy(root)

    assert result["status"] == "warn"
    findings = [i for i in result.get("issues", []) if "self-owned" in i.get("message", "")]
    assert findings, f"doctor did not name the gap: {result.get('issues')}"
    (found,) = findings
    assert "a-agent" in found["message"]
    assert "stakeholder-affected" in found["message"], "the cause must name the primary"
    assert "sensitivity-scoped" in found["message"], "and the fallback"
    assert "role-based" in found["fix"], "the remedy must name a fallback that works"


def test_doctor_reports_a_REFUSED_config_rather_than_calling_it_unconfigured(tmp_path):
    """The refusal must not fail open: "not checked" with core's reason, never silence."""
    from otaman_cli.doctor import check_critic_policy

    root = _program(
        tmp_path,
        "hooks:\n  spec-proposal:\n    primary: stakeholder-affected\n"
        "    fallback: sensitivity-scoped\n",
    )

    result = check_critic_policy(root)

    assert result["status"] == "warn"
    assert "NOT CHECKED" in result["details"]["gates"]
    assert "role-based" in result["details"]["gates"]


def test_the_policy_surface_warns_too(tmp_path, capsys, monkeypatch, older_gates_core):
    import otaman_cli.commands.policy as P

    root = _program(tmp_path)
    older_gates_core(hooks={"spec-proposal": ("stakeholder-affected", "sensitivity-scoped")})
    monkeypatch.setattr(P, "find_project_root", lambda: root)

    P.cmd_policy(["critics"])

    out = capsys.readouterr()
    text = out.out + out.err
    assert "self-owned proposals by" in text
    assert "select NO critic" in text
    assert "out of scope" in text, "the operator is offered the explicit-acceptance option"
