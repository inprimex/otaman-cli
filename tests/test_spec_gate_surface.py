"""spec-proposal-constitutional-gate 1.3 — the gate's output where reviewers look.

Stage 1 is core's deterministic lint (`spec_gate.lint_proposal`, core #48): a 0-100
score, a tier, findings. Stage 2 is plugin's critic session, emitting a
`spec-proposal-critique-result` bus message. 1.3 renders both on the console proposal
row (D5) and in `otaman spec status`, comment-not-block per D1.

**D1 is the load-bearing constraint and the first group of tests.** The gate comments;
approval stays a human decision. So nothing here returns a verdict a caller can branch
on to refuse something, and `otaman spec status` exits the same code whatever the
score says. A surface that let a caller block on a score would reintroduce the veto
the ruling forbids.

Three things I verified in the siblings' committed main before building, because two
of them changed what 1.3 can honestly deliver:

* core's `lint_proposal`/`score_tier`/`LintResult` exist (#48, a491c46). ✓
* plugin's emitter exists and its body format is fixed
  (`spec_critique_dispatch.build_critique_result`). ✓
* **Nothing calls `lint_proposal` anywhere in any repo**, and its expected input is a
  `title`/`outcome`/`affected_repos`/`artifacts` mapping that no SCR body and no
  `.openspec.yaml` on this fleet produces. So the score has no producer yet: the
  plumbing here takes a mapping and is correct the moment one exists, and renders
  `not scored` until then — never a zero, which is a real and terrible score.
"""

from __future__ import annotations

import pytest

from otaman_cli import spec_gate_surface as sgs

BODY = """\
verdict: has-comments
critic: 'web-agent'
constitution_version: '1.2'
pass_index: 2
findings:
  - item: 1
    result: pass
    note: 'problem is observed, not assumed'
  - item: 4
    result: concern
    note: 'no measurement for the latency claim'
"""


# ---------------------------------------------------------------------------
# D1 — comment, never block.


def test_nothing_here_returns_a_blocking_decision():
    """Structural. A function named `blocks`/`allowed`/`refuse` would be the seam a
    caller branches on, and D1 forbids the veto, not merely its use."""
    import pathlib

    src = pathlib.Path(sgs.__file__).read_text(encoding="utf-8")
    for forbidden in ("def allowed", "def blocks", "def refuse", "def is_blocked"):
        assert forbidden not in src


def test_a_failing_verdict_renders_as_information(capsys):
    critique = sgs.parse_critique("verdict: fail\npass_index: 1\n")
    lines = sgs.render_lines(sgs.Score(value=12, tier="poor"), critique)
    rendered = "\n".join(lines)

    assert "stage 2: fail" in rendered
    assert "does not block" in rendered, (
        "a reviewer seeing `fail` must be told the gate is not holding the proposal, "
        "or they wait for an approval nothing is blocking"
    )


def test_the_not_blocking_line_is_absent_when_the_gate_produced_nothing():
    """It is a clarification about a verdict, not a slogan on every render."""
    lines = sgs.render_lines(sgs.Score(), sgs.Critique())

    assert not any("does not block" in line for line in lines)


# ---------------------------------------------------------------------------
# Stage 1 — the score, delegated to core.


def test_the_score_comes_from_cores_lint():
    """Core owns the rule; this is its first caller anywhere."""
    pytest.importorskip("otaman_core.spec_gate")
    proposal = {
        "title": "a real title",
        "outcome": "JTBD-1-something",
        "affected_repos": ["otaman-cli"],
        "artifacts": ["proposal.md"],
    }

    score = sgs.score_for(proposal, platform_repos=["otaman-cli"])

    assert score.scored
    assert 0 <= score.value <= 100
    assert score.tier
    assert score.label.startswith(f"{score.value}/100")


def test_a_deficient_proposal_scores_lower_than_a_clean_one():
    """D5's whole purpose: reviewers triage by number before reading.

    The clean proposal is built FROM core's `SECTION_KEYS` rather than from a list
    restated here. core #106 (JTBD-57 ruling A) made the seven decision-grade SCR
    sections required and tightened the outcome id to a resolvable shape, at which
    point the old literal fixture — title/outcome/affected_repos/artifacts, outcome
    `JTBD-1-something` — scored 0 just like the deficient one and this assertion
    could no longer tell them apart. Reading the contract from its single home is
    what keeps the comparison meaningful the next time it tightens.
    """
    pytest.importorskip("otaman_core.spec_gate")
    from otaman_core.spec_gate import SECTION_KEYS

    clean = {
        "title": "rate limiting on the bridge",
        "outcome": "JTBD-57",
        "affected_repos": ["otaman-cli"],
        **{key: f"a decision-grade {key} paragraph" for key in SECTION_KEYS},
    }
    deficient = {"title": "TBD", "outcome": "", "affected_repos": ["nope"], "artifacts": []}

    assert (
        sgs.score_for(deficient, platform_repos=["otaman-cli"]).value
        < sgs.score_for(clean, platform_repos=["otaman-cli"]).value
    )


def test_an_unscorable_proposal_is_not_scored_rather_than_zero(monkeypatch):
    """A zero is a real, terrible score. "We could not score it" is not a score."""
    import otaman_core.spec_gate as core

    def boom(*a, **kw):
        raise ValueError("unreadable shape")

    monkeypatch.setattr(core, "lint_proposal", boom)
    score = sgs.score_for({}, platform_repos=[])

    assert not score.scored
    assert score.value is None
    assert score.label == sgs.NOT_SCORED
    assert "could not lint" in score.error


def test_a_bundle_without_the_lint_is_not_scored(monkeypatch):
    monkeypatch.setattr(sgs, "_core", lambda: None)
    score = sgs.score_for({"title": "x"}, platform_repos=[])

    assert not score.scored
    assert "JTBD-57 1.1" in score.error


# ---------------------------------------------------------------------------
# Stage 2 — parsing plugin's emitted body.


def test_the_emitted_body_parses_into_a_verdict_and_findings():
    critique = sgs.parse_critique(BODY)

    assert critique.ran
    assert critique.verdict == "has-comments"
    assert critique.critic == "web-agent"
    assert critique.constitution_version == "1.2"
    assert critique.pass_index == 2
    assert [(f.item, f.result) for f in critique.findings] == [(1, "pass"), (4, "concern")]
    assert critique.findings[1].note == "no measurement for the latency claim"


def test_the_real_emitter_round_trips():
    """Against plugin's own builder, not a hand-written fixture — the format is
    theirs, and a fixture that drifts from it would pass while the surface broke."""
    dispatch = pytest.importorskip("otaman_plugin.spec_critique_dispatch")
    message = dispatch.build_critique_result(
        change="some-change",
        proposer="cli-agent",
        critic="web-agent",
        pass_index=1,
        constitution_version="1.0",
        findings=[dispatch.CritiqueFinding(item=1, result="pass", note="fine")],
    )

    critique = sgs.parse_critique(message.body)

    assert critique.ran
    assert critique.verdict in sgs.VERDICTS
    assert critique.critic == "web-agent"
    assert critique.findings[0].note == "fine"


def test_the_type_constant_matches_what_plugin_emits():
    dispatch = pytest.importorskip("otaman_plugin.spec_critique_dispatch")

    assert sgs.CRITIQUE_RESULT_TYPE == dispatch.CRITIQUE_RESULT_TYPE
    assert sgs.VERDICTS == tuple(dispatch.VERDICTS)


@pytest.mark.parametrize("body", ["", "nothing structured here", "verdict:\n", "findings:\n"])
def test_an_unparseable_body_did_not_run_rather_than_half_parsing(body):
    """A verdict is the thing a reviewer acts on; a guessed one is worse than none."""
    critique = sgs.parse_critique(body)

    assert not critique.ran
    assert critique.label == sgs.NOT_CRITIQUED


def test_a_verdict_outside_the_vocabulary_is_refused():
    """A body carrying `verdict: looks fine to me` must not reach a reviewer as
    though the critic ruled."""
    critique = sgs.parse_critique("verdict: looks fine to me\npass_index: 1\n")

    assert not critique.ran


def test_the_highest_pass_wins():
    """Two passes are allowed, and pass 2 exists precisely because pass 1's verdict
    was not the final word. Showing the first would show the stale one."""

    class _M:
        def __init__(self, body):
            self.msg_type = sgs.CRITIQUE_RESULT_TYPE
            self.body = body

    first = _M("verdict: fail\npass_index: 1\n")
    second = _M("verdict: pass\npass_index: 2\n")

    assert sgs.latest_critique([first, second]).verdict == "pass"
    assert sgs.latest_critique([second, first]).verdict == "pass"


def test_messages_of_other_types_are_ignored():
    class _M:
        msg_type = "info"
        body = "verdict: pass\npass_index: 9\n"

    assert not sgs.latest_critique([_M()]).ran


# ---------------------------------------------------------------------------
# The row suffix (D5).


def test_the_row_suffix_carries_both_halves():
    suffix = sgs.row_suffix(sgs.Score(value=72, tier="adequate"), sgs.parse_critique(BODY))

    assert "gate 72/100 adequate" in suffix
    assert "critique has-comments" in suffix


def test_the_row_suffix_is_empty_when_there_is_nothing_to_say():
    """A healthy row must not grow a column of "unknown"."""
    assert sgs.row_suffix(sgs.Score(), sgs.Critique()) == ""


def test_the_row_suffix_says_not_scored_when_the_score_failed():
    suffix = sgs.row_suffix(sgs.Score(error="no lint"), sgs.Critique())

    assert sgs.NOT_SCORED in suffix


def test_a_critique_alone_still_annotates_the_row():
    """The state on this fleet today: plugin can emit, nothing produces a score."""
    suffix = sgs.row_suffix(sgs.Score(), sgs.parse_critique(BODY))

    assert "critique has-comments" in suffix
    assert "gate" not in suffix


# ---------------------------------------------------------------------------
# The full record.


def test_the_record_names_every_finding():
    lines = sgs.render_lines(
        sgs.Score(value=72, tier="adequate", findings=(("E1", "error", "missing outcome"),)),
        sgs.parse_critique(BODY),
    )
    rendered = "\n".join(lines)

    assert "stage 1: 72/100 adequate" in rendered
    assert "error: missing outcome [E1]" in rendered
    assert "stage 2: has-comments by web-agent, pass 2, constitution 1.2" in rendered
    assert "item 4: concern — no measurement for the latency claim" in rendered


def test_not_critiqued_is_rendered_not_blank():
    """A blank where a verdict goes is indistinguishable from a pass — and no
    critique-result message exists on this fleet yet, so this IS the common case."""
    rendered = "\n".join(sgs.render_lines(sgs.Score(value=80, tier="good"), sgs.Critique()))

    assert f"stage 2: {sgs.NOT_CRITIQUED}" in rendered


# ---------------------------------------------------------------------------
# The console row, end to end through the queue builder.


#: plugin's `derive_verdict`: a `fail` item -> fail, a `comment` item ->
#: has-comments, else pass. Driving the helper by ITEM RESULT rather than by the
#: verdict I want keeps the expectation on plugin's rule instead of my guess at it —
#: my first version passed `result="concern"` expecting `has-comments` and got
#: `pass`, because "concern" is not in their vocabulary.
_RESULT_FOR_VERDICT = {"fail": "fail", "has-comments": "comment", "pass": "pass"}


def _critique_message(tmp_path, change, verdict, pass_index=1):
    from otaman_plugin.spec_critique_dispatch import (
        CritiqueFinding,
        build_critique_result,
        derive_verdict,
    )

    finding = CritiqueFinding(item=1, result=_RESULT_FOR_VERDICT[verdict], note="n")
    assert derive_verdict([finding]) == verdict, (
        "the fixture no longer produces the verdict it names — plugin's rule changed"
    )
    message = build_critique_result(
        change=change,
        proposer="cli-agent",
        critic="web-agent",
        pass_index=pass_index,
        constitution_version="1.0",
        findings=[finding],
    )
    stem = f"2026100{pass_index}T00000{pass_index}-web-agent-to-cli-agent-critique-{change}"
    (tmp_path / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: web-agent\nto: cli-agent\n"
        f"type: {sgs.CRITIQUE_RESULT_TYPE}\ntimestamp: 2026-10-01T00:00:0{pass_index}Z\n"
        f"status: pending\n---\n\n## Subject: {message.subject}\n\n{message.body}\n",
        encoding="utf-8",
    )


def test_the_queue_row_carries_the_gate_annotation(tmp_path):
    """D5 end to end: the row a reviewer triages from shows the verdict."""
    pytest.importorskip("otaman_plugin.spec_critique_dispatch")
    from otaman_cli.console import bus
    from otaman_cli.console.app import queue_row_label

    root = tmp_path / "prog"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: demo\nversion: '1.0'\nrepos: []\n", "utf-8")
    _critique_message(active, "my-change", "has-comments")
    scr = "20261001T120000-spec-agent-to-human-spec-change-request"
    (active / f"{scr}.md").write_text(
        f"---\nid: {scr}\nfrom: spec-agent\nto: human\ntype: spec-change-request\n"
        "timestamp: 2026-10-01T12:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: my-change: do the thing\n\nbody\n",
        encoding="utf-8",
    )

    bus.invalidate_caches() if hasattr(bus, "invalidate_caches") else None
    from otaman_cli.console.bus_index import clear_cache

    clear_cache()
    rows = bus.list_human_queue(bus.Program(name="demo", root=root))
    scr_row = next(r for r in rows if r.msg_type == "spec-change-request")

    assert "critique has-comments" in scr_row.gate_suffix
    assert "critique has-comments" in queue_row_label(scr_row)


def test_a_proposal_with_no_critique_gets_no_suffix(tmp_path):
    from otaman_cli.console import bus
    from otaman_cli.console.bus_index import clear_cache

    root = tmp_path / "prog"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: demo\nversion: '1.0'\nrepos: []\n", "utf-8")
    scr = "20261001T120000-spec-agent-to-human-spec-change-request"
    (active / f"{scr}.md").write_text(
        f"---\nid: {scr}\nfrom: spec-agent\nto: human\ntype: spec-change-request\n"
        "timestamp: 2026-10-01T12:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: untouched: do the thing\n\nbody\n",
        encoding="utf-8",
    )

    clear_cache()
    rows = bus.list_human_queue(bus.Program(name="demo", root=root))

    assert rows and rows[0].gate_suffix == ""


def test_the_longest_change_name_wins_the_join(tmp_path):
    """`console-lens` is a substring of `console-lens-navigation-and-filtering`; the
    shorter one must not claim the longer one's proposal."""
    pytest.importorskip("otaman_plugin.spec_critique_dispatch")
    from otaman_cli.console import bus
    from otaman_cli.console.bus_index import clear_cache

    root = tmp_path / "prog"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: demo\nversion: '1.0'\nrepos: []\n", "utf-8")
    _critique_message(active, "console-lens", "fail", pass_index=1)
    _critique_message(active, "console-lens-navigation", "pass", pass_index=2)
    scr = "20261001T120000-spec-agent-to-human-spec-change-request"
    (active / f"{scr}.md").write_text(
        f"---\nid: {scr}\nfrom: spec-agent\nto: human\ntype: spec-change-request\n"
        "timestamp: 2026-10-01T12:00:00Z\nstatus: pending\n---\n\n"
        "## Subject: Spec change request: console-lens-navigation: do it\n\nbody\n",
        encoding="utf-8",
    )

    clear_cache()
    rows = bus.list_human_queue(bus.Program(name="demo", root=root))
    scr_row = next(r for r in rows if r.msg_type == "spec-change-request")

    assert "critique pass" in scr_row.gate_suffix
    assert "fail" not in scr_row.gate_suffix
