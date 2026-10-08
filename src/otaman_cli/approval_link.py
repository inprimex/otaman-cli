"""The link from an approval/rejection broadcast back to the proposal it decides.

ONE home for a format that was written in one place and read in another, which is
exactly how it broke.

`otaman approve` mints the broadcast with the originating stem in the BODY:

    **Original proposal**: 20261005T120000-deploy-agent-to-human-spec-change-request

`otaman check` looked for that stem in the broadcast's SUBJECT, which carries the
change TITLE instead. Measured on the live bus 2026-10-06: **0 of 12** approval
broadcasts have a stem-shaped string in their subject, and 104 carry the body line. So
the approval could never be detected and every decided proposal reported as still
"waiting for human approval" — deploy-agent saw three of theirs listed for two days
after Roman approved them (20261006T144602).

A writer and a reader naming different fields is the third instance of that shape in
one day (the others: `load_reports` never called in #286, `in-reply-to` written by HITL
but unsettable by `otaman send` in #287). Hence a module rather than a second grep:
the format is rendered and parsed here, so a change to one is a change to both.
"""

from __future__ import annotations

import re
from pathlib import Path

LABEL = "Original proposal"

#: The disposition verdict line, alongside the back-link, on a spec-agent disposition
#: message (seam ruled 20261007T193507): `**Disposition**: already-delivered|duplicate|
#: absorbed-into <target>`. A dispositioned SCR is DECIDED — the human should not
#: re-review an absorbed duplicate — so the readers treat it like an approval.
DISPOSITION_LABEL = "Disposition"

#: Only the agent that owns `dispositions.yaml` emits them, so the ledger and the bus
#: cannot disagree (spec-agent's one-producer rule). Enforced in the READER too: without
#: it, any agent could mint a `type: info` carrying a back-link and silently clear items
#: off the human's mandatory review queue — a worse failure than the ghosts this fixes,
#: because a ghost is visible and a vanished review is not.
DISPOSITION_SENDER = "spec-agent"

#: Tolerates the backticked form, which older minted broadcasts on this bus carry
#: (`**Original proposal**: `<stem>``) alongside the bare form the writer emits today.
#: A parser that only accepted today's spelling would silently fail to link four months
#: of history — and "no approval found" is indistinguishable from "not yet approved".
_RE = re.compile(rf"\*\*{LABEL}\*\*:\s*`?([^\s`]+)`?", re.IGNORECASE)

__all__ = [
    "DISPOSITION_LABEL",
    "decided_stems",
    "DISPOSITION_SENDER",
    "LABEL",
    "is_disposition",
    "parse_disposition",
    "parse_original_proposal",
    "render_original_proposal",
]


def render_original_proposal(stem: str) -> str:
    """The body line every approval/rejection broadcast carries."""
    return f"**{LABEL}**: {stem}"


def parse_original_proposal(body: str | None) -> str | None:
    """The stem a broadcast decides, or None when the body carries no link.

    None is a real answer, not a failure: a broadcast minted by hand, or by a sibling
    that does not follow this convention, genuinely links to nothing — and inventing a
    guess is how a wrong proposal gets marked approved.
    """
    if not body:
        return None
    m = _RE.search(body)
    if not m:
        return None
    return m.group(1).strip() or None


_DISPOSITION_RE = re.compile(rf"\*\*{DISPOSITION_LABEL}\*\*:\s*(\S[^\n]*)", re.IGNORECASE)


def parse_disposition(body: str | None) -> str | None:
    """The disposition verdict a body records, or None when it records none."""
    if not body:
        return None
    m = _DISPOSITION_RE.search(body)
    if not m:
        return None
    return m.group(1).strip() or None


def is_disposition(sender: str | None, body: str | None) -> bool:
    """True when *body* is a disposition FROM its one authorised producer.

    Both halves are required. A disposition line from anyone else is not a decision —
    it is an agent asserting that the human need not look at something, which is the
    one direction this reader must never take on trust.
    """
    if (sender or "").strip() != DISPOSITION_SENDER:
        return False
    return parse_disposition(body) is not None and parse_original_proposal(body) is not None


#: Decided-broadcast filename patterns. Globbed rather than content-scanned: 7,364
#: active files, and reading all of them on a console render path is the cost 2.3's
#: guard exists to prevent.
_VERDICT_GLOBS = (
    ("*spec-change-approved*.md", "approved"),
    ("*spec-change-rejected*.md", "rejected"),
)

#: Dispositions carry no contractual filename token — spec-agent's first emission was
#: slugged "disposition-emitted-for-one-of-the-resid" from its SUBJECT, which is not a
#: promise. The SENDER is contractual, and filenames encode it, so the scan is scoped
#: by sender (945 active + 456 archived files instead of 7,364) and the frontmatter is
#: still what decides.
_DISPOSITION_GLOB = f"*{DISPOSITION_SENDER}-to-*.md"


def _sender_from_frontmatter(text: str) -> str | None:
    for line in text.splitlines()[:40]:
        if line.startswith("from:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("## "):
            break
    return None


def decided_stems(active_dir: Path, archive_dir: Path | None = None) -> dict[str, str]:
    """Proposal stem -> the verdict that settles it: approved / rejected / dispositioned.

    Scans the ARCHIVE as well as active, which is the bug this function exists to fix.
    #288 and #290 both globbed `active/` only, so a decision that had been archived
    became invisible and its proposal RESURFACED on the human's mandatory queue — the
    exact ghost those changes removed. Found on 2026-10-08: a proposal rejected on
    08-24 reappeared because the rejection sits in `archive/2026-08/`, with 14 decided
    broadcasts already archived and the count only growing.

    Returns the KIND, not just membership, because `otaman check` renders an approval
    ("READY TO RESUME") differently from a rejection ("REJECTED") and a single home must
    serve both readers without either re-deriving it.
    """
    out: dict[str, str] = {}
    dirs = [d for d in (active_dir, archive_dir) if d is not None and d.is_dir()]

    for d in dirs:
        for pattern, verdict in _VERDICT_GLOBS:
            # rglob so an archive laid out as archive/<month>/ is covered without
            # hard-coding its shape.
            for f in d.rglob(pattern):
                try:
                    stem = parse_original_proposal(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if stem:
                    out.setdefault(stem, verdict)

        for f in d.rglob(_DISPOSITION_GLOB):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if DISPOSITION_LABEL not in text:
                continue
            if not is_disposition(_sender_from_frontmatter(text), text):
                continue
            stem = parse_original_proposal(text)
            if stem:
                out.setdefault(stem, "dispositioned")
    return out
