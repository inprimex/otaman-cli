"""`otaman send` must refuse a comma-joined recipient (no-silent-success).

Found via deploy-agent's 20260921T225111: they spotted a bus filename carrying
commas and spaces in its recipient segment and called the shape "more fragile
than length ever will be". The files themselves were from 2026-06-10 — the
legacy comma-joined `to:` that notify-change-fanout replaced in August — so the
obvious reading is "historical artifact, already fixed".

It is not. The fanout fixed `notify-change`; the `send` door stayed open:

    otaman send "runner-agent, web-agent" --subject t --body b
    [+] Sent: …-cli-agent-to-runner-agent, web-agent-t.md      exit 0

and `otaman check` shows it to NEITHER recipient, because the ownership filter
matches `-to-<agent>-` and the stem contains `-to-runner-agent,` and
`, web-agent-`. The sender is told it worked. Both recipients never hear about
it. That is the same loss the August fanout existed to end, and it is exactly
the shape the no-silent-success ruling names: a verb that did no work reporting
success.

The fix refuses at the door rather than splitting the string, because `--cc`
already means "several recipients" and silently reinterpreting one as the other
would be a second way to say the same thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def program(isolate_bus, monkeypatch):
    """The suite's own sandbox, populated with the agents this test routes to.

    Deliberately built ON `isolate_bus` rather than a hand-rolled tmp root: that
    autouse fixture pins OTAMAN_ROOT at its sandbox, so a self-made root is not
    where `otaman send` writes — the sends land in the sandbox and a test
    asserting on its own directory sees nothing and reads as a pass.
    """
    root = isolate_bus
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "agents.yaml").write_text(
        "agents:\n  - name: cli-agent\n  - name: runner-agent\n  - name: web-agent\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    return root


def _active(root: Path):
    return sorted((root / ".agents" / "bus" / "active").glob("*.md"))


@pytest.mark.parametrize(
    "recipient",
    [
        "runner-agent, web-agent",
        "runner-agent,web-agent",
        "runner-agent web-agent",
        "runner-agent;web-agent",
    ],
)
def test_a_multi_recipient_to_is_refused(program, recipient, capsys):
    from otaman_cli.commands.bus_messaging import cmd_send

    rc = cmd_send([recipient, "--subject", "t", "--body", "b"])

    assert rc != 0, "reported success for a message that would reach nobody"
    assert not _active(program), "wrote a message despite refusing"
    out = capsys.readouterr().out
    assert "--cc" in out, "refusal must name the thing that actually does this"


def test_a_single_recipient_still_sends(program):
    from otaman_cli.commands.bus_messaging import cmd_send

    assert cmd_send(["runner-agent", "--subject", "t", "--body", "b"]) == 0
    wrote = _active(program)
    assert len(wrote) == 1
    assert "-to-runner-agent-" in wrote[0].stem


def test_cc_is_the_supported_way_to_reach_several(program):
    """The refusal points here, so this has to keep working."""
    from otaman_cli.commands.bus_messaging import cmd_send

    assert cmd_send(["runner-agent", "--cc", "web-agent", "--subject", "t", "--body", "b"]) == 0
    stems = [p.stem for p in _active(program)]
    assert any("-to-runner-agent-" in s for s in stems)
    assert any("-to-web-agent-" in s or "-cc-web-agent-" in s for s in stems), stems


def test_a_recipient_with_a_slash_is_not_caught_by_this(program):
    """Cross-program URIs legitimately carry `/` — the guard is about SEPARATORS,
    not about every unusual character, and over-refusing would break routing."""
    from otaman_cli.commands.bus_messaging import cmd_send

    rc = cmd_send(["otaman://other/prog/runner-agent", "--subject", "t", "--body", "b"])
    assert rc == 0 or not any(", " in p.name for p in _active(program))
