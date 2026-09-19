"""`otaman ack` argument resolution (cpo-agent bug report 20260917T095503).

Two bus files shared a prefix:

    ...-outcome-estimates-ready.md
    ...-outcome-estimates-ready-2.md

Matching is substring-based, so the shorter stem matched BOTH and was refused
as ambiguous. Acking the longer one did not help: acks are SIDECAR files, so
both messages stay in `active/` and the collision is permanent. The message
became unackable through the CLI and sat in `otaman check` forever — and
hand-editing the bus is how ack bookkeeping gets corrupted, so there was no
safe workaround.

Two rules, both requested by the reporter: an exact stem beats a prefix match,
and a candidate this agent already RESOLVED cannot be what they meant.
"""

from __future__ import annotations

import pathlib

import pytest

import otaman_cli.commands.bus_messaging as BM

STEM = "20260911T214358-cofounder-agent-to-cpo-agent-outcome-estimates-ready"


def _msg(active: pathlib.Path, stem: str, *, to: str = "cpo-agent") -> None:
    (active / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: cofounder-agent\nto: {to}\ntype: info\n"
        f"timestamp: 2026-09-11T21:43:58Z\nstatus: pending\n---\n\n## Subject: x\n",
        encoding="utf-8",
    )


@pytest.fixture
def bus(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    monkeypatch.setattr(BM, "find_project_root", lambda: root)
    monkeypatch.setattr(BM, "resolve_agent_identity", lambda r: "cpo-agent")
    monkeypatch.setattr(BM, "_resolve_bus_paths", lambda r: (active, active / "acks"))
    return active


def _acks(active) -> set[str]:
    return {p.name for p in (active / "acks").glob("*.ack")}


# ---------------------------------------------------------------------------
# exact beats prefix


def test_the_prefix_stem_acks_its_own_message(bus):
    """THE BUG: this was refused forever, leaving the message unackable."""
    _msg(bus, STEM)
    _msg(bus, STEM + "-2")
    assert BM.cmd_ack([STEM]) == 0
    assert f"{STEM}.cpo-agent.ack" in _acks(bus)


def test_the_longer_stem_still_acks_its_own(bus):
    _msg(bus, STEM)
    _msg(bus, STEM + "-2")
    assert BM.cmd_ack([STEM + "-2"]) == 0
    assert f"{STEM}-2.cpo-agent.ack" in _acks(bus)


def test_exact_never_acks_the_neighbour(bus):
    """The failure mode the exactness rule must not introduce: acking the
    prefix must not also close the message that merely contains it."""
    _msg(bus, STEM)
    _msg(bus, STEM + "-2")
    BM.cmd_ack([STEM])
    assert f"{STEM}-2.cpo-agent.ack" not in _acks(bus)


def test_both_can_be_acked_independently(bus):
    _msg(bus, STEM)
    _msg(bus, STEM + "-2")
    assert BM.cmd_ack([STEM + "-2"]) == 0
    assert BM.cmd_ack([STEM]) == 0
    assert len(_acks(bus)) == 2


# ---------------------------------------------------------------------------
# genuine ambiguity is still refused


def test_a_partial_pattern_matching_two_messages_is_still_refused(bus, capsys):
    """Exactness must not turn every partial into a guess — a pattern that is
    no one's full stem stays ambiguous."""
    _msg(bus, STEM + "-alpha")
    _msg(bus, STEM + "-beta")
    assert BM.cmd_ack([STEM]) == 1
    out = capsys.readouterr().out
    assert "Ambiguous stem" in out
    assert _acks(bus) == set()


def test_an_unmatched_pattern_still_errors(bus, capsys):
    _msg(bus, STEM)
    assert BM.cmd_ack(["nothing-like-this-at-all"]) == 1
    assert "No message matching" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# a resolved candidate breaks the tie


def test_a_resolved_candidate_drops_out_of_the_tie(bus):
    """Two genuinely different matches, one already closed → the open one is
    unambiguously the intended target."""
    _msg(bus, STEM + "-alpha")
    _msg(bus, STEM + "-beta")
    assert BM.cmd_ack([STEM + "-alpha"]) == 0
    assert BM.cmd_ack([STEM]) == 0  # only -beta is still open
    assert f"{STEM}-beta.cpo-agent.ack" in _acks(bus)


def test_a_read_candidate_does_not_drop_out(bus, capsys):
    """`read` means "seen, still open". A read->resolved follow-up by the same
    pattern must still be possible, so read must NOT break the tie."""
    _msg(bus, STEM + "-alpha")
    _msg(bus, STEM + "-beta")
    assert BM.cmd_ack([STEM + "-alpha", "--read"]) == 0
    assert BM.cmd_ack([STEM]) == 1  # still genuinely ambiguous
    assert "Ambiguous stem" in capsys.readouterr().out


def test_ack_is_idempotent_on_the_exact_stem(bus):
    _msg(bus, STEM)
    _msg(bus, STEM + "-2")
    assert BM.cmd_ack([STEM]) == 0
    assert BM.cmd_ack([STEM]) == 0  # exact match still wins after resolution
