"""Shared confirmation gates for DESTRUCTIVE and PRIVILEGED commands.

`confirm_destructive_operation` — see
openspec/changes/destructive-command-safety/ (design.md's command-risk
classification, spec.md's formal requirements) for the full rationale --
triggered by the 2026-07-04 `otaman migrate` incident, where a command
mutated a directory resolved via upward path-walking with no echo and no
confirmation gate.

`confirm_human_decision` — F012 (security GAP finding, 2026-07-04): gates
commands that produce a PRIVILEGED bus message (one asserting `from: human`
-- `otaman approve`, `otaman emergency-halt`). Deliberately has NO
`--yes`/scripted bypass, unlike `confirm_destructive_operation` above: the
whole point is that a Bash-tool-driven agent session (which has no real
TTY) cannot satisfy it, only an actual human at an actual terminal can. Any
override flag here would hand agents a scriptable way around the human
gate it exists to enforce.

Lives in otaman-cli, not otaman-core, per design.md's two-consumer-rule
decision: only otaman-cli needs this today. Revisit only if otaman-plugin's
launcher scripts later want the same pattern for their own destructive-ish
operations.
"""

from __future__ import annotations

import sys
from enum import Enum


class SafetyTier(str, Enum):
    """Command safety classification, weakest → strongest.

    hitl-confirmation-adapters 1.1 adds HUMAN_DECISION as a tier above
    DESTRUCTIVE_CROSS_DIRECTORY: it is the single point where human
    authority enters the spec workflow (separation of duties — an agent
    cannot approve its own proposal), and it routes through the
    confirmation-adapter framework rather than reinventing per-command
    guards. `approve` is the first member; future sign-off commands
    inherit the tier instead of hand-rolling a TTY check.
    """

    DESTRUCTIVE_CROSS_DIRECTORY = "destructive-cross-directory"
    HUMAN_DECISION = "human-decision"


# Commands whose execution asserts a live human decision. Membership here
# (not an ad-hoc `confirm_human_decision` call) is what routes a command
# through the adapter framework.
HUMAN_DECISION_COMMANDS: frozenset[str] = frozenset({"approve"})


def classify_command(name: str) -> SafetyTier | None:
    """Return the safety tier for *name*, or None if unclassified."""
    if name in HUMAN_DECISION_COMMANDS:
        return SafetyTier.HUMAN_DECISION
    return None


def confirm_destructive_operation(
    description: str,
    targets: list[str] | str,
    *,
    yes: bool = False,
) -> bool:
    """Echo *description* + *targets*, then gate on explicit confirmation.

    Interactive TTY: prompts ``Proceed? [y/N]``, defaults to refusing on
    any answer other than y/yes.

    Non-interactive (no TTY) or scripted: requires ``yes=True`` (the
    command's own ``--yes`` flag) and refuses -- without prompting -- if
    it's absent. Never silently assumes consent.

    Returns True if the caller should proceed, False if it should abort
    without mutating anything.
    """
    print()
    print(description)
    target_list = [targets] if isinstance(targets, str) else list(targets)
    for t in target_list:
        print(f"  {t}")
    print()

    if yes:
        return True

    if not sys.stdin.isatty():
        print(
            "Refusing to proceed without --yes in a non-interactive context.",
            file=sys.stderr,
        )
        return False

    try:
        answer = input("Proceed? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def require_interactive_tty(description: str) -> bool:
    """Bare TTY gate for flows that assert a human decision: echoes
    *description*, then refuses outright -- no prompt, no bypass -- if
    stdin isn't a real TTY.

    Split out of `confirm_human_decision` (2026-07-09, `otaman hitl take`
    TTY-gate follow-on to F012) for callers
    that already run their own multi-step interactive dialogue after the
    gate (e.g. `otaman hitl take`'s decision/rationale/followup prompts)
    and don't want a redundant type-a-phrase step layered on top -- the
    TTY check alone is the part that actually blocks a piped/non-EOF
    non-interactive stdin from forging the message, same as
    `confirm_human_decision` below.

    Returns True if stdin is a real interactive terminal (caller may go on
    to run its own prompts), False if it should abort without prompting
    further.
    """
    print()
    print(description)
    print()

    if not sys.stdin.isatty():
        print(
            "Refusing: this action asserts a human decision and requires an "
            "interactive terminal. Non-interactive/scripted callers -- "
            "including agent Bash-tool sessions -- cannot satisfy this.",
            file=sys.stderr,
        )
        return False

    return True


def confirm_human_decision(description: str, expected_phrase: str = "CONFIRM") -> bool:
    """Gate for producing a PRIVILEGED bus message (asserts ``from: human``).

    Echoes *description*, then refuses outright -- no prompt, no bypass --
    if stdin isn't a real TTY (via `require_interactive_tty`). Only when it
    is does it ask the caller to type *expected_phrase* verbatim
    (case-sensitive); anything else, or a non-interactive EOF/interrupt,
    refuses.

    This is a practical proxy for "a human is driving this" specifically
    because Claude Code's Bash tool (and similar agent-harness shells) does
    not attach a real interactive TTY to the processes it spawns -- an
    agent session cannot satisfy this check by itself, only a genuine
    terminal session can.

    Returns True if the caller should proceed, False if it should abort
    without writing anything.
    """
    if not require_interactive_tty(description):
        return False

    try:
        typed = input(f"Type '{expected_phrase}' to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return typed == expected_phrase


def record_privileged_confirmation(
    *,
    message_id: str,
    content: str,
    command: str,
    agent: str = "human",
) -> bool:
    """bus-test-isolation task 2.1 — ledger-gate a privileged bus write.

    Called by the TTY-gated producers (`approve`, `emergency-halt`,
    `hitl take`) AFTER human confirmation and BEFORE writing the bus file.
    Appends the confirmation record (message id + hash of the exact bytes
    about to be written) to ``~/.otaman/confirmations.log``; consumers
    (bridge watcher, doctor provenance audit) verify against it.

    Returns True on success. On failure prints the refusal and returns
    False — the caller MUST then not write the bus file (fail closed:
    no record, no bus file).
    """
    from otaman_core.confirmations import LedgerError, append_confirmation, hash_message

    try:
        append_confirmation(
            message_id=message_id,
            content_hash=hash_message(content),
            command=command,
            agent=agent,
        )
    except LedgerError as exc:
        print(f"  [!] Refusing to write the bus message: {exc}", file=sys.stderr)
        print(
            "      Privileged messages require a confirmation-ledger record "
            "(fail closed: no record, no bus file).",
            file=sys.stderr,
        )
        return False
    return True
