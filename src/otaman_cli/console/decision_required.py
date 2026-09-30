"""delivery-authorization-envelope 1.2 — decision-required on the human's surfaces.

An agent that reaches a decision only the human can make must EMIT rather than
freeze. Core's 1.1 gave that emission a type and a schema (`decision-required`,
carrying the agent, the decision, what it blocks, and what would unblock it);
this makes the console's awaiting-you surfaces see it, and makes answering it
reach the agent that is waiting.

Without this half the type is write-only: an agent emits, nothing surfaces it,
and the human still finds out by noticing a pane that has not moved — which is
the failure the whole change exists to remove, relocated rather than fixed.

The answer routes through `otaman send`, the same verb a human would type at the
shell (D4). One write path means the console cannot drift from the CLI, and it
means the reply is an ordinary bus message the emitting agent already knows how
to receive.
"""

from __future__ import annotations

from typing import Any

#: The type core's 1.1 registered.
DECISION_REQUIRED = "decision-required"


def needs_answer(msg_type: str) -> bool:
    """Is this a question the human has to answer (rather than approve/reject)?

    Deliberately NOT folded into `is_decision`. That property drives the
    approve/reject/defer keys, and a decision-required is not any of those —
    offering `a` on it would promise an action that does not apply to it.
    """
    return msg_type == DECISION_REQUIRED


def blocked_refs(blocks: Any) -> set[str]:
    """What a decision-required's `blocks:` field names.

    Accepts the shapes the field is written in — a bare id, a comma list, or a
    "task 2.1 of change-name" phrase — and returns the identifiers found.
    Tolerant on purpose: a `blocks:` the console cannot parse must not make the
    message invisible, so an unrecognised shape yields no refs rather than
    dropping the row that carries it.
    """
    text = str(blocks or "").strip()
    if not text:
        return set()
    refs: set[str] = set()
    for piece in text.replace(";", ",").split(","):
        token = piece.strip()
        if not token:
            continue
        # "task 2.1 of console-reactive-store" → the change slug is the ref the
        # tree can mark; the task id alone matches no artifact node.
        if " of " in token:
            token = token.split(" of ", 1)[1].strip()
        token = token.strip("`'\"")
        if token:
            refs.add(token)
    return refs


def answer_argv(recipient: str, subject: str, body: str) -> list[str]:
    """`otaman send` argv that routes an answer back to the emitting agent.

    The recipient is the message's `from:` — the agent that is blocked — not the
    human and not a broadcast. An answer sent anywhere else leaves the waiting
    agent waiting.
    """
    return [
        "send",
        recipient,
        "--type",
        "info",
        "--subject",
        subject,
        "--body",
        body,
    ]


def answer_subject(original_subject: str) -> str:
    """`Re: <what was asked>` — so the agent's own check output names the
    question it is an answer to."""
    text = (original_subject or "").strip() or "your decision-required"
    return text if text.lower().startswith("re:") else f"Re: {text}"


__all__ = [
    "DECISION_REQUIRED",
    "answer_argv",
    "answer_subject",
    "blocked_refs",
    "needs_answer",
]
