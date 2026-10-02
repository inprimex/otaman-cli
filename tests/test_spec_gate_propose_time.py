"""JTBD-57 1.3 — stage 1's SCORE, at propose time and on every surface.

The gate's rule has shipped since core #48 and nothing called it: scoring a real
SCR needs a mapping from the seven decision-grade sections to lint fields, and core
#106 is that extractor. This is the caller — `otaman propose` returns the score to
the proposer immediately (D1: comment, never block), the console row carries it, and
`otaman spec status` renders it with its findings.

Four corpus mismatches are pinned here, because each one decides what this surface
sends into core's rule:

- `outcome` is required only when an `.openspec.yaml` exists (75 vs 100 on the same
  honest SCR);
- the lint's body scan reads `proposal["body"]`, which the extractor does not set;
- `.openspec.yaml` keeps the outcome ID in `outcome-id` and the STATEMENT in
  `outcome`;
- an SCR with no sections at all predates the rubric and is unscorable, not a zero.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from otaman_cli.spec_gate_surface import score_for_scr

SECTIONS = {
    "problem": "the bridge has no rate limit",
    "evidence": "measured 4k req/s from one agent on 2026-10-01",
    "impact": "one agent can starve the fleet",
    "direction": "a token bucket per connection",
    "scope": "otaman-bridge only",
    "routing": "otaman-bridge",
    "workaround": "n/a because no limit exists to work around",
}


def _scr(**overrides) -> str:
    """A rendered, decision-grade SCR body."""
    from otaman_cli.scr_gate import scr_template

    sections = {**SECTIONS, **overrides}
    return scr_template().render("add rate limiting", sections=sections, evidence_level="measured")


def _root(repos: str = "  - name: otaman-bridge\n    path: ../b\n") -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "blocked").mkdir(parents=True)
    (root / "platform.yaml").write_text(
        "project: d\nversion: '1.0'\nrepos:\n" + repos, encoding="utf-8"
    )
    return root


def _wire_propose(monkeypatch, root):
    import otaman_cli.commands.propose_team as PT

    active = root / ".agents" / "bus" / "active"
    monkeypatch.setattr(PT, "find_project_root", lambda: root)
    monkeypatch.setattr(PT, "resolve_agent_identity", lambda _r: "cli-agent")
    monkeypatch.setattr(PT, "_resolve_bus_paths", lambda _r: (active, active / "acks"))
    return PT, active


def _propose_args() -> list[str]:
    args = ["add rate limiting"]
    for key, value in SECTIONS.items():
        args += [f"--{key}", value]
    return args + ["--evidence-level", "measured"]


# --- what the surface sends into core's rule ---------------------------------------


def test_a_decision_grade_scr_scores_full_marks():
    score = score_for_scr(_scr(), subject="add rate limiting", platform_repos=["otaman-bridge"])
    assert score.scored
    assert score.value == 100
    assert score.findings == ()


def test_the_outcome_is_required_only_once_an_openspec_exists():
    """The proposer cannot know the outcome id — spec-agent assigns it at authoring.

    Both halves measured on the SAME body: requiring it costs an honest proposal 25
    points for a field that does not exist yet.
    """
    body = _scr()
    at_propose = score_for_scr(body, platform_repos=["otaman-bridge"])
    authored = score_for_scr(body, platform_repos=["otaman-bridge"], openspec={})
    assert at_propose.value == 100
    assert [c for c, _, _ in at_propose.findings] == []
    assert authored.value == 75
    assert [c for c, _, _ in authored.findings] == ["missing-field"]
    assert "outcome" in authored.findings[0][2]


def test_an_authored_change_with_an_outcome_scores_full_marks():
    score = score_for_scr(_scr(), platform_repos=["otaman-bridge"], openspec={"outcome": "JTBD-57"})
    assert score.value == 100


def test_the_body_reaches_the_secret_scan():
    """1.1 promised gitleaks on the body; the extractor does not set `body`.

    Without the caller supplying it, `scan_secrets` runs on an empty string and the
    check has never fired on any SCR.
    """
    leaky = _scr(evidence="the token ghp_abcdefghijklmnopqrstuvwxyz0123456789 was in the log")
    score = score_for_scr(leaky, platform_repos=["otaman-bridge"])
    codes = [c for c, _, _ in score.findings]
    assert "secret-in-body" in codes
    # Values-free: the finding names the PATTERN, never the credential.
    assert all("ghp_abcdefghij" not in message for _, _, message in score.findings)


def test_a_proposal_that_predates_the_template_is_unscorable_not_a_zero():
    """19 pending approval items on this fleet resolve to SCRs filed before the
    seven-section template existed. A red 0/100 on each is how a reviewer learns to
    ignore the number."""
    old = (
        "---\nfrom: spec-agent\n---\n\n"
        "## Subject: Spec change request: canonize a marker\n\nprose.\n"
    )
    score = score_for_scr(old, platform_repos=["otaman-bridge"])
    assert not score.scored
    assert score.value is None
    assert "predates" in score.error


def test_a_core_without_the_extractor_is_not_scored_never_a_zero(monkeypatch):
    import otaman_cli.spec_gate_surface as sgs

    class _NoExtractor:
        def lint_proposal(self, *a, **k):  # pragma: no cover - never reached
            raise AssertionError("must not be called without the extractor")

        def score_tier(self, value):  # pragma: no cover
            return ""

    monkeypatch.setattr(sgs, "_core", lambda: _NoExtractor())
    score = sgs.score_for_scr(_scr(), platform_repos=[])
    assert not score.scored
    assert "extractor" in score.error


# --- the repo vocabulary the lint checks against ------------------------------------


def test_declared_repo_names_reads_the_declaration_order():
    from otaman_cli.platform_config import declared_repo_names

    root = _root("  - name: otaman-bridge\n    path: ../b\n  - name: otaman-cli\n    path: ../c\n")
    assert declared_repo_names(root) == ["otaman-bridge", "otaman-cli"]


def test_declared_repo_names_tolerates_bare_strings_and_a_missing_file(tmp_path):
    from otaman_cli.platform_config import declared_repo_names

    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("repos:\n  - otaman-cli\n  - ''\n", encoding="utf-8")
    assert declared_repo_names(root) == ["otaman-cli"]
    assert declared_repo_names(tmp_path / "nowhere") == []


def test_a_marker_is_handed_over_as_written_and_still_scores():
    """`outcome-id` holds the id; `outcome` holds the STATEMENT — in 25 and 31 of the
    71 change markers respectively, and not one carries an id in `outcome`.

    A caller-side normalization briefly lived in `_cmd_status` for exactly that, and
    core #110 made the extractor read `outcome-id` first — so the marker now goes over
    as written, and this asserts the OUTCOME from the surface's side: prose in
    `outcome` must not cost a proposal points when the id is right there. It fails on
    a core that stops reading `outcome-id`, which is the regression worth catching.
    """
    marker = {
        "outcome-id": "JTBD-112-action-required-channel-semantics",
        "outcome": "the human sees what needs them, grouped and instant",
    }
    score = score_for_scr(_scr(), platform_repos=["otaman-bridge"], openspec=marker)
    if _core_reads_outcome_id():
        assert score.value == 100
        assert [c for c, _, _ in score.findings] == []
    else:
        # A bundle predating #110 reads the prose and says so — a visible,
        # uniform -10, not a wrong claim about the proposal. Asserted rather than
        # skipped so the difference is recorded instead of hidden.
        assert [c for c, _, _ in score.findings] == ["malformed-outcome"]


def _core_reads_outcome_id() -> bool:
    """Whether core's extractor prefers `outcome-id` over the prose `outcome` (#110)."""
    from otaman_core.spec_gate import proposal_from_scr

    mapping = proposal_from_scr("## Subject: t\n", {"outcome-id": "JTBD-1", "outcome": "prose"})
    return mapping.get("outcome") == "JTBD-1"


# --- propose time ------------------------------------------------------------------


def test_propose_reports_the_score_to_the_proposer(monkeypatch, capsys):
    root = _root()
    PT, active = _wire_propose(monkeypatch, root)
    rc = PT.cmd_propose(_propose_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Stage-1 gate: 100/100 excellent" in out
    assert list(active.glob("*spec-change-request*.md"))


def test_propose_names_a_finding_and_files_the_proposal_anyway(monkeypatch, capsys):
    """D1 — comment, never block: a flagged proposal still reaches the human."""
    root = _root()
    PT, active = _wire_propose(monkeypatch, root)
    args = _propose_args()
    args[args.index("--routing") + 1] = "otaman-nope"
    rc = PT.cmd_propose(args)
    out = capsys.readouterr().out
    assert rc == 0, "the gate must not block the proposal"
    assert list(active.glob("*spec-change-request*.md")), "the SCR must be filed anyway"
    assert "unknown-repo" in out
    assert "filed either way" in out


def test_a_gate_that_cannot_run_never_costs_the_proposer_their_proposal(monkeypatch, capsys):
    root = _root()
    PT, active = _wire_propose(monkeypatch, root)

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr("otaman_cli.spec_gate_surface.score_for_scr", boom)
    rc = PT.cmd_propose(_propose_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert list(active.glob("*spec-change-request*.md"))
    assert "did not run" in out


# --- the console row ---------------------------------------------------------------


def _bus_program(root):
    from otaman_cli.console.bus import Program

    return Program(name="demo", root=root)


def _write(active, stem, *, msg_type, subject, body="", to="human"):
    active.joinpath(f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: spec-agent\nto: {to}\ntype: {msg_type}\n"
        f"timestamp: 2026-10-01T10:00:00Z\nstatus: pending\n---\n\n"
        f"## Subject: {subject}\n\n{body}",
        encoding="utf-8",
    )
    return stem


def test_the_console_row_carries_the_score(monkeypatch):
    from otaman_cli.console.bus import list_human_queue
    from otaman_cli.console.bus_index import clear_cache

    root = _root()
    active = root / ".agents" / "bus" / "active"
    scr = _scr()
    body = scr.split("\n", 1)[1] if scr.startswith("## Subject:") else scr
    _write(
        active,
        "20261001T100000-spec-agent-to-human-spec-change-request",
        msg_type="spec-change-request",
        subject="add rate limiting",
        body=body,
    )
    clear_cache()
    rows = list_human_queue(_bus_program(root))
    assert len(rows) == 1
    assert "gate 100/100 excellent" in rows[0].gate_suffix


def test_an_approval_pending_row_scores_via_the_scr_it_names():
    """What the human's queue actually holds: 19 of these against 0 pending SCRs."""
    from otaman_cli.console.bus import list_human_queue
    from otaman_cli.console.bus_index import clear_cache

    root = _root()
    active = root / ".agents" / "bus" / "active"
    scr_stem = "20261001T100000-spec-agent-to-human-spec-change-request"
    scr = _scr()
    _write(
        active,
        scr_stem,
        msg_type="spec-change-request",
        subject="add rate limiting",
        body=scr.split("\n", 1)[1],
        to="spec-agent",
    )
    _write(
        active,
        "20261001T100001-spec-agent-to-human-spec-approval-pending",
        msg_type="spec-approval-pending",
        subject="Approval pending: add rate limiting",
        body=f"Awaiting human approval of SCR `{scr_stem}` (from cli-agent).\n",
    )
    clear_cache()
    rows = {r.msg_type: r for r in list_human_queue(_bus_program(root))}
    assert "gate 100/100 excellent" in rows["spec-approval-pending"].gate_suffix


def test_a_pre_template_proposal_does_not_grow_a_column_of_unknown():
    """A dating fact is not a verdict: `gate not scored` on 9 legitimate rows is the
    noise the row annotation exists to avoid, and nobody can make a 2026-09-11
    proposal decision-grade retroactively."""
    from otaman_cli.console.bus import list_human_queue
    from otaman_cli.console.bus_index import clear_cache
    from otaman_cli.spec_gate_surface import PREDATES_TEMPLATE, Score, row_suffix

    root = _root()
    active = root / ".agents" / "bus" / "active"
    _write(
        active,
        "20261001T100003-spec-agent-to-human-spec-change-request",
        msg_type="spec-change-request",
        subject="Spec change request: canonize a marker",
        body="prose, no sections.\n",
    )
    clear_cache()
    rows = list_human_queue(_bus_program(root))
    assert rows[0].gate_suffix == ""
    # Any OTHER reason still annotates: "we could not score it" is worth saying.
    assert "not scored" in row_suffix(Score(error="core is too old"), _no_critique())
    assert row_suffix(Score(error=PREDATES_TEMPLATE), _no_critique()) == ""


def _no_critique():
    from otaman_cli.spec_gate_surface import Critique

    return Critique()


def test_an_unscored_type_is_never_READ(monkeypatch):
    """The type gate is a COST guard, and the cost is the body read.

    The row loop deliberately does a bounded subject read — full reads of every row
    measured ~1.6s on this bus's 5,473 messages, a visible freeze on the screen Roman
    opens most. Scoring needs a whole body, so it is confined to the two scored
    types. Asserting the empty annotation would NOT test this: a non-SCR type has no
    SCR to resolve either, so the annotation is empty whether or not the file was
    opened. The read count is the thing that can regress.
    """
    from otaman_cli.console import bus

    root = _root()
    active = root / ".agents" / "bus" / "active"
    info = active / "20261001T100002-spec-agent-to-human-info.md"
    _write(active, info.stem, msg_type="info", subject="fyi")
    scr = active / "20261001T100000-spec-agent-to-human-spec-change-request.md"
    _write(
        active,
        scr.stem,
        msg_type="spec-change-request",
        subject="add rate limiting",
        body=_scr().split("\n", 1)[1],
    )

    reads: list[str] = []
    real = pathlib.Path.read_text

    def counting(self, *a, **k):
        reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)

    assert bus._score_for(info, "info", []).label == "not scored"
    assert info.name not in reads, "an unscored type must not cost a body read"

    reads.clear()
    assert bus._score_for(scr, "spec-change-request", ["otaman-bridge"]).value == 100
    assert scr.name in reads


# --- otaman spec status -------------------------------------------------------------


@pytest.fixture
def status_root(monkeypatch):
    """A program whose specs dir holds one authored change with an SCR on the bus."""
    import otaman_cli.commands.spec as spec_cmd

    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True)
    changes = root / "specs" / "openspec" / "changes" / "rate-limiting"
    changes.mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: d\nspecs:\n  path: specs\nrepos:\n  - name: otaman-bridge\n    path: ../b\n",
        encoding="utf-8",
    )
    changes.joinpath("tasks.md").write_text("# tasks\n\n- [ ] 1.1 @otaman-cli do it\n", "utf-8")
    # Both keys carry the id, so the score is the same on either side of core #110 —
    # which key is READ is core's business and has its own test above.
    changes.joinpath(".openspec.yaml").write_text(
        "stage: spec-approved\noutcome-id: JTBD-57\noutcome: JTBD-57\n", "utf-8"
    )
    _write(
        active,
        "20261001T100000-spec-agent-to-human-spec-change-request",
        msg_type="spec-change-request",
        subject="rate-limiting: add a token bucket",
        body=_scr().split("\n", 1)[1],
    )
    monkeypatch.setattr(spec_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        "otaman_cli.bus_paths._resolve_bus_paths", lambda r: (active, active / "acks")
    )
    return root, spec_cmd


def test_spec_status_renders_the_score_and_says_it_does_not_block(status_root, capsys):
    root, spec_cmd = status_root
    spec_cmd.cmd_spec(["status"])
    out = capsys.readouterr().out
    assert "gate: 100/100 excellent" in out
    assert out.count("it does not block") == 1, "the disclaimer is a footer, not a refrain"


def test_spec_status_json_carries_the_number(status_root, capsys):
    import json

    root, spec_cmd = status_root
    spec_cmd.cmd_spec(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    row = next(c for c in payload["changes"] if c["change"] == "rate-limiting")
    assert row["gate_score"] == 100
    assert row["gate_tier"] == "excellent"


def test_a_change_with_no_scr_on_the_bus_carries_no_score(status_root, capsys):
    """`null`, never 0: "nobody filed an SCR for this" is not "this scored zero"."""
    import json

    root, spec_cmd = status_root
    for path in (root / ".agents" / "bus" / "active").glob("*spec-change-request*.md"):
        path.unlink()
    spec_cmd.cmd_spec(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    row = next(c for c in payload["changes"] if c["change"] == "rate-limiting")
    assert row["gate_score"] is None
    assert row["gate_tier"] == ""
