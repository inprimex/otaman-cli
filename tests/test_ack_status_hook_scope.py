"""Acking a task-assignment must not claim work this agent was not given.

Both defects below were found live on 2026-09-25, on this agent's own record:
acking a CC copy of a task-assignment addressed to plugin-agent set cli-agent
to `state: working, task: null`, overriding an explicit `idle` set minutes
earlier. The fleet then rendered `cli-agent working (—)`.

That record is the precise shape `otaman set-status` refuses outright — "a
working record with no task cannot be told from a dead session" — so the
invariant was enforced in one path and violated in another. The staleness and
HALTED surfaces read this record, so a false `working` is not cosmetic: it is
an input to whether a session gets reported as hung.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.bus_messaging import (
    _assignment_is_addressed_to,
    _file_is_for_agent,
    _status_hook_after_ack,
)
from otaman_cli.status import State, get_backend, is_agent_presence_enabled


def _msg(path, *, to, msg_type="task-assignment", cc_copy=False, body="", extra=""):
    fm = f"---\nid: x\nfrom: spec-agent\nto: {to}\ntype: {msg_type}\n"
    if cc_copy:
        fm += "cc: [cli-agent]\nx-cc: true\n"
    fm += extra + "---\n"
    path.write_text(fm + body, encoding="utf-8")
    return path


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "meta"
    (root / ".agents" / "status").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\nagent_presence:\n  enabled: true\n",
        encoding="utf-8",
    )
    if not is_agent_presence_enabled(root):
        pytest.skip("agent presence disabled in this config shape")
    return root


# ---------------------------------------------------------------------------
# the two questions are different


def test_a_cc_copy_is_my_file_but_not_my_assignment(tmp_path):
    """The distinction the bug collapsed. Both answers are correct; using the
    first to answer the second is what marked this agent working."""
    stem = "20260925T171639-spec-agent-to-plugin-agent-cc-cli-agent-conformance-task"
    fm = {"to": "plugin-agent", "cc": ["cli-agent"], "x-cc": True, "type": "task-assignment"}
    assert _file_is_for_agent(stem, fm, "cli-agent") is True
    assert _assignment_is_addressed_to(fm, "cli-agent") is False


def test_my_own_assignment_is_addressed_to_me():
    assert _assignment_is_addressed_to({"to": "cli-agent"}, "cli-agent")


def test_a_broadcast_assignment_counts():
    assert _assignment_is_addressed_to({"to": "all"}, "cli-agent")


def test_a_multi_recipient_to_field_counts():
    assert _assignment_is_addressed_to({"to": "plugin-agent, cli-agent"}, "cli-agent")


def test_someone_elses_assignment_does_not():
    assert not _assignment_is_addressed_to({"to": "plugin-agent"}, "cli-agent")


# ---------------------------------------------------------------------------
# the hook


def test_acking_a_cc_of_another_agents_assignment_leaves_my_status_alone(program):
    """The live defect, end to end."""
    backend = get_backend(program)
    msg = _msg(
        program / "m.md",
        to="plugin-agent",
        cc_copy=True,
        body="\n## Subject: their task\n\n- [ ] 3.1 @otaman-plugin do a thing\n",
    )
    _status_hook_after_ack(program, "cli-agent", [msg])
    assert backend.read("cli-agent") is None, "being CC'd is not being assigned"


def test_acking_my_own_assignment_sets_working_with_the_task(program):
    msg = _msg(
        program / "m.md",
        to="cli-agent",
        body=(
            '\n## Subject: Tasks assigned from "demo-change"\n\n'
            'The following tasks from the feature "demo-change" are assigned to you:\n\n'
            "- [ ] 2.2 @otaman-cli do the thing (otaman-cli)\n"
        ),
    )
    _status_hook_after_ack(program, "cli-agent", [msg])
    rec = get_backend(program).read("cli-agent")
    assert rec is not None and rec.state == State.WORKING
    assert rec.task == "2.2 do the thing", f"routing metadata leaked: {rec.task!r}"
    assert rec.change == "demo-change"


def test_an_assignment_with_no_parsable_task_does_not_write_bare_working(program):
    """The invariant `set-status` enforces, applied here. A `working` with no
    task is indistinguishable from a dead session — which is what the
    staleness surfaces are trying to detect."""
    msg = _msg(program / "m.md", to="cli-agent", body="\n## Subject: no task lines here\n\nprose\n")
    _status_hook_after_ack(program, "cli-agent", [msg])
    assert get_backend(program).read("cli-agent") is None


def test_a_bare_working_never_overwrites_an_explicit_idle(program):
    """What actually happened: an explicit `idle` was replaced by
    `working/None` on the next ack of an unparsable CC assignment."""
    from otaman_cli.status import AgentStatus

    backend = get_backend(program)
    backend.write(
        AgentStatus(
            agent="cli-agent",
            state=State.IDLE,
            task=None,
            change=None,
            since="2026-09-25T16:54:36Z",
            updated_at="2026-09-25T16:54:36Z",
        )
    )
    msg = _msg(program / "m.md", to="plugin-agent", cc_copy=True, body="\n## Subject: theirs\n")
    _status_hook_after_ack(program, "cli-agent", [msg])
    assert backend.read("cli-agent").state == State.IDLE


def test_a_non_assignment_message_never_touches_status(program):
    msg = _msg(program / "m.md", to="cli-agent", msg_type="info", body="\n## Subject: fyi\n")
    _status_hook_after_ack(program, "cli-agent", [msg])
    assert get_backend(program).read("cli-agent") is None


# ---------------------------------------------------------------------------
# the parser knew the hand-written templates but not the dispatcher's own

from otaman_cli.commands.bus_messaging import (  # noqa: E402
    _parse_task_and_change_from_body as parse,
)


def test_the_dispatchers_own_line_format_parses():
    """The root cause of fleet-wide `working (—)`: every real assignment is
    emitted in this shape, and the parser understood only the hand-written
    templates — so task came back None and the hook wrote a bare `working`."""
    body = (
        '## Subject: Tasks assigned from "session-runtime-freshness"\n\n'
        'The following tasks from the feature "session-runtime-freshness" '
        "are assigned to you:\n\n"
        "- [ ] 1.3 @otaman-cli console session view renders the same verdicts (otaman-cli)\n"
    )
    task, change = parse(body)
    assert task == "1.3 console session view renders the same verdicts"
    assert change == "session-runtime-freshness"


def test_routing_metadata_is_not_part_of_the_task():
    """`@otaman-cli` and the trailing `(otaman-cli)` say where the work lives,
    not what it is — and a human scans this line across the whole fleet."""
    task, _ = parse("- [ ] 2.3 @otaman-cli the no-render-path-IO guard (otaman-cli)\n")
    assert task == "2.3 the no-render-path-IO guard"


def test_an_already_ticked_line_still_parses():
    task, _ = parse("- [x] 1.1 @otaman-cli the Store (otaman-cli)\n")
    assert task == "1.1 the Store"


def test_the_handwritten_templates_still_parse():
    """This fix ADDS shapes; the ones that already worked must keep working."""
    task, change = parse("**Task:** 4.2 wire the thing\n**Change:** some-change\n")
    assert task == "4.2 wire the thing" and change == "some-change"
    task, _ = parse("### 3.1 — do the other thing\n")
    assert task == "3.1 do the other thing"
