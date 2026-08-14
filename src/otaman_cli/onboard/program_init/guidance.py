"""Post-init next-step guidance generator (tasks.md 6.1).

Generates role- and process-appropriate next-step guidance after a
successful ``program-init``.  The output is a plain-text message that
each user role can act on immediately.

Design: rule table — each entry has a callable predicate (lambda) and a
next-step command.  Using callables instead of eval()-on-strings eliminates
the security noise noted in the CTO review.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class GuidanceEntry:
    predicate: Callable[[dict[str, Any]], bool]  # receives answers dict
    command: str  # shell command to run
    description: str  # one-line human description


def _has_process(name: str) -> Callable[[dict[str, Any]], bool]:
    return lambda a: name in a.get("processes", [])


# Ordered — first matching entries are shown first
_GUIDANCE_TABLE: list[GuidanceEntry] = [
    GuidanceEntry(
        predicate=lambda _: True,
        command="otaman check",
        description="Check your agent inbox and see pending tasks",
    ),
    GuidanceEntry(
        predicate=_has_process("outcomes"),
        command="otaman outcome add",
        description="Draft your first business outcome (CPO role)",
    ),
    GuidanceEntry(
        predicate=_has_process("solutions"),
        command="otaman solution add",
        description="Propose a solution for your first outcome (CTO role)",
    ),
    GuidanceEntry(
        predicate=_has_process("vocabulary"),
        command="otaman vocab add",
        description="Add canonical terms to your vocabulary registry",
    ),
    GuidanceEntry(
        predicate=_has_process("risks"),
        command="otaman risk add",
        description="Register your first risk in the PMI risk register",
    ),
    GuidanceEntry(
        predicate=_has_process("strategy"),
        command="otaman pitch add",
        description="Start your first pitch deck (cofounder role)",
    ),
    GuidanceEntry(
        predicate=_has_process("strategy"),
        command="otaman plan add",
        description="Draft your first business plan",
    ),
    GuidanceEntry(
        predicate=lambda _: True,
        command="otaman skill list",
        description="Review active skills for your program",
    ),
    GuidanceEntry(
        predicate=lambda a: a.get("active_edition") == "ee",
        command="otaman doctor --show-edition",
        description="Verify EE capabilities and license-gated features",
    ),
]


def generate_guidance(answers: dict[str, Any]) -> list[tuple[str, str]]:
    """Return list of (command, description) pairs for the current answers.

    Only entries whose predicate returns True are included.
    """
    result: list[tuple[str, str]] = []
    for entry in _GUIDANCE_TABLE:
        try:
            if entry.predicate(answers):
                result.append((entry.command, entry.description))
        except Exception:
            continue
    return result


def print_guidance(answers: dict[str, Any], program_name: str) -> None:
    """Print the post-init guidance block to stdout."""
    entries = generate_guidance(answers)
    if not entries:
        return

    print()
    print(f"  Program '{program_name}' is initialized.  Next steps:")
    print()
    for cmd, desc in entries:
        print(f"    - Run `{cmd}`")
        print(f"      {desc}")
        print()
