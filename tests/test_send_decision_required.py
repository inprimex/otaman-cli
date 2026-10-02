"""`otaman send --type decision-required` was refused by the CLI — the duty was unexecutable.

The orchestration rules make emitting a `decision-required` a DUTY: "Never block
silently — emit `decision-required` first", with its own costed incidents (three
halts, ~11h of fleet delivery on 2026-09-25; a 62-hour frozen verification gate on
2026-09-26). The console has rendered them since dae 1.2, reading a `blocks`
frontmatter key for its row annotation.

Measured on the live bus before this change: **zero** `decision-required` messages
across `active/` and every archive month. Two reasons, both in this repo:

1. `decision-required` was absent from `MESSAGE_TYPES`, and `cmd_send` hard-rejects
   an unregistered type — so the prescribed command exited 2.
2. There were no flags for the three fields core REQUIRES on that type
   (`decision`, `blocks`, `unblock-condition`), so even a registered type could not
   produce a message that passes validation.

core's side was already in place on committed main (cc2f93b): the type is in
`VALID_TYPES`, deliberately NOT privileged (it asks for a decision, it does not
assert one), and `_DECISION_REQUIRED_FIELDS` is enforced in the validator.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import otaman_cli.commands.bus_messaging as BM


@pytest.fixture
def program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", "utf-8")
    (root / ".agents" / "agents.yaml").write_text(
        "agents:\n  - name: cli-agent\n    role: developer\n  - name: human\n    role: human\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.setattr(BM, "find_project_root", lambda: root)
    return root


def _sent(root: Path) -> list[Path]:
    return sorted((root / ".agents" / "bus" / "active").glob("*.md"))


def _fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert m, "no frontmatter"
    return yaml.safe_load(m.group(1))


FULL = [
    "--decision",
    "merge #74 by delegate, or restart the owner's session",
    "--blocks",
    "llm-router-backend 1.4 telemetry clause (cli)",
    "--unblock-condition",
    "PR #74 merged into otaman-bridge main",
]


# ---------------------------------------------------------------------------
# the regression: the type is sendable at all


def test_the_type_is_registered_and_the_send_succeeds(program):
    """Previously: exit 2, "Unknown message type" — the duty's own command."""
    rc = BM.cmd_send(
        ["human", "--type", "decision-required", "--subject", "s", "--body", "b", *FULL]
    )

    assert rc == 0
    assert len(_sent(program)) == 1
    assert _fm(_sent(program)[0])["type"] == "decision-required"


def test_the_three_fields_land_as_frontmatter(program):
    BM.cmd_send(["human", "--type", "decision-required", "--subject", "s", "--body", "b", *FULL])

    fm = _fm(_sent(program)[0])
    assert fm["decision"] == "merge #74 by delegate, or restart the owner's session"
    assert fm["blocks"] == "llm-router-backend 1.4 telemetry clause (cli)"
    assert fm["unblock-condition"] == "PR #74 merged into otaman-bridge main"


def test_the_emitted_message_passes_cores_validator(program):
    """The end-to-end contract, not just this repo's opinion of it.

    core enforces the three fields in `validate_message`; a send that writes them
    under the wrong names, or unquoted so a colon makes them a mapping, fails there
    and nowhere else.
    """
    from otaman_core.validate_message import validate_message

    BM.cmd_send(["human", "--type", "decision-required", "--subject", "s", "--body", "b", *FULL])

    errors, _warnings = validate_message(_sent(program)[0])
    assert errors == [], errors


def test_a_decision_containing_a_colon_survives(program):
    """`"merge or delegate: #74"` unquoted is a YAML mapping, and the field reads
    as missing — which core then rejects. Quoting is not cosmetic here."""
    from otaman_core.validate_message import validate_message

    rc = BM.cmd_send(
        [
            "human",
            "--type",
            "decision-required",
            "--subject",
            "s",
            "--body",
            "b",
            "--decision",
            "merge or delegate: PR #74",
            "--blocks",
            "lrb 1.4: telemetry",
            "--unblock-condition",
            "either: merged, or reassigned",
        ]
    )

    assert rc == 0
    fm = _fm(_sent(program)[0])
    assert fm["decision"] == "merge or delegate: PR #74"
    assert isinstance(fm["blocks"], str)
    assert validate_message(_sent(program)[0])[0] == []


# ---------------------------------------------------------------------------
# the refusals


@pytest.mark.parametrize("drop", ["--decision", "--blocks", "--unblock-condition"])
def test_a_missing_field_refuses_and_writes_nothing(program, capsys, drop):
    args = ["human", "--type", "decision-required", "--subject", "s", "--body", "b"]
    for i in range(0, len(FULL), 2):
        if FULL[i] != drop:
            args += FULL[i : i + 2]

    rc = BM.cmd_send(args)

    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 2
    assert _sent(program) == [], "a refused decision-required still wrote a message"
    assert drop in text, f"the refusal must name the missing flag; got: {text}"


def test_the_refusal_names_all_three_fields_and_why(program, capsys):
    """One refusal, the whole shape — an agent emitting under the never-block-silently
    duty should not learn the contract one rejected send at a time."""
    rc = BM.cmd_send(["human", "--type", "decision-required", "--subject", "s", "--body", "b"])

    text = "".join(capsys.readouterr())
    assert rc == 2
    for flag in ("--decision", "--blocks", "--unblock-condition"):
        assert flag in text
    assert "silent freeze" in text


def test_the_fields_are_refused_on_another_type(program, capsys):
    """Symmetry with the sequencing contract: these keys mean one type."""
    rc = BM.cmd_send(["human", "--type", "info", "--subject", "s", "--body", "b", "--blocks", "x"])

    text = "".join(capsys.readouterr())
    assert rc == 2
    assert _sent(program) == []
    assert "decision-required" in text


def test_an_ordinary_send_is_untouched(program):
    """The common case pays nothing: no new required flags, no new frontmatter."""
    rc = BM.cmd_send(["human", "--subject", "s", "--body", "b"])

    fm = _fm(_sent(program)[0])
    assert rc == 0
    assert fm["type"] == "info"
    for key in ("decision", "blocks", "unblock-condition"):
        assert key not in fm


# ---------------------------------------------------------------------------
# the console's render path finally has an input


def test_the_console_reads_the_blocks_annotation(program):
    """dae 1.2 reads `blocks` for the row annotation, and until now nothing could
    write it: zero decision-required messages existed on the live bus.

    `list_human_queue`, not `list_pending_proposals`: the consolidated lister is the
    one whose filter carries `_AWAITING_TYPES`. Checked before asserting — Home's
    `_queue_counts` reads the older lister and counts SCRs and outcome-proposals by
    name, so it is correct as it stands and needed no change.
    """
    from otaman_cli.console.bus import Program, list_human_queue

    BM.cmd_send(
        ["human", "--type", "decision-required", "--subject", "bridge #74", "--body", "b", *FULL]
    )

    rows = list_human_queue(Program(name="p", root=program))
    mine = [r for r in rows if r.msg_type == "decision-required"]
    assert len(mine) == 1, f"the console did not surface it: {[r.msg_type for r in rows]}"
    assert mine[0].blocks == "llm-router-backend 1.4 telemetry clause (cli)"
    assert mine[0].is_awaiting, "a decision-required must sort with the rows awaiting the human"
    assert mine[0].needs_answer, "it is answered, not approved/rejected"


def test_it_reaches_the_human_queue_even_when_addressed_to_an_agent(program):
    """dae 1.2's stated reason: requiring it to also be addressed correctly "would
    let a misaddressed emission freeze the agent silently — the exact failure the
    type exists to remove". So the queue must not filter it on `to:`.
    """
    from otaman_cli.console.bus import Program, list_human_queue

    BM.cmd_send(
        [
            "cli-agent",
            "--type",
            "decision-required",
            "--subject",
            "misaddressed",
            "--body",
            "b",
            *FULL,
        ]
    )

    rows = list_human_queue(Program(name="p", root=program))
    assert [r.msg_type for r in rows] == ["decision-required"], (
        "a decision-required addressed to an agent vanished from the human's queue"
    )
