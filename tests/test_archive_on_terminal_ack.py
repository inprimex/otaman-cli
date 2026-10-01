"""`cleanup` archived on ack EXISTENCE, not on ack STATE.

deploy-agent measured it on the live bus before the first successful cleanup in four
months ran (20261001T201348): of the 2,494 messages the fixed parse makes archivable,
**428 are acked `read`** — deliberately kept visible — and would have been archived out
of `otaman check`.

    resolved only       2004
    contains read        428      <- the defect
    approved              46
    rejected               5
    approved/resolved      6
    rejected/resolved      4

`otaman ack --read` exists to keep a message visible while the recipient finishes
something else, and this repo's own CLAUDE.md prescribes exactly that pattern ("ack as
read, add to queue, finish current task first"). The predicate was answering "has
anyone acked this?" when the question is "is anyone still working on it?".

This is not a new bus-semantics ruling. `--read`'s meaning is already shipped — the
archive predicate contradicted it. Whether `read` should additionally AGE OUT after some
longer window IS a ruling, and is spec-agent's; it would be an addition on top of this,
not a replacement for it.

Measured after the fix, same live bus: 2,105 archivable, 1,887 held (was 2,494 / 1,471),
and all three of the messages deploy named by stem now stay with their owners.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from otaman_cli.cleanup_bus import (
    LIVE_ACK_STATE,
    TERMINAL_ACK_STATES,
    ack_is_terminal,
    cleanup,
    is_fully_acked,
)


def _bus(root, messages, *, agents=("cli-agent", "core-agent")):
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "agents.yaml").write_text(
        "agents:\n" + "".join(f"  - name: {a}\n    role: developer\n" for a in agents),
        encoding="utf-8",
    )
    for name, ts, to in messages:
        (active / f"{name}.md").write_text(
            f"---\nid: {name}\nfrom: spec-agent\nto: {to}\ntype: info\n"
            f"timestamp: {ts}\nstatus: pending\n---\n\n## Subject: s\nbody\n",
            encoding="utf-8",
        )
    return active


def _ack(active, stem, agent, state):
    (active / "acks" / f"{stem}.{agent}.ack").write_text(f"{state}\n", encoding="utf-8")


def _old(days=90):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# The vocabulary.


@pytest.mark.parametrize("state", TERMINAL_ACK_STATES)
def test_a_terminal_state_is_terminal(tmp_path, state):
    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", state)

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is True


def test_read_is_not_terminal(tmp_path):
    """THE defect, at its smallest. 428 live messages are in this state."""
    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", LIVE_ACK_STATE)

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is False


def test_read_is_not_in_the_terminal_set():
    """Named explicitly rather than merely absent, so a reader can see the one word
    that keeps a message live."""
    assert LIVE_ACK_STATE not in TERMINAL_ACK_STATES


def test_a_state_word_followed_by_prose_still_counts(tmp_path):
    """One live ack reads `approved — already actioned: skill authored + merged in PR
    #76 ...`. A state word with a reason appended is still that state."""
    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", "approved — already actioned: merged in PR #76")

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is True


def test_the_match_is_case_insensitive(tmp_path):
    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", "RESOLVED")

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is True


@pytest.mark.parametrize("state", ["", "   ", "deferred", "seen", "ok"])
def test_an_unrecognised_or_empty_state_is_not_terminal(tmp_path, state):
    """Unreadable is not finished — the same rule this module applies to an
    unparseable timestamp, and the safe direction when an operator experiences the
    move as irreversible even though the file is only relocated."""
    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", state)

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is False


def test_an_absent_ack_is_not_terminal(tmp_path):
    active = _bus(tmp_path / "p", [])

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is False


def test_an_unreadable_ack_is_not_terminal(tmp_path, monkeypatch):
    from pathlib import Path

    active = _bus(tmp_path / "p", [])
    _ack(active, "m", "cli-agent", "resolved")
    real = Path.read_text

    def boom(self, *a, **kw):
        if self.suffix == ".ack":
            raise OSError("permission denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", boom)

    assert ack_is_terminal(active / "acks", "m", "cli-agent") is False


# ---------------------------------------------------------------------------
# Through the archive decision.


def test_a_read_acked_message_is_not_archived(tmp_path):
    """deploy's three named examples are exactly this shape, four months old and acked
    `read`. Staleness is a judgement nobody has made."""
    root = tmp_path / "p"
    active = _bus(root, [("20260521T095228-m-to-cli-agent-tasks", _old(), "cli-agent")])
    _ack(active, "20260521T095228-m-to-cli-agent-tasks", "cli-agent", "read")

    report = cleanup(root, dry_run=True)

    assert report["archived"] == []
    assert report["held_unacked"] == 1


def test_a_resolved_message_is_archived(tmp_path):
    root = tmp_path / "p"
    active = _bus(root, [("20260521T095228-m-to-cli-agent-tasks", _old(), "cli-agent")])
    _ack(active, "20260521T095228-m-to-cli-agent-tasks", "cli-agent", "resolved")

    report = cleanup(root, dry_run=True)

    assert report["archived"] == ["20260521T095228-m-to-cli-agent-tasks.md"]
    assert report["held_unacked"] == 0


def test_a_broadcast_needs_every_agent_terminal_not_merely_acked(tmp_path):
    """One `read` among the recipients keeps a broadcast live. Before, any ack file
    counted, so one agent's `--read` was indistinguishable from their `resolved`."""
    root = tmp_path / "p"
    stem = "20260521T095228-m-to-all-broadcast"
    active = _bus(root, [(stem, _old(), "all")])
    _ack(active, stem, "cli-agent", "resolved")
    _ack(active, stem, "core-agent", "read")

    assert cleanup(root, dry_run=True)["archived"] == []

    _ack(active, stem, "core-agent", "resolved")
    assert cleanup(root, dry_run=True)["archived"] == [f"{stem}.md"]


def test_a_broadcast_with_a_missing_ack_is_still_held(tmp_path):
    """Unchanged behaviour: this is the 17-of-17 requirement deploy raised separately,
    and it is a policy question rather than part of this fix."""
    root = tmp_path / "p"
    stem = "20260521T095228-m-to-all-broadcast"
    active = _bus(root, [(stem, _old(), "all")])
    _ack(active, stem, "cli-agent", "resolved")

    assert cleanup(root, dry_run=True)["archived"] == []


def test_the_held_count_distinguishes_read_from_never_acked(tmp_path):
    """Both are held, and the operator needs the number to mean something — it is the
    answer to "why is this still here" after the parse fix."""
    root = tmp_path / "p"
    active = _bus(
        root,
        [
            ("20260521T000001-m-to-cli-agent-a", _old(), "cli-agent"),
            ("20260521T000002-m-to-cli-agent-b", _old(), "cli-agent"),
            ("20260521T000003-m-to-cli-agent-c", _old(), "cli-agent"),
        ],
    )
    _ack(active, "20260521T000001-m-to-cli-agent-a", "cli-agent", "read")
    _ack(active, "20260521T000002-m-to-cli-agent-b", "cli-agent", "resolved")
    # the third has no ack at all

    report = cleanup(root, dry_run=True)

    assert report["archived"] == ["20260521T000002-m-to-cli-agent-b.md"]
    assert report["held_unacked"] == 2


def test_a_young_resolved_message_is_not_archived(tmp_path):
    """The age check still runs first — a terminal ack does not make a message old."""
    root = tmp_path / "p"
    active = _bus(root, [("20260521T000001-m-to-cli-agent-a", _old(1), "cli-agent")])
    _ack(active, "20260521T000001-m-to-cli-agent-a", "cli-agent", "resolved")

    assert cleanup(root, dry_run=True)["archived"] == []


def test_the_predicate_reads_the_file_rather_than_testing_existence():
    """Structural, because the bug was a one-line `exists()` and would come back as
    one. The function must actually read what the ack says."""
    import ast
    import inspect
    import textwrap

    func = ast.parse(textwrap.dedent(inspect.getsource(ack_is_terminal))).body[0]
    code = "\n".join(ast.unparse(n) for n in func.body if not isinstance(n, ast.Expr))

    assert "read_text" in code, "the ack state is not being read"
    assert ".exists()" not in code, "existence is not the question — state is"


def test_is_fully_acked_delegates_rather_than_re_deriving():
    """Two places deciding what 'finished' means is how the single-agent and broadcast
    paths would come to disagree."""
    import ast
    import inspect
    import textwrap

    func = ast.parse(textwrap.dedent(inspect.getsource(is_fully_acked))).body[0]
    code = "\n".join(ast.unparse(n) for n in func.body if not isinstance(n, ast.Expr))

    assert code.count("ack_is_terminal") == 2, "both paths must ask the same predicate"
    assert ".exists()" not in code
