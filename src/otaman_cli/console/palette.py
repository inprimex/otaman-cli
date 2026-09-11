"""Shared status/priority palette + short-form helpers (tree-view-polish 1.2).

One source of truth for how registry statuses and priorities render across every
console surface — the artifact tree, the lifecycle view, detail panels. Roman's
live tree leaked raw enum reprs (`Priority.P1 [OutcomeStatus.APPROVED]`) with no
color; the ruling is short lowercase value words (`p1`, `approved`) each in a
distinct palette color, and NO per-screen palettes (interactive-human-console
spec: "sourced from one shared palette across console surfaces").
"""

from __future__ import annotations

#: distinct color per priority value (P0 hottest → P3 muted).
PRIORITY_STYLE = {
    "P0": "bright_red",
    "P1": "orange1",
    "P2": "gold1",
    "P3": "grey62",
}

#: distinct color per status value, shared across surfaces (moved here from the
#: TreeScreen's private map so the lifecycle/detail surfaces share the source).
STATUS_STYLE = {
    "Done": "green",
    "Complete": "green",
    "Approved": "green",
    "complete-unarchived": "green",
    "Discarded": "red",
    "Retired": "dim",
    "In-Progress": "yellow",
    "in-flight": "yellow",
    "Drafting": "yellow",
    "Considering": "yellow",
    "Backlog": "cyan",
}

#: grayed rows (decided-out sibling solutions, closed items shown under `f`).
GRAY_STYLE = "grey42"


def _canon(value: object) -> str:
    """Canonical value string for a status/priority — the enum ``.value`` when
    given an Enum, else the string with any ``EnumName.`` repr prefix stripped so
    a stray ``OutcomeStatus.APPROVED`` never leaks into a console surface."""
    val = getattr(value, "value", value)
    s = "" if val is None else str(val)
    # a raw enum repr like "OutcomeStatus.APPROVED" — keep only the member tail.
    if "." in s and " " not in s and "-" not in s.split(".")[0]:
        s = s.rsplit(".", 1)[1]
    return s


def short_priority(value: object) -> str:
    """`P1` → `p1`; empty for none/unknown."""
    return _canon(value).lower()


def short_status(value: object) -> str:
    """`Approved` → `approved`, `In-Progress` → `in-progress`; empty for none."""
    return _canon(value).lower()


def priority_style(value: object) -> str:
    """Palette color for a priority value, or `""` when unmapped."""
    return PRIORITY_STYLE.get(_canon(value), "")


def status_style(value: object) -> str:
    """Palette color for a status value, or `""` when unmapped."""
    return STATUS_STYLE.get(_canon(value), "")


__all__ = [
    "PRIORITY_STYLE",
    "STATUS_STYLE",
    "GRAY_STYLE",
    "short_priority",
    "short_status",
    "priority_style",
    "status_style",
]
