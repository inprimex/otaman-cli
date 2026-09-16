"""ratify-spec-approve-split 2.1 — the gate names a RUNNABLE command.

core's violation text interpolates the change name into its remediation, but
only when the caller puts the name on the record. Without it the message shipped
the literal `<change>`:

    ... but never advanced to spec-approved — run `otaman spec approve <change>`

which is not a command anyone can run — the pmeets tenant's whole complaint was
having no CLI path forward, and being handed a placeholder is a smaller version
of the same failure.

core landed the ratifier recovery (#61); this is the remaining call-site half.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.spec import _run_gate

RATIFIED_BUT_NOT_ADVANCED = {
    "stage": "approved",
    "ratified": True,
    "approved_by": "ratified: starikov@inprimex.com — urgent",
    "ratified_at": "2026-09-13T21:05:54Z",
}


@pytest.fixture
def policy():
    from otaman_core.spec_lifecycle import resolve_spec_policy

    return resolve_spec_policy({}, {})


def _violations(decision) -> str:
    return " ".join(str(v) for v in decision.violations)


def test_change_name_is_interpolated_into_the_remediation(policy):
    decision = _run_gate(
        dict(RATIFIED_BUT_NOT_ADVANCED), policy, "dispatch", change_name="manifest-operation-model"
    )
    text = _violations(decision)
    assert "otaman spec approve manifest-operation-model" in text
    assert "<change>" not in text  # the placeholder is gone


def test_without_a_name_the_placeholder_survives(policy):
    """Pins WHY the stamp is needed: core can only interpolate what it is given."""
    text = _violations(_run_gate(dict(RATIFIED_BUT_NOT_ADVANCED), policy, "dispatch"))
    assert "<change>" in text


def test_a_name_already_on_the_record_is_not_overwritten(policy):
    record = {**RATIFIED_BUT_NOT_ADVANCED, "change": "from-the-file"}
    text = _violations(_run_gate(record, policy, "dispatch", change_name="from-the-caller"))
    assert "from-the-file" in text  # the file is the source of truth when it speaks


def test_a_blank_name_on_the_record_is_replaced(policy):
    record = {**RATIFIED_BUT_NOT_ADVANCED, "change": "   "}
    text = _violations(_run_gate(record, policy, "dispatch", change_name="real-name"))
    assert "real-name" in text


def test_the_callers_dict_is_never_mutated(policy):
    """The dict is the caller's parsed `.openspec.yaml`; stamping it in place
    would leak a synthesised field into anything that reads it afterwards."""
    record = dict(RATIFIED_BUT_NOT_ADVANCED)
    _run_gate(record, policy, "dispatch", change_name="some-change")
    assert "change" not in record


def test_the_ratifier_and_time_are_named_too(policy):
    """core's #61 half — kept under test from this side so a regression in
    either half is visible here."""
    text = _violations(
        _run_gate(dict(RATIFIED_BUT_NOT_ADVANCED), policy, "dispatch", change_name="c")
    )
    assert "starikov@inprimex.com" in text
    assert "2026-09-13T21:05:54Z" in text


@pytest.mark.parametrize("at", ["dispatch", "archive", "merge"])
def test_every_gate_accepts_the_name_without_error(policy, at):
    """The stamp lives in `_run_gate`, so it applies to whichever gate runs —
    a fourth caller cannot forget it."""
    decision = _run_gate(dict(RATIFIED_BUT_NOT_ADVANCED), policy, at, change_name="c")
    assert decision is not None


def test_all_call_sites_pass_the_name():
    """Guard: the three in-repo callers must each supply it, or the placeholder
    comes back on whichever path forgot."""
    import inspect

    from otaman_cli.commands import spec as mod

    src = inspect.getsource(mod)
    assert src.count("change_name=change_name") == 2  # dispatch_gate_check + waiver slug
    assert "change_name=name" in src  # `otaman spec gate <change>`
