"""hitl-default-approver 2.1 — `otaman hitl take` requires the approver role.

The HITL confirmation actor (resolved from OTAMAN_HUMAN) must hold the roster
`approver` role, via the SAME shared helper as the console approval path. A
resolved-but-non-approver is refused (named) before any decision is collected;
an unresolved OTAMAN_HUMAN keeps today's behavior. The TTY + ledger gates
(test_hitl_take_confirm_gate.py) are unchanged and still apply.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from otaman_cli.hitl.commands import cmd_take


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTAMAN_AGENT", "human")
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)

    def _write_roster(roles: str | None) -> None:
        body = "project: tst\nrepos: []\n"
        if roles is not None:
            body += (
                "human-roster:\n  - name: roman\n"
                f"    email: roman@example.com\n    roles: {roles}\n"
            )
        (tmp_path / "platform.yaml").write_text(body, encoding="utf-8")

    _write_roster("[developer]")  # default; individual tests rewrite it
    return tmp_path, _write_roster


def _stage_request(root: Path, stem: str) -> None:
    (root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        "---\n"
        f"id: {stem}\nfrom: bridge-agent\nto: human\npriority: normal\n"
        "type: request-human-review\ntimestamp: 2026-07-09T00:00:00Z\nstatus: pending\n"
        "session-id: sess-xyz\ndecision-type: approve-reject\n---\n\n"
        "## Subject: Approve widget rollout?\n\nbody\n",
        encoding="utf-8",
    )


_STEM = "20260709T000000-bridge-agent-to-human-request-human-review-x"


def test_take_refuses_resolved_non_approver(project, monkeypatch):
    root, write_roster = project
    write_roster("[developer]")  # roman resolves but lacks approver
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _stage_request(root, _STEM)
    # TTY is interactive; the refusal must fire BEFORE any decision prompt.
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", side_effect=AssertionError("must not prompt for a decision")),
    ):
        rc = cmd_take({"id": _STEM})
    assert rc != 0
    active = root / ".agents" / "bus" / "active"
    assert [f for f in active.glob("*human-decision*")] == []  # nothing recorded
    assert not (active / "acks" / f"{_STEM}.human.ack").exists()


def test_take_allows_approver(project, monkeypatch):
    root, write_roster = project
    write_roster("[approver]")
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _stage_request(root, _STEM)
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", side_effect=["approve", "", "", ""]),
    ):
        rc = cmd_take({"id": _STEM})
    assert rc == 0
    active = root / ".agents" / "bus" / "active"
    assert len([f for f in active.glob("*human-decision*")]) == 1  # recorded


def test_take_unresolved_human_unchanged(project, monkeypatch):
    root, write_roster = project
    write_roster("[approver]")  # roster exists…
    monkeypatch.setenv("OTAMAN_HUMAN", "ghost")  # …but the actor matches no entry
    _stage_request(root, _STEM)
    with (
        mock.patch("otaman_cli.safety.sys.stdin.isatty", return_value=True),
        mock.patch("builtins.input", side_effect=["approve", "", "", ""]),
    ):
        rc = cmd_take({"id": _STEM})
    assert rc == 0  # unresolved → not gated by approver (today's behavior)
