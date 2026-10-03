"""The displayed critic set is PRE-EXCLUSION under csp's D4 invariant.

spec-agent ruled (20261001, from plugin's measured inversion) that D4's structural
independence is an invariant OVER policies rather than a policy. The csp delta now
carries it verbatim:

    NO policy may select the proposer as a critic of their own proposal: selection
    excludes the proposing agent, and when exclusion empties a policy's set the
    fallback policy applies.

`otaman policy critics` and the doctor check evaluate a hook WITHOUT a proposal, so
there is no proposer to exclude and the set they show is the one before exclusion. That
is not a defect — a proposal-independent view is what a config surface is for — but an
unlabelled one is read as the review roster, and a reader who takes it that way concludes
an agent reviews its own proposals: exactly the outcome the invariant forbids, read off a
surface that never claimed it.

Two consequences, both surfaced rather than left to inference:

* every evaluated set says it is pre-exclusion
* a hook resolving to ONE candidate has nobody for that agent's own proposals, so the
  fallback decides them — or nothing does, when no fallback is declared. Pre-dispatch,
  the same shape as sghc's guarded-route warning.

Not implemented here, deliberately: the exclusion itself. `select_critics` is core's
single home and core's engine has no proposer concept yet (verified in their committed
main). Re-deriving the exclusion in this surface would be the second opinion about
selection that single-home exists to prevent.
"""

from __future__ import annotations

from otaman_cli import critic_policy

_PLATFORM_TWO_OWNERS = """project: demo
repos:
  - {name: otaman-core, owner: core-agent}
  - {name: otaman-cli, owner: cli-agent}
"""

_PLATFORM_ONE_OWNER = """project: demo
repos:
  - {name: otaman-cli, owner: cli-agent}
"""

_GATES_WITH_FALLBACK = """hooks:
  scr-critique:
    primary: stakeholder-affected
    fallback: role-based
roles:
  reviewer-agent: [reviewer]
"""

#: A chain with no fallback, as an engine bundle rather than YAML: core #122 refuses to
#: parse it (no `role-based` arm can select for a self-owned proposal), so the only way
#: to reach cli's grading of the case is the pre-#122 shape built from core's dataclasses
#: — and the grading must stay correct, because cli does not get to assume core's version.
_NO_FALLBACK_HOOKS = {"scr-critique": ("stakeholder-affected", None)}


def _program(tmp_path, platform, gates):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "platform.yaml").write_text(platform, encoding="utf-8")
    (tmp_path / "verification-gates.yaml").write_text(gates, encoding="utf-8")
    return tmp_path


def _hook(surface, name="scr-critique"):
    return next(h for h in surface.hooks if h.hook == name)


# ---------------------------------------------------------------------------
# The label.


def test_the_pre_exclusion_note_names_the_invariant():
    """A note saying only "pre-exclusion" would leave the reader to guess what is
    excluded and why."""
    note = critic_policy.PRE_EXCLUSION_NOTE

    assert "proposer" in note
    assert "never its own critic" in note


def test_the_rendered_set_says_it_is_pre_exclusion(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    assert policy_cmd.cmd_policy(["critics"]) == 0
    out = capsys.readouterr().out

    assert "selects:" in out
    assert critic_policy.PRE_EXCLUSION_NOTE in out


def test_the_json_payload_says_it_too(tmp_path, monkeypatch, capsys):
    """A caller reading `critics` as the review roster would conclude an agent reviews
    its own proposals — so the payload carries the qualifier, not just the text view."""
    import json

    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    assert policy_cmd.cmd_policy(["critics", "--json"]) == 0
    hooks = json.loads(capsys.readouterr().out)["hooks"]

    assert all(h["critics_are_pre_exclusion"] is True for h in hooks)


# ---------------------------------------------------------------------------
# The single-candidate consequence.


def test_a_lone_candidate_is_reported(tmp_path):
    """One owner, so `stakeholder-affected` resolves to one critic — and that agent's
    own proposals empty the set entirely."""
    root = _program(tmp_path, _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)

    hook = _hook(critic_policy.load(root))

    assert hook.evaluated
    assert hook.critics == ("cli-agent",)
    assert hook.single_candidate == "cli-agent"


def test_two_candidates_is_not_a_finding(tmp_path):
    """With two, either one's proposals still leave the other — nothing to warn about,
    and a warning on every healthy hook is noise."""
    root = _program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK)

    assert _hook(critic_policy.load(root)).single_candidate == ""


def test_an_unevaluated_hook_claims_no_lone_candidate(tmp_path):
    """`consumer-chain` is not locally evaluable, so this surface does not know its set
    — and must not report a finding about a set it could not resolve.

    Through `load`, this passes whether or not the `evaluated` check exists, because an
    unevaluated hook's `critics` is empty and an empty tuple has no single element: the
    round-trip version of this guard is VACUOUS, which the sabotage pass showed by
    deleting the check and watching all thirteen tests pass. The direct test below is
    the one with teeth.
    """
    root = _program(
        tmp_path,
        _PLATFORM_ONE_OWNER,
        "hooks:\n  outcome-review:\n    primary: consumer-chain\n    fallback: role-based\n"
        "roles:\n  reviewer-agent: [reviewer]\n",
    )

    hook = _hook(critic_policy.load(root), "outcome-review")

    assert hook.evaluated is False
    assert hook.single_candidate == ""


def test_a_single_critic_on_an_unevaluated_hook_is_not_a_lone_candidate():
    """The invariant the `evaluated` flag carries, asserted directly.

    `critics` on an unevaluated hook is not a resolution — it is whatever this surface
    could not finish working out. Reporting one entry in it as "the only agent who
    qualifies" would be a finding derived from a set the surface itself says it does not
    know, which is the fail-open shape in miniature.
    """
    unresolved = critic_policy.HookView(
        hook="outcome-review", primary="consumer-chain", critics=("core-agent",), evaluated=False
    )
    resolved = critic_policy.HookView(
        hook="scr-critique", primary="role-based", critics=("core-agent",), evaluated=True
    )

    assert unresolved.single_candidate == ""
    assert resolved.single_candidate == "core-agent"


def test_the_command_warns_and_names_the_fallback(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    policy_cmd.cmd_policy(["critics"])
    out = capsys.readouterr().out

    assert "only cli-agent qualifies" in out
    assert "fallback role-based decides those" in out


def test_the_command_says_when_there_is_no_fallback(
    tmp_path, monkeypatch, capsys, older_gates_core
):
    """The worse case, and the one worth the louder wording: exclusion empties the set
    and nothing is declared to catch it."""
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)
    older_gates_core(hooks=_NO_FALLBACK_HOOKS)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    policy_cmd.cmd_policy(["critics"])
    out = capsys.readouterr().out

    assert "NO fallback is declared" in out


# ---------------------------------------------------------------------------
# The doctor check.


def test_doctor_warns_on_a_lone_candidate_and_grades_by_fallback(tmp_path, older_gates_core):
    """Higher severity without a fallback: with one, the proposals are decided by a
    declared policy; without one, they are decided by nothing."""
    from otaman_cli.doctor import check_critic_policy

    with_fb = check_critic_policy(
        _program(tmp_path / "a", _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)
    )
    (tmp_path / "b").mkdir()
    older_gates_core(hooks=_NO_FALLBACK_HOOKS)
    without = check_critic_policy(
        _program(tmp_path / "b", _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)
    )

    assert with_fb["status"] == "warn"
    assert any("only cli-agent qualifies" in i["message"] for i in with_fb["issues"])
    assert [i["severity"] for i in with_fb["issues"] if "only cli-agent" in i["message"]] == [
        "medium"
    ]
    assert [i["severity"] for i in without["issues"] if "only cli-agent" in i["message"]] == [
        "high"
    ]


def test_doctor_states_that_the_sets_are_pre_exclusion(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    result = check_critic_policy(_program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK))

    assert result["details"]["critics_are"] == critic_policy.PRE_EXCLUSION_NOTE


def test_doctor_is_quiet_on_a_healthy_hook(tmp_path):
    from otaman_cli.doctor import check_critic_policy

    result = check_critic_policy(_program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK))

    assert not any("qualifies" in i["message"] for i in result.get("issues", []))


# ---------------------------------------------------------------------------
# What this surface does NOT do.


def test_the_exclusion_itself_is_not_implemented_here():
    """`select_critics` is core's single home, and re-deriving the exclusion here would
    be the second opinion about selection that single-home exists to prevent — the same
    mistake plugin avoided by holding rather than following their instruction literally.

    Checked on IDENTIFIERS, not on text: `PRE_EXCLUSION_NOTE` contains the word
    "proposer" by design, and a text search cannot tell a sentence from a decision. A
    string is data; a name is logic.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(critic_policy.__file__).read_text(encoding="utf-8"))
    logic = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            logic.add(node.id)
        elif isinstance(node, ast.Attribute):
            logic.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            logic.add(node.arg)
        elif isinstance(node, ast.arg):
            logic.add(node.arg)

    # REFINED 2026-10-03, and narrowly. The rule is "cli does not RE-DERIVE the
    # exclusion"; the original encoding was "the word proposer appears in no
    # identifier", a proxy that also forbids DELEGATING — passing `proposer=` INTO
    # core's SelectionContext so core applies its own invariant. The coverage probe
    # (`_self_owned_uncovered`, plugin's csp finding 20261003T032605) must do exactly
    # that: it asks what the configured chain selects for a self-owned proposal, and
    # the question is meaningless without naming the proposer.
    #
    # So `proposer` is allowed ONLY as a keyword argument to a SelectionContext call.
    # Anywhere else — a local binding, a comparison, a comprehension filter — is
    # re-derivation and still fails. The sabotage the original guard existed to catch
    # (cli removing the proposer from a candidate set itself) trips this one too; that
    # is verified in `test_a_locally_derived_exclusion_still_fails_the_guard`.
    # BOUND names are the tell, and this is where the first refinement was too loose:
    # subtracting every SelectionContext keyword from the whole module's identifiers
    # let a local `proposer = ...` through, because the probe legitimately passes
    # `proposer=` elsewhere. Verified by sabotage — cli filtering the proposer out of
    # core's result was caught only by behavioural tests, not by this guard, which is
    # the one whose job it is.
    #
    # So: a proposer may be PASSED (a keyword at a SelectionContext call) and never
    # BOUND (assigned, or taken as a parameter). Deriving an exclusion requires a
    # binding; delegating one does not.
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    bound.add(sub.id)
        elif isinstance(node, ast.For):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    bound.add(sub.id)
    offenders = {name for name in bound if "proposer" in name.lower()}
    assert not offenders, (
        f"this surface BINDS a proposer ({sorted(offenders)}) — deriving the exclusion "
        "needs a binding, delegating it does not; pass proposer= into SelectionContext"
    )
    assert {n for n in logic if "proposer" in n.lower()}, (
        "the coverage probe no longer names a proposer at all — it cannot be asking "
        "what a self-owned proposal selects"
    )
    assert "select_critics" in logic, "selection is still delegated to core"


def _is_selection_context(node) -> bool:
    """Whether *node* is a `SelectionContext(...)` call, however it is referenced."""
    import ast as _ast

    func = node.func
    if isinstance(func, _ast.Name):
        return func.id.endswith("SelectionContext")
    if isinstance(func, _ast.Attribute):
        return func.attr == "SelectionContext"
    return False


def test_a_locally_derived_exclusion_still_fails_the_guard():
    """The refinement above did not loosen the rule it refines.

    Re-derivation looks like a local `proposer` binding and a filter over candidates.
    Compiled here rather than described, so the guard is tested against the shape it
    exists to reject rather than against my claim about it.
    """
    import ast

    reimplementation = ast.parse(
        "def pick(core, config, hook, ctx, proposer):\n"
        "    result = core.select_critics(config, hook, ctx)\n"
        "    return tuple(c for c in result.critics if c != proposer)\n"
    )
    logic = set()
    for node in ast.walk(reimplementation):
        if isinstance(node, ast.Name):
            logic.add(node.id)
        elif isinstance(node, ast.Attribute):
            logic.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            logic.add(node.arg)
        elif isinstance(node, ast.arg):
            logic.add(node.arg)
    bound = {n.arg for n in ast.walk(reimplementation) if isinstance(n, ast.arg)}
    for node in ast.walk(reimplementation):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)

    assert {n for n in bound if "proposer" in n.lower()}, (
        "the refined guard would accept a locally derived exclusion — it must not"
    )
    assert logic, "sanity: the sample parsed"


def test_the_label_is_true_by_construction_not_by_cores_state():
    """Why the label stays correct however core evolves.

    I first wrote this as "core's engine has no proposer concept yet", reading their
    source — and it failed, because core is implementing the exclusion right now: their
    `verification_gates.py` has `SelectionContext.proposer` in the WORKING TREE and
    nothing in committed main (`git show main:` finds zero hits). A test that asserts a
    sibling's uncommitted state is unstable by construction and would pass or fail on
    whichever revision CI happened to check out.

    The real reason the label holds is local and permanent: this surface builds a
    selection context WITHOUT a proposer, because it evaluates a hook rather than a
    proposal. So whatever core does with `ctx.proposer`, what comes back here is the
    unexcluded set — which is what the note says.
    """
    import ast
    import pathlib

    src = pathlib.Path(critic_policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    contexts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("SelectionContext")
    ]
    contexts += [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SelectionContext"
    ]

    assert contexts, "this surface no longer builds a SelectionContext — re-read the note"

    # Scoped to the context that produces the RENDERED set. `_view` builds that one
    # and must stay proposer-free, or PRE_EXCLUSION_NOTE becomes a false label rather
    # than a cautious one. `_self_owned_uncovered` builds a different context, with a
    # proposer, for a different question (does a self-owned proposal get a critic) —
    # and its answer is reported separately, never as `view.critics`.
    rendering_funcs = {"_view", "load"}
    probe_funcs = {"_self_owned_uncovered"}
    seen_probe_context = False
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
            if not _is_selection_context(call):
                continue
            passed = {kw.arg for kw in call.keywords}
            if func.name in rendering_funcs:
                assert "proposer" not in passed, (
                    f"{func.name} now names a proposer, so what it renders is "
                    "POST-exclusion for that agent — PRE_EXCLUSION_NOTE is then a "
                    "false label, not a cautious one"
                )
            if func.name in probe_funcs:
                seen_probe_context = True
                assert "proposer" in passed, (
                    f"{func.name} asks whether a SELF-OWNED proposal selects anyone; "
                    "without a proposer it asks nothing and would report false coverage"
                )
    assert seen_probe_context, "the coverage probe no longer builds a context — re-read it"


# ---------------------------------------------------------------------------
# Whether the installed engine actually KEEPS the invariant the note asserts.


def test_the_invariant_is_enforced_by_the_installed_engine():
    """core #104 landed it. Probed on `SelectionResult.excluded_proposer` rather than a
    version, per attribute-probe adoption: that field exists because core records the
    exclusion as a fact, so its presence is the engine's own statement that it does."""
    assert critic_policy.invariant_enforced() is True


def test_a_bundle_without_the_invariant_is_reported_not_assumed(monkeypatch):
    """The gap the note would otherwise hide. `PRE_EXCLUSION_NOTE` states CANON; before
    core #104 nothing applied it, so a reader trusting the note on an older bundle would
    believe an exclusion that never happens — the same shape as the generation-stamp
    absence and the security-gate-record probe."""
    from otaman_cli.doctor import check_critic_policy

    monkeypatch.setattr(critic_policy, "invariant_enforced", lambda: False)
    import tempfile

    root = _program(
        __import__("pathlib").Path(tempfile.mkdtemp()),
        _PLATFORM_TWO_OWNERS,
        _GATES_WITH_FALLBACK,
    )
    result = check_critic_policy(root)

    assert result["status"] == "warn"
    assert critic_policy.INVARIANT_NOT_ENFORCED in result["details"]["proposer_exclusion"]
    assert any(i["severity"] == "high" for i in result["issues"])


def test_the_not_enforced_path_does_not_crash(monkeypatch, tmp_path):
    """Written because the first version appended to `issues` three lines before it was
    declared — an UnboundLocalError on exactly the old-bundle path the check exists to
    report. A reporting path that crashes reports nothing."""
    from otaman_cli.doctor import check_critic_policy

    monkeypatch.setattr(critic_policy, "invariant_enforced", lambda: False)
    result = check_critic_policy(_program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK))

    assert isinstance(result.get("issues"), list) and result["issues"]


def test_the_command_says_when_the_engine_does_not_enforce_it(monkeypatch, tmp_path, capsys):
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(critic_policy, "invariant_enforced", lambda: False)

    policy_cmd.cmd_policy(["critics"])
    out = capsys.readouterr().out

    assert "does not implement proposer exclusion" in out


def test_the_enforced_case_is_quiet(monkeypatch, tmp_path, capsys):
    """A line on every healthy run is noise — the enforcement note belongs in doctor's
    details, not in the operator's face."""
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_TWO_OWNERS, _GATES_WITH_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(critic_policy, "invariant_enforced", lambda: True)

    policy_cmd.cmd_policy(["critics"])

    assert "does not implement" not in capsys.readouterr().out


def test_an_absent_core_is_not_enforcement(monkeypatch):
    """No engine is not a compliant engine — the same absent-vs-unreadable rule this
    repo has applied at five other sites."""
    monkeypatch.setattr(critic_policy, "_core", lambda: None)

    assert critic_policy.invariant_enforced() is False


def test_the_probe_reads_the_engine_rather_than_asserting_the_answer(monkeypatch):
    """The guard the others could not give.

    Every "not enforced" test above monkeypatches `invariant_enforced` itself, so none
    of them exercises the probe — replacing its body with `return True` left all twenty
    passing. This drives a STUB ENGINE through the real probe: one whose result carries
    `excluded_proposer` and one whose does not, which is the difference between a core
    that records the exclusion and a core that predates it.
    """
    from dataclasses import dataclass

    @dataclass
    class _Old:
        policy: str = ""
        fell_back: bool = False

    @dataclass
    class _New:
        policy: str = ""
        fell_back: bool = False
        excluded_proposer: bool = False

    class _Engine:
        def __init__(self, result):
            self.SelectionResult = result

    monkeypatch.setattr(critic_policy, "_core", lambda: _Engine(_Old))
    assert critic_policy.invariant_enforced() is False, "a pre-#104 engine reads as enforcing"

    monkeypatch.setattr(critic_policy, "_core", lambda: _Engine(_New))
    assert critic_policy.invariant_enforced() is True


def test_an_engine_with_no_result_type_is_not_enforcement(monkeypatch):
    """A bundle whose module exists but carries no `SelectionResult` cannot be read as
    compliant — unreadable is not finished, here as everywhere else today."""

    class _Engine:
        pass

    monkeypatch.setattr(critic_policy, "_core", lambda: _Engine())

    assert critic_policy.invariant_enforced() is False
