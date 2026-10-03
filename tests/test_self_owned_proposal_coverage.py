"""A self-owned proposal can resolve to NO critic, and only the fallback decides.

plugin-agent's csp 1.2 finding (20261003T032605), reproduced here through core's
committed engine rather than taken from the message:

    primary=stakeholder-affected  fallback=sensitivity-scoped  -> critics=()
    primary=stakeholder-affected  fallback=role-based          -> critics=('b-agent',)

Same invariant (`excluded_proposer: True`), same primary, same inputs, same proposer.
An agent proposing a change to the repo it owns is the commonest proposal shape in the
fleet: the primary selects that repo's owner, D4's invariant excludes the proposer, and
`sensitivity-scoped` returns nothing when no sensitivity class is set — which is the
ordinary case. `parse_verification_gates` accepts either pairing, so the tenant gets
silent non-coverage and the runtime reports `no-eligible-critic`, which is honest about
the outcome and says nothing about the cause being a config pairing.

The spec wording is spec-agent's and a parse-time refusal is core's. cli's half is to
name the cause BEFORE a proposal hits it — measured by asking the engine, never by
pattern-matching policy names, so a policy added later is covered too.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli import critic_policy


def _program(tmp_path: Path, *, primary: str, fallback: str | None) -> Path:
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n"
        "  - name: a\n    path: ../a\n    owner: a-agent\n"
        "  - name: b\n    path: ../b\n    owner: b-agent\n",
        encoding="utf-8",
    )
    hook = f"    primary: {primary}\n"
    if fallback:
        hook += f"    fallback: {fallback}\n"
    (root / critic_policy.CONFIG_NAME).write_text(
        "hooks:\n  spec-proposal:\n" + hook + "roles:\n  reviewer: b-agent\n",
        encoding="utf-8",
    )
    return root


def _hook(root: Path):
    surface = critic_policy.load(root)
    assert surface.configured and not surface.error, surface.error
    (hook,) = [h for h in surface.hooks if h.hook == "spec-proposal"]
    return hook


# ---------------------------------------------------------------------------
# the finding, both arms


def test_a_sensitivity_scoped_fallback_leaves_self_owned_proposals_uncovered(tmp_path):
    hook = _hook(_program(tmp_path, primary="stakeholder-affected", fallback="sensitivity-scoped"))

    assert set(hook.self_owned_uncovered) == {"a-agent", "b-agent"}, (
        "both owners' own proposals select nobody under this pairing"
    )


def test_a_role_based_fallback_yields_NO_FINDING_because_cli_cannot_know(tmp_path):
    """The honest answer, and it corrected a pre-existing false report.

    plugin measured `fallback: role-based` SELECTING a critic — with `agent_roles` and
    `target_role` supplied, which a live gate has and a repo checkout does not:
    `parse_verification_gates` carries only `clearances` and `hooks`, so there is no
    roles table in the config at all.

    cli nevertheless listed `role-based` as locally evaluable, so a `role-based` hook
    rendered `critics=()` / "role-based selected nobody" / `evaluated=True` — asserting
    the hook selects nobody when the truth was that cli could not know. Fixed with this
    change; `role-based` now takes the not-evaluated path.

    So this pairing yields NO coverage finding: a finding without the inputs to know
    would push a tenant to change a config that may be working.
    """
    hook = _hook(_program(tmp_path, primary="stakeholder-affected", fallback="role-based"))

    assert hook.self_owned_uncovered == (), (
        f"cli claimed knowledge it does not have: {hook.self_owned_uncovered}"
    )


def test_a_role_based_hook_is_reported_as_not_evaluated_not_as_empty(tmp_path):
    """The pre-existing false report, pinned so it cannot come back."""
    root = _program(tmp_path, primary="role-based", fallback=None)

    hook = _hook(root)

    assert hook.evaluated is False, "cli cannot evaluate role-based from a checkout"
    assert hook.critics == ()
    assert "not evaluated" in hook.note, hook.note
    assert "selected nobody" not in hook.note, (
        "an unevaluable policy must not be reported as having selected nobody"
    )


def test_no_fallback_at_all_is_also_uncovered(tmp_path):
    """Nothing decides them — the case the surface's `single_candidate` note predicts."""
    hook = _hook(_program(tmp_path, primary="stakeholder-affected", fallback=None))

    assert "a-agent" in hook.self_owned_uncovered


# ---------------------------------------------------------------------------
# it is ASKED, not pattern-matched


def test_the_probe_runs_the_engine_rather_than_matching_policy_names(tmp_path, monkeypatch):
    """A policy core adds later must be covered without touching cli.

    So the probe must call `select_critics`, not compare `primary`/`fallback` against a
    hard-coded list. Sabotage-proof in the useful direction: a stub engine that covers
    everything yields no finding even for the pairing that is known-bad by name.
    """
    root = _program(tmp_path, primary="stakeholder-affected", fallback="sensitivity-scoped")
    real_core = critic_policy._core()

    class _AlwaysCovers:
        SelectionContext = real_core.SelectionContext
        parse_verification_gates = staticmethod(real_core.parse_verification_gates)
        POLICIES = real_core.POLICIES
        CONFIG_NAME = getattr(real_core, "CONFIG_NAME", "verification-gates.yaml")

        @staticmethod
        def select_critics(config, hook, ctx):
            res = real_core.select_critics(config, hook, ctx)
            return type(res)(**{**res.__dict__, "critics": ("someone",)})

    monkeypatch.setattr(critic_policy, "_core", lambda: _AlwaysCovers)

    assert _hook(root).self_owned_uncovered == (), (
        "the probe matched policy NAMES instead of asking the engine"
    )


def test_an_engine_that_raises_yields_no_finding_rather_than_a_false_one(tmp_path, monkeypatch):
    """An unconfigured hook raises by design and is reported elsewhere; this probe
    must not turn that into a coverage claim in either direction."""
    root = _program(tmp_path, primary="stakeholder-affected", fallback="sensitivity-scoped")
    real_core = critic_policy._core()

    class _Raises:
        SelectionContext = real_core.SelectionContext
        parse_verification_gates = staticmethod(real_core.parse_verification_gates)
        POLICIES = real_core.POLICIES

        @staticmethod
        def select_critics(config, hook, ctx):
            raise RuntimeError("hook not configured")

    monkeypatch.setattr(critic_policy, "_core", lambda: _Raises)

    assert _hook(root).self_owned_uncovered == ()


# ---------------------------------------------------------------------------
# the operator is told, on both surfaces


def test_doctor_reports_the_pairing_with_its_cause_and_remedy(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    root = _program(tmp_path, primary="stakeholder-affected", fallback="sensitivity-scoped")

    result = check_critic_policy(root)

    assert result["status"] == "warn"
    findings = [i for i in result.get("issues", []) if "self-owned" in i.get("message", "")]
    assert findings, f"doctor did not name the gap: {result.get('issues')}"
    (found,) = findings
    assert "a-agent" in found["message"]
    assert "stakeholder-affected" in found["message"], "the cause must name the primary"
    assert "sensitivity-scoped" in found["message"], "and the fallback"
    assert "role-based" in found["fix"], "the remedy must name a fallback that works"


def test_the_policy_surface_warns_too(tmp_path, capsys):
    import otaman_cli.commands.policy as P

    root = _program(tmp_path, primary="stakeholder-affected", fallback="sensitivity-scoped")
    P.find_project_root = lambda: root

    P.cmd_policy(["critics"])

    out = capsys.readouterr()
    text = out.out + out.err
    assert "self-owned proposals by" in text
    assert "select NO critic" in text
    assert "out of scope" in text, "the operator is offered the explicit-acceptance option"
