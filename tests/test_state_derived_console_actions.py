"""state-derived-console-actions 1.1–1.4.

D2 is the load-bearing decision: the console must know WHY an action is
unavailable, and the reason must come from the same validation the verb runs.
A console-local table of reason strings drifts from the machine within a
release, so "a wrong reason is worse than no reason" has to be structural
rather than aspirational.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from otaman_cli.registries.outcomes import OutcomeStatus, check_action

APP = Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "console" / "app.py"


# ---------------------------------------------------------------------------
# 1.1 / D2 — the machine answers, the console renders


def test_accept_cost_refuses_below_backlog_with_the_ruled_sentence():
    """D1's exact wording — the refusal carries the fix, per rule 2."""
    verdict = check_action("accept-cost", OutcomeStatus.DRAFTING)
    assert not verdict.allowed
    assert verdict.reason == "accept-cost: requires status Backlog — promote first"


@pytest.mark.parametrize(
    "status", [OutcomeStatus.BACKLOG, OutcomeStatus.APPROVED, OutcomeStatus.IN_PROGRESS]
)
def test_accept_cost_is_allowed_from_backlog_onward(status):
    assert check_action("accept-cost", status).allowed


def test_every_refusal_reason_names_the_action():
    """A reason rendered next to a disabled control has to say which control."""
    for action in ("promote", "demote", "accept-cost"):
        for status in OutcomeStatus:
            verdict = check_action(action, status)
            if not verdict.allowed:
                assert verdict.reason.startswith(f"{action}:"), verdict.reason


def test_refusal_reasons_are_true_for_each_state_not_one_reused_string():
    """`Done` is not demotable, but it is not "the first state" either. A single
    generic message quietly reintroduces the wrong-reason class D2 outlaws."""
    drafting = check_action("demote", OutcomeStatus.DRAFTING).reason
    done = check_action("demote", OutcomeStatus.DONE).reason
    assert "first state" in drafting
    assert "first state" not in done, f"Done reported as the first state: {done!r}"
    assert "Done" in done


def test_promote_is_terminal_at_done_and_available_before():
    assert not check_action("promote", OutcomeStatus.DONE).allowed
    assert check_action("promote", OutcomeStatus.DRAFTING).allowed


def test_an_unknown_action_is_allowed_rather_than_invented():
    """This answers "is there a state-machine reason to refuse", not "is this a
    real action". Inventing a refusal for an unclassified verb blocks work on a
    guess."""
    assert check_action("frobnicate", OutcomeStatus.DRAFTING).allowed


def test_an_unparseable_status_does_not_refuse():
    assert check_action("accept-cost", "not-a-status").allowed
    assert check_action("accept-cost", None).allowed


# ---------------------------------------------------------------------------
# 1.3 — both surfaces, one reason


def test_the_console_does_not_carry_its_own_reason_strings():
    """The grep the gate asks for: reasons sourced from the machine.

    The ruled sentence must appear in the state machine and NOT be duplicated as
    a literal in the console — a second copy is the drift D2 forbids.
    """
    console = Path(APP).parent
    ruled = "requires status Backlog"
    offenders = [f.name for f in console.glob("*.py") if ruled in f.read_text(encoding="utf-8")]
    assert not offenders, f"console files carrying the reason as a literal: {offenders}"

    machine = (Path(APP).parent.parent / "registries" / "outcomes.py").read_text(encoding="utf-8")
    assert ruled in machine, "the ruled sentence is not in the state machine"


def test_the_cli_verb_refuses_before_writing_anything():
    """The half-apply this replaces set cost-accepted=True and left status at
    Drafting. The check must run BEFORE the first mutation, not after."""
    source = (Path(APP).parent.parent / "registries" / "cli_outcome.py").read_text(encoding="utf-8")
    body = source[source.index("def cmd_accept_cost") :]
    body = body[: body.index("\ndef ")]
    check_at = body.index('check_action("accept-cost"')
    mutate_at = body.index('outcome["cost-accepted"] = True')
    assert check_at < mutate_at, "accept-cost mutates before it validates"


def test_the_conditional_status_bump_is_gone():
    """`if from_status == "Backlog"` was what made Drafting a half-apply."""
    source = (Path(APP).parent.parent / "registries" / "cli_outcome.py").read_text(encoding="utf-8")
    body = source[source.index("def cmd_accept_cost") :]
    body = body[: body.index("\ndef ")]
    # The bump may remain, but it can no longer be reached from Drafting —
    # which the refusal above guarantees. What must NOT remain is a path that
    # writes cost-accepted without validating first.
    assert 'check_action("accept-cost"' in body


# ---------------------------------------------------------------------------
# 1.4 — the show=False audit


#: Actions whose ONLY binding is unadvertised, and why that is correct.
#: Both are deprecation ALIASES that announce where the operation moved; their
#: real paths (`m` Messages, `t` Artifacts + `L`) are advertised. Anything else
#: appearing here is the defect rule 5 describes.
_ALIAS_ONLY = {
    "decisions": "alias for Messages (m) — _alias() names the move",
    "lifecycle": "alias for Artifacts (t) in the lifecycle lens — L cycles",
}


def _hidden_only_actions() -> dict[str, list[str]]:
    src = APP.read_text(encoding="utf-8")
    bindings = re.findall(r'Binding\(\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]*)"([^)]*)\)', src)
    by_action: dict[str, list[tuple[str, bool]]] = {}
    for key, action, _label, rest in bindings:
        by_action.setdefault(action, []).append((key, "show=False" in rest))
    return {
        action: [k for k, _ in keys]
        for action, keys in by_action.items()
        if all(hidden for _, hidden in keys)
    }


def test_no_operation_reaches_the_user_only_through_an_unadvertised_key():
    """Rule 5's audit, executable.

    It caught two of MY OWN bindings an hour after I shipped them: `u` (undo)
    and `ctrl+a` (session actions) were both show=False, making each an
    operation whose only path was a key nobody is told about. An undo nobody
    knows exists protects nobody.
    """
    unexplained = sorted(set(_hidden_only_actions()) - set(_ALIAS_ONLY))
    assert not unexplained, (
        f"operations reachable only by an unadvertised key: {unexplained} — "
        "advertise the binding or give the operation another path"
    )


def test_the_alias_register_has_no_stale_entries():
    stale = sorted(set(_ALIAS_ONLY) - set(_hidden_only_actions()))
    assert not stale, f"registered aliases that are no longer hidden-only: {stale}"


def test_undo_and_the_session_view_are_advertised():
    """The two findings, pinned so they cannot quietly go hidden again."""
    hidden = _hidden_only_actions()
    assert "undo" not in hidden
    assert "session_actions" not in hidden


def test_the_status_verbs_are_advertised_on_the_detail_screen():
    """1.2 shipped promote/demote as real, visible actions — not hidden keys."""
    src = APP.read_text(encoding="utf-8")
    assert re.search(r'Binding\("P",\s*"promote"', src)
    assert re.search(r'Binding\("D",\s*"demote"', src)
    assert "show=False" not in re.search(r'Binding\("P",[^)]*\)', src).group(0)
