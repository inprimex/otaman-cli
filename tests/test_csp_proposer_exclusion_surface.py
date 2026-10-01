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
"""

_GATES_NO_FALLBACK = """hooks:
  scr-critique:
    primary: stakeholder-affected
"""


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
        tmp_path, _PLATFORM_ONE_OWNER, "hooks:\n  outcome-review:\n    primary: consumer-chain\n"
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


def test_the_command_says_when_there_is_no_fallback(tmp_path, monkeypatch, capsys):
    """The worse case, and the one worth the louder wording: exclusion empties the set
    and nothing is declared to catch it."""
    from otaman_cli.commands import policy as policy_cmd

    root = _program(tmp_path, _PLATFORM_ONE_OWNER, _GATES_NO_FALLBACK)
    monkeypatch.setattr(policy_cmd, "find_project_root", lambda: root)

    policy_cmd.cmd_policy(["critics"])
    out = capsys.readouterr().out

    assert "NO fallback is declared" in out


# ---------------------------------------------------------------------------
# The doctor check.


def test_doctor_warns_on_a_lone_candidate_and_grades_by_fallback(tmp_path):
    """Higher severity without a fallback: with one, the proposals are decided by a
    declared policy; without one, they are decided by nothing."""
    from otaman_cli.doctor import check_critic_policy

    with_fb = check_critic_policy(
        _program(tmp_path / "a", _PLATFORM_ONE_OWNER, _GATES_WITH_FALLBACK)
    )
    (tmp_path / "b").mkdir()
    without = check_critic_policy(_program(tmp_path / "b", _PLATFORM_ONE_OWNER, _GATES_NO_FALLBACK))

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

    assert not {name for name in logic if "proposer" in name.lower()}, (
        "this surface names a proposer in its logic — selection is core's"
    )
    assert "select_critics" in logic, "selection is still delegated to core"


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
    for call in contexts:
        passed = {kw.arg for kw in call.keywords}
        assert "proposer" not in passed, (
            "the surface now names a proposer, so what it shows is POST-exclusion for "
            "that agent — PRE_EXCLUSION_NOTE is then a false label, not a cautious one"
        )
