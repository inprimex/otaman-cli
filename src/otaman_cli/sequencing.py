"""Task-sequencing contract helpers (task-sequencing-contract, JTBD-67/D6).

Machine-readable coordination for multi-step work items: sequenced
task-assignments carry ``sequence-id`` / ``step: <n>/<m>`` /
``depends-on`` / ``stop-at`` frontmatter AND the prose coordination
sections (Sequence / Your step / Handoff, plus Context / Artifacts) —
per Roman's review the two halves travel together; either without the
other is malformed and refused at send time.

Pure helpers, no I/O, no UI imports — the MCP ``otaman_send`` (plugin's
bus_server) imports these for parity, same pattern as cc_fanout and
bus_target.
"""

from __future__ import annotations

import re
from typing import Any

#: Frontmatter fields of the contract, in emission order.
SEQ_FIELDS = ("sequence-id", "step", "depends-on", "stop-at")

#: Coordination section headers. The first three are unconditional for a
#: sequenced assignment; Context/Artifacts detect the "sections present"
#: half of the travel-together rule but are not individually mandatory
#: (Context only for larger-scope items; Artifacts may be "none").
COORD_SECTIONS = ("## Sequence", "## Your step", "## Handoff", "## Context", "## Artifacts")
_REQUIRED_SECTIONS = ("## Sequence", "## Your step", "## Handoff")

_STEP_RE = re.compile(r"^(\d+)/(\d+)$")
_SEQ_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DEP_STEP_RE = re.compile(r"\bstep\s+(\d+)\b", re.IGNORECASE)
_DONE_RE = re.compile(r"\bdone\b", re.IGNORECASE)


def parse_step(value: str) -> tuple[int, int] | None:
    """``"4/5"`` -> ``(4, 5)``; None when malformed (incl. n<1 or n>m)."""
    m = _STEP_RE.match(str(value).strip())
    if not m:
        return None
    n, total = int(m.group(1)), int(m.group(2))
    if n < 1 or total < 1 or n > total:
        return None
    return n, total


def body_coordination_sections(body: str) -> list[str]:
    """Coordination section headers present in *body* (line-anchored)."""
    found = []
    for line in body.splitlines():
        stripped = line.strip()
        for section in COORD_SECTIONS:
            if stripped == section or stripped.startswith(section + " "):
                found.append(section)
    return found


def validate_sequencing(
    fields: dict[str, Any],
    body: str,
) -> list[str]:
    """Validate the sequencing half of a task-assignment; return error strings.

    *fields* holds whichever of :data:`SEQ_FIELDS` the sender supplied
    (missing/None values mean "not supplied"). Enforces well-formedness
    AND the travel-together rule in both directions. An empty error list
    means the message is well-formed — including the unsequenced case
    (no fields, no sections).
    """
    errors: list[str] = []
    supplied = {k: v for k, v in fields.items() if v not in (None, "", [])}
    sections = body_coordination_sections(body)

    if not supplied and not sections:
        return errors  # unsequenced single-task assignment — fine

    # Travel-together (Roman's review): each half requires the other.
    if sections and not supplied:
        errors.append(
            f"body carries coordination section(s) {sorted(set(sections))} but the "
            "sequencing frontmatter (--sequence-id/--step/--stop-at) is missing — "
            "they travel together"
        )
        return errors
    if supplied:
        missing_sections = [s for s in _REQUIRED_SECTIONS if s not in sections]
        if missing_sections:
            errors.append(
                f"sequencing frontmatter supplied but body lacks coordination "
                f"section(s) {missing_sections} — they travel together"
            )

    # Field well-formedness.
    seq_id = supplied.get("sequence-id")
    if not seq_id:
        errors.append("sequence-id is required for a sequenced assignment")
    elif not _SEQ_ID_RE.match(str(seq_id)):
        errors.append(
            f"malformed sequence-id {seq_id!r}: lowercase slug ([a-z0-9][a-z0-9._-]*, max 64 chars)"
        )

    step_raw = supplied.get("step")
    parsed = parse_step(step_raw) if step_raw else None
    if not step_raw:
        errors.append("step is required for a sequenced assignment (format <n>/<m>)")
    elif parsed is None:
        errors.append(f"malformed step {step_raw!r}: must be <n>/<m> with 1 <= n <= m")

    if not supplied.get("stop-at"):
        errors.append("stop-at is required for a sequenced assignment")

    deps = supplied.get("depends-on") or []
    if isinstance(deps, str):
        deps = [deps]
    if parsed:
        n, total = parsed
        for dep in deps:
            for ref in _DEP_STEP_RE.finditer(str(dep)):
                ref_n = int(ref.group(1))
                if ref_n < 1 or ref_n > total:
                    errors.append(
                        f"depends-on references unknown step {ref_n} (sequence has {total} step(s))"
                    )
                elif ref_n == n:
                    errors.append(f"depends-on references the assignment's own step {ref_n}")
        if n == 1 and not deps:
            pass  # empty depends-on is the norm for step 1
    return errors


def render_frontmatter_lines(fields: dict[str, Any]) -> str:
    """The frontmatter lines for a sequenced message ('' when unsequenced)."""
    supplied = {k: v for k, v in fields.items() if v not in (None, "", [])}
    if not supplied:
        return ""
    lines = []
    for key in SEQ_FIELDS:
        value = supplied.get(key)
        if value in (None, "", []):
            continue
        if key == "depends-on":
            deps = [value] if isinstance(value, str) else list(value)
            lines.append(f"depends-on: [{', '.join(str(d) for d in deps)}]")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n" if lines else ""


def waiting_annotation(fm: dict[str, Any], body: str = "") -> str | None:
    """Advisory ``waiting on step <n> (<owner>)`` for `otaman check`.

    A pending sequenced task-assignment is "waiting" when any
    ``depends-on`` entry references a step NOT marked done (senders stamp
    completion in the entry text, e.g. ``step 3 (DONE — ...)``). Advisory
    only — display, never enforcement (Roman: evaluate in practice first).
    Owner is read from the body's ``## Sequence`` list when parseable.
    """
    deps = fm.get("depends-on") or []
    if isinstance(deps, str):
        deps = [deps]
    for dep in deps:
        dep_s = str(dep)
        step_ref = _DEP_STEP_RE.search(dep_s)
        if step_ref and not _DONE_RE.search(dep_s):
            n = step_ref.group(1)
            owner = "?"
            m = re.search(rf"^\s*{n}\.\s+([A-Za-z0-9_-]+)", body, re.MULTILINE) or re.search(
                rf"\b{n}\.\s+([A-Za-z0-9_-]+)\s+—", body
            )
            if m:
                owner = m.group(1)
            return f"waiting on step {n} ({owner})"
    return None


__all__ = [
    "COORD_SECTIONS",
    "SEQ_FIELDS",
    "body_coordination_sections",
    "parse_step",
    "render_frontmatter_lines",
    "validate_sequencing",
    "waiting_annotation",
]
