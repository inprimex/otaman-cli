"""task-complete-reconciler 2.3 — a non-owner's `otaman complete` is honest.

The tick has no automated consumer: it applies only when the specs owner's
agent sweeps by hand. Until the reconciler ships, a non-owner filing a complete
must say the durable write is DEFERRED — and must not assert when it lands.

The measured cost of the old wording, from the pmeets tenant report that
prompted this change: ~2 weeks of completes unapplied after owner sessions were
cleared, the lens under-counting 5/11 against a real 11/11, and nothing warning
— because every caller had been told "spec-agent will tick tasks.md on next
session start" and believed it.

The spec: "A non-owner's `otaman complete` SHALL state that the durable tick is
deferred to the owner agent and SHALL NOT claim tasks updated."
"""

from __future__ import annotations

import pytest

from otaman_cli.commands import complete as C


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A tmp program the CLI resolves by its own rules.

    `chdir` matters and patching `find_project_root` alone is not enough:
    `cmd_complete` also resolves an ENFORCEMENT identity through core, which
    walks up from the cwd independently. Without the chdir that walk reached
    the real `otaman-meta` — and core's OTAMAN_TEST_MODE sentinel refused it
    rather than letting the suite touch a live bus. The guard was right; my
    first fixture was the leak.
    """
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    root.joinpath(".otaman").write_text("agent: cli-agent\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(C, "find_project_root", lambda: root)
    monkeypatch.setattr(C, "resolve_agent_identity", lambda r: "cli-agent")
    return root


def _run(argv=("demo-change", "--tasks", "1.1")):
    return C.cmd_complete(list(argv))


def _body(root):
    msg = next((root / ".agents" / "bus" / "active").glob("*task-complete*.md"))
    return msg.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# what it must NOT say


def test_a_non_owner_never_claims_tasks_updated(program, capsys):
    """The spec's explicit prohibition. `Updated: N task(s)` is a past-tense
    report of a write that did not happen for this caller."""
    _run()
    out = capsys.readouterr().out
    assert "Updated:" not in out
    assert "task(s) marked complete" not in out


def test_a_non_owner_promises_no_schedule(program, capsys):
    """The regression this task exists for. "on next session start" asserts a
    time nothing guarantees — it is the sentence that cost pmeets two weeks."""
    _run()
    out = capsys.readouterr().out
    assert "next session start" not in out
    assert "next session sweep" not in out


def test_the_bus_body_promises_no_schedule_either(program):
    """The terminal line and the filed record must not disagree — the body is
    what the owner reads later."""
    _run()
    body = _body(program)
    assert "next session start" not in body and "next session sweep" not in body
    assert "**Updated**" not in body


# ---------------------------------------------------------------------------
# what it must say


def test_a_non_owner_states_the_tick_is_deferred(program, capsys):
    _run()
    out = capsys.readouterr().out
    assert "DEFERRED" in out
    assert "tasks.md is unchanged" in out, "say what is NOT true yet, not just what is pending"


def test_the_deferral_names_the_owner_it_is_deferred_to(program, capsys):
    """ "Deferred" without a name leaves the caller unable to chase it."""
    _run()
    assert "spec-agent" in capsys.readouterr().out


def test_the_deferral_prefers_the_changes_declared_spec_owner(program, capsys, monkeypatch):
    """When `.openspec.yaml` names an owner, that is who it is deferred to —
    not a hardcoded `spec-agent`, since a program may own its specs elsewhere."""
    monkeypatch.setattr(C, "_read_spec_owner", lambda root, change: "docs-agent")
    _run()
    out = capsys.readouterr().out
    assert "DEFERRED to docs-agent" in out


def test_the_bus_body_records_the_deferral(program):
    _run()
    body = _body(program)
    assert "DEFERRED" in body and "not applied yet" in body


# ---------------------------------------------------------------------------
# the mechanism that does not exist yet


def test_it_does_not_point_at_a_command_that_is_not_built(program, capsys):
    """`otaman spec sweep` is task 1.2 and is blocked on core's 1.1 reader.
    Naming it here would replace one unfounded promise with another."""
    out = capsys.readouterr().out + " "
    _run()
    out += capsys.readouterr().out
    assert "spec sweep" not in out


def test_the_filing_is_named_as_the_record(program, capsys):
    """The caller needs to know the work IS recorded — the failure mode to
    avoid is someone re-filing or redoing it because deferral read as loss."""
    _run()
    assert "the record" in capsys.readouterr().out


def test_the_owner_path_is_untouched_by_this_change(program, capsys, monkeypatch):
    """Only the NON-persisting path changed. An owner still gets the real
    past-tense report, because for them the write actually happened."""
    monkeypatch.setattr(C, "_is_spec_agent", lambda a: True)
    monkeypatch.setattr(
        C,
        "run_script",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})(),
    )
    _run()
    out = capsys.readouterr().out
    assert "DEFERRED" not in out, "an owner's write is not deferred — it happened"
