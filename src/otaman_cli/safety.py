"""Shared confirmation gate for DESTRUCTIVE-CROSS-DIRECTORY commands.

See openspec/changes/destructive-command-safety/ (design.md's command-risk
classification, spec.md's formal requirements) for the full rationale --
triggered by the 2026-07-04 `otaman migrate` incident, where a command
mutated a directory resolved via upward path-walking with no echo and no
confirmation gate.

Lives in otaman-cli, not otaman-core, per design.md's two-consumer-rule
decision: only otaman-cli needs this today. Revisit only if otaman-plugin's
launcher scripts later want the same pattern for their own destructive-ish
operations.
"""

from __future__ import annotations

import sys


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
