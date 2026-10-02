"""spec-proposal-constitutional-gate 1.3 — the gate's output, where reviewers look.

Stage 1 is core's deterministic lint (`otaman_core.spec_gate.lint_proposal`, core
#48): a 0-100 score, its tier, and findings. Stage 2 is plugin's critic session,
which emits a `spec-proposal-critique-result` bus message (plugin's
`spec_critique_dispatch`). This renders both — on the console proposal row (D5) and
in `otaman spec status` — and nothing here decides anything.

**D1, comment-never-block, is the load-bearing constraint.** A failing proposal is
visibly failing in the queue and still reaches the human; approval stays their
decision. So every function here returns a rendering, never a verdict a caller could
branch on to refuse something, and `otaman spec status` exits the same code whatever
the score. A surface that let a caller block on a score would reintroduce the veto the
ruling exists to forbid.

Three findings from building it, each of which shapes the code:

**Nothing calls `lint_proposal` today.** Core ships it (1.1, ticked) and no caller
exists in any repo, so no score is persisted anywhere. D6 forbids a parallel state
store, so the score is COMPUTED from the proposal on the surface's own terms — which
is also why `score_for` takes an already-parsed mapping rather than reading a file:
the console derives at ingest, not on the render path.

**No critique-result message exists on this fleet yet.** So "not critiqued" has to be
a rendering rather than an absence — a blank where a verdict goes is indistinguishable
from a pass.

**`spec-proposal-critique-result` is NOT in core's `VALID_TYPES`.** Plugin flagged the
registry gap and documented that `otaman send` warns and delivers anyway. This matches
on the type string regardless, because that is what plugin emits and what the delta
names; if the type is later registered, nothing here changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The message type plugin's critic emits. Kept as a literal rather than imported
#: from otaman_plugin: this surface must render a critique that is already ON the bus
#: even where the plugin package is not installed, and a hard import would make the
#: console's queue row depend on a sibling it does not otherwise need.
CRITIQUE_RESULT_TYPE = "spec-proposal-critique-result"

#: Plugin's `derive_verdict` vocabulary. `has-comments` is the D1 shape made literal:
#: the common outcome is neither pass nor block.
VERDICTS = ("pass", "fail", "has-comments")

#: Rendered where no critique has run. Distinct from a pass, and from a blank.
NOT_CRITIQUED = "not critiqued"

#: Rendered where the score could not be computed — an old bundle, or a proposal
#: whose fields the lint cannot read. Never a zero: a zero is a real, terrible score
#: and "we could not score it" is not a score at all.
NOT_SCORED = "not scored"

#: Why a proposal written before the seven-section template cannot be scored. A
#: DATING fact, not a verdict: the row stays unannotated for it (`row_suffix`), while
#: the fuller renderings, which have room to explain, still say it.
PREDATES_TEMPLATE = "predates the decision-grade SCR template — no sections to score"

_FINDING_ITEM = re.compile(r"^\s*-\s*item:\s*(?P<item>\d+)\s*$")
_FINDING_RESULT = re.compile(r"^\s*result:\s*(?P<result>\S+)\s*$")
_FINDING_NOTE = re.compile(r"^\s*note:\s*(?P<note>.*)$")


@dataclass(frozen=True)
class CritiqueFinding:
    """One rubric item from the critic, mirroring plugin's emitted shape."""

    item: int
    result: str
    note: str = ""


@dataclass
class Critique:
    """A parsed `spec-proposal-critique-result`, or the absence of one."""

    verdict: str = ""
    critic: str = ""
    constitution_version: str = ""
    pass_index: int = 0
    findings: list[CritiqueFinding] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return bool(self.verdict)

    @property
    def label(self) -> str:
        return self.verdict if self.ran else NOT_CRITIQUED


def _core() -> Any | None:
    """core's spec_gate module, or None on a bundle that predates it."""
    try:
        from otaman_core import spec_gate
    except Exception:  # noqa: BLE001 - absent → not-scored, never a zero
        return None
    needed = ("lint_proposal", "score_tier")
    return spec_gate if all(hasattr(spec_gate, n) for n in needed) else None


@dataclass
class Score:
    """Stage 1's visible output (D5), or why there is none."""

    value: int | None = None
    tier: str = ""
    findings: tuple[tuple[str, str, str], ...] = ()
    error: str = ""

    @property
    def scored(self) -> bool:
        return self.value is not None

    @property
    def predates(self) -> bool:
        """This proposal was written before the rubric existed (not a low score)."""
        return self.error == PREDATES_TEMPLATE

    @property
    def label(self) -> str:
        if not self.scored:
            return NOT_SCORED
        return f"{self.value}/100 {self.tier}"


def score_for(
    proposal: dict[str, Any],
    *,
    platform_repos: list[str] | tuple[str, ...],
    required_fields: tuple[str, ...] | None = None,
) -> Score:
    """Stage 1's score for an already-parsed *proposal* mapping.

    Delegates to core's `lint_proposal` — the single home for the rule. Takes a
    mapping rather than a path because the console derives at ingest (crs D1: the
    render path does no IO), and because a surface that re-read the file per row
    would be the thing the no-render-path-IO guard forbids.

    *required_fields* narrows core's default set; `score_for_scr` is where the one
    narrowing this surface makes is decided and explained.
    """
    core = _core()
    if core is None:
        return Score(error="otaman-core does not carry the stage-1 lint (JTBD-57 1.1)")
    extra = {} if required_fields is None else {"required_fields": required_fields}
    try:
        result = core.lint_proposal(proposal, platform_repos=platform_repos, **extra)
    except Exception as exc:  # noqa: BLE001 - an unlintable proposal is not a zero
        return Score(error=f"could not lint: {type(exc).__name__}")
    findings = tuple(
        (str(getattr(f, "code", "")), str(getattr(f, "level", "")), str(getattr(f, "message", "")))
        for f in getattr(result, "findings", ())
    )
    return Score(value=int(result.score), tier=str(result.tier), findings=findings)


def score_for_scr(
    body: str,
    *,
    subject: str = "",
    platform_repos: list[str] | tuple[str, ...],
    openspec: dict[str, Any] | None = None,
) -> Score:
    """Stage 1's score for a real SCR BODY, via core's own extractor (core #106).

    The score arm of this surface was empty until now for a concrete reason: scoring
    a real SCR needs a mapping from the seven decision-grade sections to lint fields,
    and hand-rolling that in cli would have been a second extractor beside core's
    rule. `proposal_from_scr` (spec-agent ruling A, core #106) is that single home,
    so this is a two-call wrapper and nothing more.

    **`outcome` is required only when an `.openspec.yaml` is supplied.** The outcome
    id is assigned by spec-agent at AUTHORING; a proposer filing an SCR cannot know
    it, and `.openspec.yaml` does not exist yet. Measured on a complete, honest SCR:
    75/100 `strong` with the field required, 100/100 `excellent` without — a
    permanent error finding on every proposal ever filed, for a field nobody could
    have supplied. That is the same reasoning core applied when it dropped
    `artifacts` from the required set ("there are no artifacts at SCR time"), applied
    to the one field whose answer arrives one stage later.
    """
    core = _core()
    if core is None:
        return Score(error="otaman-core does not carry the stage-1 lint (JTBD-57 1.1)")
    if not hasattr(core, "proposal_from_scr"):
        return Score(error="otaman-core does not carry the SCR extractor (JTBD-57, core #106)")
    try:
        mapping = core.proposal_from_scr(body, openspec, subject=subject or None)
    except Exception as exc:  # noqa: BLE001 - an unparseable SCR is not a zero
        return Score(error=f"could not read the proposal: {type(exc).__name__}")
    # The lint's check 3 scans `proposal["body"]` for pasted credentials
    # (gitleaks-lite, 1.1's "gitleaks on body"). core's extractor did not set `body`,
    # so the check was inert for every SCR it could ever have run on; core #110 sets
    # it now, which makes this `setdefault` a no-op against a current bundle and the
    # whole reason it is `setdefault` rather than an assignment. It stays as the one
    # line that keeps the scan live on a core that predates #110 — cli pins no core
    # version, so "the bundle is older" is a real state, not a hypothetical.
    mapping.setdefault("body", body)
    # A proposal with NOT ONE decision-grade section is not a 0 — it is a document
    # written before the rubric existed. Measured on this bus: 19 pending approval
    # items resolve to SCRs filed 2026-09-11, all seven sections absent because the
    # template did not exist yet, every one scoring 0/100 `failing`. A red badge on
    # 19 legitimate items is how a reviewer learns to ignore the number. A NEW SCR
    # cannot reach this state — `otaman propose` refuses to file one with unfilled
    # sections — so an empty section set dates the document rather than judging it.
    sections = tuple(getattr(core, "SECTION_KEYS", ()) or ())
    if sections and not any(str(mapping.get(key) or "").strip() for key in sections):
        return Score(error=PREDATES_TEMPLATE)
    required = None
    if openspec is None:
        required = tuple(
            field_name
            for field_name in getattr(core, "DEFAULT_REQUIRED_FIELDS", ())
            if field_name != "outcome"
        )
    return score_for(mapping, platform_repos=platform_repos, required_fields=required)


def parse_critique(body: str) -> Critique:
    """Parse plugin's emitted critique body.

    The format is plugin's `build_critique_result`: `verdict:`, `critic:`,
    `constitution_version:`, `pass_index:`, then a `findings:` list of
    item/result/note. Values are repr-quoted by the emitter, so quotes are stripped.

    Tolerant by design: a body that does not parse yields a Critique that did NOT run
    rather than a half-populated one, because a verdict is the thing a reviewer acts
    on and a guessed verdict is worse than an absent one.
    """
    critique = Critique()
    if not body:
        return critique
    item: int | None = None
    result = ""
    note = ""

    def _flush() -> None:
        nonlocal item, result, note
        if item is not None:
            critique.findings.append(CritiqueFinding(item=item, result=result, note=note))
        item, result, note = None, "", ""

    def _unquote(raw: str) -> str:
        text = raw.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
            return text[1:-1]
        return text

    for line in body.splitlines():
        m = _FINDING_ITEM.match(line)
        if m:
            _flush()
            item = int(m.group("item"))
            continue
        if item is not None:
            m = _FINDING_RESULT.match(line)
            if m:
                result = m.group("result")
                continue
            m = _FINDING_NOTE.match(line)
            if m:
                note = _unquote(m.group("note"))
                continue
        stripped = line.strip()
        if stripped.startswith("verdict:"):
            value = _unquote(stripped[len("verdict:") :])
            # Only a vocabulary verdict is honored: a body carrying `verdict: looks
            # fine to me` must not reach a reviewer as though the critic ruled.
            if value in VERDICTS:
                critique.verdict = value
        elif stripped.startswith("critic:"):
            critique.critic = _unquote(stripped[len("critic:") :])
        elif stripped.startswith("constitution_version:"):
            critique.constitution_version = _unquote(stripped[len("constitution_version:") :])
        elif stripped.startswith("pass_index:"):
            try:
                critique.pass_index = int(_unquote(stripped[len("pass_index:") :]))
            except ValueError:
                critique.pass_index = 0
    _flush()
    return critique


def latest_critique(messages: list[Any]) -> Critique:
    """The critique for one change: the HIGHEST pass index wins.

    Two passes are allowed (plugin's MAX_PASSES), and pass 2 exists precisely because
    pass 1's verdict was not the final word. Showing the first would show the stale
    one; showing "two verdicts" would make a reviewer pick.
    """
    best = Critique()
    for message in messages:
        if str(getattr(message, "msg_type", "")) != CRITIQUE_RESULT_TYPE:
            continue
        parsed = parse_critique(str(getattr(message, "body", "")))
        if parsed.ran and parsed.pass_index >= best.pass_index:
            best = parsed
    return best


def row_suffix(score: Score, critique: Critique) -> str:
    """The console proposal row's gate annotation (D5).

    Appended, never substituted: the row's existing text identifies WHICH proposal,
    and a reviewer who cannot tell the items apart cannot triage them by number
    either. Empty only when there is genuinely nothing to say — no score AND no
    critique AND no reason — so a healthy row does not grow a column of "unknown".
    """
    parts: list[str] = []
    if score.scored:
        parts.append(f"gate {score.label}")
    elif score.error and not score.predates:
        # A pre-template proposal is NOT annotated: 9 of the 19 approval items on this
        # fleet resolve to SCRs filed before the sections existed, and `gate not
        # scored` on each is the column of "unknown" this function exists to avoid —
        # nobody can make a 2026-09-11 proposal decision-grade retroactively.
        parts.append(f"gate {NOT_SCORED}")
    if critique.ran:
        parts.append(f"critique {critique.verdict}")
    if not parts:
        return ""
    return "  [" + " · ".join(parts) + "]"


def render_lines(score: Score, critique: Critique) -> list[str]:
    """The full gate record for `otaman spec status` and the detail view.

    COMMENT, never block (D1): a `fail` verdict and a 12/100 score render as
    information, with no refusal and no exit-code consequence. The line saying so is
    deliberate — a reviewer seeing `fail` needs to know the gate is not holding the
    proposal, or they will wait for an approval that nothing is blocking.
    """
    lines: list[str] = []
    if score.scored:
        lines.append(f"stage 1: {score.label}")
        for code, level, message in score.findings:
            lines.append(f"  {level}: {message} [{code}]")
    else:
        lines.append(f"stage 1: {NOT_SCORED}" + (f" — {score.error}" if score.error else ""))
    if critique.ran:
        where = f" by {critique.critic}" if critique.critic else ""
        which = f", pass {critique.pass_index}" if critique.pass_index else ""
        version = (
            f", constitution {critique.constitution_version}"
            if critique.constitution_version
            else ""
        )
        lines.append(f"stage 2: {critique.verdict}{where}{which}{version}")
        for finding in critique.findings:
            note = f" — {finding.note}" if finding.note else ""
            lines.append(f"  item {finding.item}: {finding.result}{note}")
    else:
        lines.append(f"stage 2: {NOT_CRITIQUED}")
    if score.scored or critique.ran:
        lines.append("the gate comments; it does not block — approval stays with the human")
    return lines


__all__ = [
    "CRITIQUE_RESULT_TYPE",
    "NOT_CRITIQUED",
    "NOT_SCORED",
    "VERDICTS",
    "Critique",
    "CritiqueFinding",
    "Score",
    "latest_critique",
    "parse_critique",
    "render_lines",
    "row_suffix",
    "score_for",
]
