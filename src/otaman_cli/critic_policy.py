"""critic-selection-policy 1.3 — the config surface for verification gates.

Core owns the engine: the four named policies, the clearance gate, the fallback
chain, and `SelectionResult` recording which policy fired. This is the cli side —
reading `verification-gates.yaml`, showing what each hook will do, and listing
who is cleared for what.

Three invariants inherited from core and deliberately NOT re-implemented here:

* the clearance gate drops an uncleared critic when the content carries a
  sensitivity class, and `dropped_uncleared` names them — a dropped critic
  renders as not-run WITH its reason, never as a shorter list;
* an unconfigured hook RAISES rather than returning no critics, because "no
  critics configured" and "this hook selected nobody" mean opposite things;
* every result names the policy that fired.

Re-deriving any of those would be a second opinion about selection, which is
exactly the drift `select_critics` exists as a single home to prevent.

What this surface can and cannot evaluate is worth stating. A policy's inputs
come from dispatch — the candidates, the sensitivity class, the consumers of a
change. Two of them are derivable from the program itself: `stakeholder-affected`
needs repo owners, which platform.yaml already declares, and `role-based` needs
agent roles. The other two cannot be answered without a live gate, so this shows
their configured chain and says so rather than inventing a context and
presenting the result as if it were real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Where the gate config lives. The spec makes this the single home — the hook-c
#: `security-gates:` block migrates here from platform.yaml (plugin's csp 1.2),
#: which is why the name is not "critics.yaml".
CONFIG_NAME = "verification-gates.yaml"

#: Policies whose inputs this surface can derive from the program itself.
#: Policies this surface can resolve from what a repo checkout holds. ONLY
#: `stakeholder-affected`: its inputs are the affected repos and their owners, both in
#: platform.yaml.
#:
#: `role-based` was listed here and should not have been. Its inputs are `agent_roles`
#: and `target_role`, which live in the GATE's context — `parse_verification_gates`
#: carries only `clearances` and `hooks`, so there is no roles table in the config at
#: all. The effect was a false report, measured 2026-10-03: a `role-based` hook
#: rendered `critics=()` with "role-based selected nobody" and `evaluated=True`, which
#: asserts the hook selects no one when the truth is that this surface cannot know.
#: That is the exact NOT-CHECKED/no-critics conflation this module's docstring says it
#: exists to prevent, so it now takes the not-evaluated path and says so.
_LOCALLY_EVALUABLE = ("stakeholder-affected",)

#: Said wherever an evaluated critic set is shown. The set is proposal-independent and
#: the invariant is proposal-dependent, so a reader who takes one for the other
#: concludes that an agent reviews its own proposals — the exact outcome the invariant
#: forbids, read off a surface that never claimed it.
PRE_EXCLUSION_NOTE = "before proposer exclusion — the proposing agent is never its own critic"

#: Said when the installed engine does NOT implement the invariant the note above
#: asserts. The note states CANON; whether this bundle enforces it is a separate fact,
#: and before core #104 nothing did — so a reader trusting the note on an older bundle
#: would believe an exclusion that never happens. Same shape as the generation-stamp
#: absence and the security-gate-record probe: a claim the surface makes, checked
#: against the code that would have to keep it.
INVARIANT_NOT_ENFORCED = (
    "this bundle's selection engine does not implement proposer exclusion "
    "(core #104) — the rule is canon and nothing applies it here"
)


def invariant_enforced() -> bool:
    """Whether the installed engine removes the proposer from a selection.

    Probed on the RESULT's `excluded_proposer` marker rather than on a version, per
    attribute-probe adoption: that field exists precisely because core records the
    exclusion as a fact, so its presence is the engine's own statement that it does.
    """
    core = _core()
    if core is None:
        return False
    result = getattr(core, "SelectionResult", None)
    fields = getattr(result, "__dataclass_fields__", {}) if result is not None else {}
    return "excluded_proposer" in fields


@dataclass
class HookView:
    """One hook, as the config surface renders it."""

    hook: str
    primary: str
    fallback: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    #: Critics this surface could actually resolve, when the policy's inputs are
    #: derivable locally. Empty with a stated reason otherwise.
    #:
    #: PRE-EXCLUSION. The csp delta now carries D4 as an invariant OVER policies
    #: (ruling 20261001, from plugin's measured inversion): "NO policy may select the
    #: proposer as a critic of their own proposal: selection excludes the proposing
    #: agent, and when exclusion empties a policy's set the fallback policy applies."
    #: This surface evaluates a hook WITHOUT a proposal, so there is no proposer to
    #: exclude and the set it shows is the one before exclusion — which is why the
    #: rendering says so rather than letting a reader take it for the review roster.
    critics: tuple[str, ...] = ()
    evaluated: bool = False
    note: str = ""
    #: Declared repo owners whose OWN proposal this hook's chain selects nobody for
    #: (plugin-agent's csp finding 20261003T032605, reproduced here through core's
    #: engine). An agent proposing a change to the repo it owns is the commonest
    #: proposal shape in the fleet, and under the D4 invariant the primary empties for
    #: it — so the FALLBACK alone decides whether it is reviewed at all. Measured, not
    #: inferred from policy names, so a policy added later is covered too.
    self_owned_uncovered: tuple[str, ...] = ()

    @property
    def single_candidate(self) -> str:
        """The lone critic, when this hook resolves to exactly one.

        That agent's own proposals empty the set under the invariant, so the fallback
        decides them — or nothing does, when no fallback is declared. A pre-dispatch
        finding rather than a runtime surprise, the same shape as sghc's
        guarded-route warning.
        """
        return self.critics[0] if self.evaluated and len(self.critics) == 1 else ""


@dataclass
class Roster:
    """Who is cleared for which sensitivity classes."""

    rows: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    classes: tuple[str, ...] = ()


@dataclass
class Surface:
    """What `otaman policy critics` and the doctor check render."""

    hooks: list[HookView] = field(default_factory=list)
    roster: Roster = field(default_factory=Roster)
    error: str = ""
    configured: bool = False


def _core() -> Any | None:
    """core's verification-gates engine, or None on an older install."""
    try:
        from otaman_core import verification_gates
    except Exception:  # noqa: BLE001 - absent → not-checked, never "no gates"
        return None
    needed = ("parse_verification_gates", "select_critics", "SelectionContext")
    return verification_gates if all(hasattr(verification_gates, n) for n in needed) else None


def config_path(root: Path) -> Path:
    return root / CONFIG_NAME


def _repo_owners(root: Path) -> dict[str, str]:
    """`{repo: owner-agent}` from platform.yaml — what stakeholder-affected needs."""
    try:
        import yaml

        doc = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - unreadable → nothing derivable, said below
        return {}
    owners: dict[str, str] = {}
    for repo in doc.get("repos") or []:
        if isinstance(repo, dict):
            name = str(repo.get("name") or "").strip()
            owner = str(repo.get("owner") or repo.get("agent") or "").strip()
            if name and owner:
                owners[name] = owner
    return owners


def load(root: Path, *, sensitivity: str | None = None) -> Surface:
    """Read and parse the gate config for *root*.

    *sensitivity* previews what a hook does when the content carries that class:
    the override policy applies and the clearance gate drops uncleared critics.
    Without it the clearance gate never runs, so the dropped-critic rendering
    would be unreachable — which is how I found it was: a sabotage run on that
    branch changed nothing, because no test could reach it.
    """
    surface = Surface()
    core = _core()
    if core is None:
        surface.error = (
            "otaman-core does not carry the verification-gates engine "
            "(critic-selection-policy 1.1) — cannot evaluate; update the bundle"
        )
        return surface

    path = config_path(root)
    if not path.is_file():
        return surface  # no gate config — nothing declared, which is not a failure

    surface.configured = True
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable is NOT absent
        surface.error = f"{CONFIG_NAME} exists but could not be read: {type(exc).__name__}"
        return surface

    try:
        config = core.parse_verification_gates(raw)
    except Exception as exc:  # noqa: BLE001 - core raises with the reason
        # Exists and does not parse: the gates cannot be evaluated. Reporting
        # "no gates configured" here is the fail-open shape this fleet has now
        # corrected three times (cli #223, #227).
        surface.error = f"{CONFIG_NAME} exists but does not parse — {exc}"
        return surface

    owners = _repo_owners(root)
    for name in sorted(config.hooks):
        surface.hooks.append(_view(core, config, name, owners, sensitivity))
    surface.roster = _roster(config)
    return surface


def _view(
    core: Any,
    config: Any,
    hook: str,
    owners: dict[str, str],
    sensitivity: str | None = None,
) -> HookView:
    """One hook's configured chain, evaluated where its inputs are derivable."""
    policy = config.hooks[hook]
    view = HookView(
        hook=hook,
        primary=policy.primary,
        fallback=policy.fallback,
        overrides=dict(policy.sensitivity_overrides or {}),
    )
    effective = (policy.sensitivity_overrides or {}).get(sensitivity or "", policy.primary)
    if effective not in _LOCALLY_EVALUABLE and effective != "sensitivity-scoped":
        view.note = (
            f"{effective} selects over inputs only a live gate has "
            "(candidates, sensitivity, consumers) — configured chain shown, not evaluated"
        )
        return view
    if effective == "stakeholder-affected" and not owners:
        view.note = "no repo owners declared in platform.yaml — nothing to select over"
        return view

    context = core.SelectionContext(
        affected_repos=tuple(sorted(owners)),
        repo_owners=owners,
        candidates=tuple(sorted(owners.values())),
        sensitivity=sensitivity,
    )
    try:
        result = core.select_critics(config, hook, context)
    except Exception as exc:  # noqa: BLE001 - an unconfigured hook RAISES by design
        view.note = f"selection could not run: {exc}"
        return view
    view.critics = tuple(result.critics)
    view.evaluated = True
    if getattr(result, "dropped_uncleared", ()):
        # Named, never silently absent from the list. This is the clearance gate
        # removing a critic the policy DID pick.
        view.note = "dropped for missing clearance: " + ", ".join(result.dropped_uncleared)
    elif not view.critics:
        # Selecting nobody is the other way a gate goes quiet, and core
        # distinguishes it from a drop: `sensitivity-scoped` picks only cleared
        # agents, so it never "drops" — it simply finds none. Rendering an empty
        # list with no reason is the silent case either way, so it gets one.
        reason = f"{result.policy} selected nobody"
        if sensitivity:
            reason += f" — no candidate declares clearance for {sensitivity!r}"
        view.note = reason
    elif getattr(result, "sensitivity_override", False):
        view.note = f"sensitivity {sensitivity!r} replaced the primary with {result.policy}"
    elif getattr(result, "fell_back", False):
        view.note = f"primary selected nobody — fallback {policy.fallback!r} ran"
    view.self_owned_uncovered = _self_owned_uncovered(core, config, hook, owners, sensitivity)
    return view


def _self_owned_uncovered(
    core: Any,
    config: Any,
    hook: str,
    owners: dict[str, str],
    sensitivity: str | None,
) -> tuple[str, ...]:
    """Owners whose own proposal this hook selects NOBODY for.

    plugin-agent measured (csp 1.2, 20261003T032605) that with
    `primary: stakeholder-affected` and `fallback: sensitivity-scoped` — the pairing
    the requirement text reads most naturally — a proposal affecting only the
    proposer's own repo resolves to no critic: the primary selects the affected
    repo's owner, the D4 invariant excludes the proposer, and the fallback returns
    nothing because no sensitivity class is set. Reproduced here through core's
    committed engine: the same inputs with `fallback: role-based` select a critic.
    So the fallback choice alone decides whether a whole class of proposal is
    reviewed, and `parse_verification_gates` accepts either pairing.

    ASKED, not pattern-matched: this runs the real `select_critics` once per declared
    owner with that owner as the proposer and their repo as the only affected one.
    A policy added to core later is therefore covered without touching this, and a
    tenant whose roster makes the pairing survivable is not warned for nothing.
    """
    # Every arm of the chain must be locally evaluable, or the coverage question is
    # OPEN and this returns nothing. A finding produced without the inputs to know
    # would push a tenant to change a config that may be working — worse than silence,
    # and the same rule as the not-evaluated note above.
    policy = config.hooks[hook]
    arms = [policy.primary, *([policy.fallback] if policy.fallback else [])]
    arms += list((policy.sensitivity_overrides or {}).values())
    if any(a not in _LOCALLY_EVALUABLE and a != "sensitivity-scoped" for a in arms):
        return ()

    uncovered: list[str] = []
    for repo, owner in sorted(owners.items()):
        context = core.SelectionContext(
            affected_repos=(repo,),
            repo_owners=owners,
            candidates=tuple(sorted(owners.values())),
            sensitivity=sensitivity,
            proposer=owner,
        )
        try:
            result = core.select_critics(config, hook, context)
        except Exception:  # noqa: BLE001 - an unconfigured hook is reported elsewhere
            return ()
        if not tuple(result.critics):
            uncovered.append(owner)
    return tuple(dict.fromkeys(uncovered))


def _roster(config: Any) -> Roster:
    rows = sorted((agent, tuple(classes)) for agent, classes in (config.clearances or {}).items())
    classes = sorted({c for _, cs in rows for c in cs})
    return Roster(rows=rows, classes=tuple(classes))


__all__ = ["CONFIG_NAME", "HookView", "Roster", "Surface", "config_path", "load"]
