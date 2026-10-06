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

LABEL = "Original proposal"

#: Tolerates the backticked form, which older minted broadcasts on this bus carry
#: (`**Original proposal**: `<stem>``) alongside the bare form the writer emits today.
#: A parser that only accepted today's spelling would silently fail to link four months
#: of history — and "no approval found" is indistinguishable from "not yet approved".
_RE = re.compile(rf"\*\*{LABEL}\*\*:\s*`?([^\s`]+)`?", re.IGNORECASE)

__all__ = ["LABEL", "parse_original_proposal", "render_original_proposal"]


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
