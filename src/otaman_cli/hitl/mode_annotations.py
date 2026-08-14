"""[headless] / [interactive] annotation parser for tasks.md (task 3.1).

Grammar (auto-session-spawn-on-bus-events/design.md Q2 Resolved 2026-05-21):

    task-line     ::= "- [ ] " task-num " " agent-prefix [" " mode-annot] " " body
    agent-prefix  ::= "@otaman-" repo-slug
    mode-annot    ::= "[headless]" | "[interactive]"

Rules:
- Annotation comes immediately after `@otaman-<repo>`, space-separated
- Default mode when annotation is absent: `interactive`
- Conflict (both `[headless]` and `[interactive]` on the same line): parse error
- Unknown bracketed token in the mode position (e.g. `[batch]`): parse error
- Case-sensitive — `[Headless]` is rejected

This parser is complementary to `otaman_plugin.map_tasks` (which handles the
`@otaman-<repo>` prefix and `[ ] / [x]` checkbox). It's hooked into
`cmd_assign` post-processing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["headless", "interactive"]


class ModeAnnotationError(ValueError):
    """Raised on conflict / unknown-token / case-mismatch."""


# Recognise the `@otaman-<repo>` agent prefix + everything after it.
# We split on whitespace from there to find the optional mode annotation.
_AGENT_PREFIX_RE = re.compile(r"@otaman-[a-z0-9][a-z0-9-]*")

# Any bracketed single-word token in the mode position.
_BRACKETED_TOKEN_RE = re.compile(r"^\[([a-zA-Z][a-zA-Z0-9-]*)\]$")

# Markdown task-line shape: "- [ ] N.N body"  or  "- [x] N.N body"
_TASK_LINE_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+(.+)$")


@dataclass(frozen=True)
class ResolvedTask:
    """One task line with its resolved mode."""

    line_number: int  # 1-based
    raw_line: str
    mode: Mode
    has_explicit_annotation: bool
    body: str  # task text with the mode annotation stripped


def resolve_task_mode(task_line: str) -> tuple[Mode, bool, str]:
    """Resolve the mode for a single task line.

    Returns ``(mode, has_explicit, body_without_annotation)``.

    Raises:
        ModeAnnotationError: on duplicate annotations, unknown bracketed
            tokens in the mode slot, or wrong-case spellings.
    """
    # Trim the "- [ ] N.N " prefix
    m = _TASK_LINE_RE.match(task_line)
    if not m:
        # Not a task line — nothing to resolve
        return ("interactive", False, task_line)
    payload = m.group(1)

    # Find every "@otaman-<repo>" position and look at the token RIGHT AFTER it.
    # Conflict detection: if more than one `[headless]` / `[interactive]` token
    # appears anywhere on the line, that's a conflict regardless of position.
    annotations: list[str] = []
    invalid_tokens: list[str] = []

    # First pass: scan all bracketed tokens to detect duplicates + invalid kinds
    for tok in re.findall(r"\[[^\]\s]+\]", payload):
        if tok in ("[headless]", "[interactive]"):
            annotations.append(tok)
        else:
            # Track only bracketed tokens that LOOK like a mode annotation
            # (single bracketed word, no inner whitespace) so we don't
            # false-positive on Markdown link references like `[foo]`.
            inner_match = _BRACKETED_TOKEN_RE.match(tok)
            if inner_match:
                lowered = inner_match.group(1).lower()
                if lowered in ("headless", "interactive") and tok != f"[{lowered}]":
                    # Case-mismatch — explicit error
                    raise ModeAnnotationError(
                        f"mode annotation must be lowercase: got {tok!r} (expected [{lowered}])"
                    )
                # Otherwise — could be a bracketed body marker, not necessarily an
                # error. We only flag as "unknown mode token" if it sits in the
                # immediate-after-@otaman position (checked below).
                invalid_tokens.append(tok)

    if len(annotations) > 1:
        # Either two of the same OR one headless + one interactive — both are conflicts
        raise ModeAnnotationError(
            f"conflicting mode annotations on task line: {annotations} (only one of "
            f"`[headless]` or `[interactive]` may appear)"
        )

    # Second pass: detect unknown bracketed token in the mode-position slot
    # (i.e., the first whitespace-separated token after @otaman-<repo>).
    for prefix_match in _AGENT_PREFIX_RE.finditer(payload):
        tail = payload[prefix_match.end() :].lstrip()
        if not tail:
            continue
        first_token = tail.split(None, 1)[0]
        if first_token.startswith("[") and first_token.endswith("]"):
            if first_token in ("[headless]", "[interactive]"):
                continue
            inner_match = _BRACKETED_TOKEN_RE.match(first_token)
            if inner_match:
                raise ModeAnnotationError(
                    f"unknown mode annotation in mode position: {first_token!r} "
                    f"(expected `[headless]` or `[interactive]`)"
                )

    if annotations:
        mode: Mode = "headless" if annotations[0] == "[headless]" else "interactive"
        body = payload
        # Strip the annotation from the body (anywhere it appears)
        body = body.replace(annotations[0] + " ", "", 1).replace(" " + annotations[0], "", 1)
        return (mode, True, body.strip())
    return ("interactive", False, payload)


def resolve_tasks_md(tasks_md_text: str) -> list[ResolvedTask]:
    """Walk *tasks_md_text* and resolve mode for every task line.

    Returns a list of ``ResolvedTask`` in document order. Raises
    ``ModeAnnotationError`` on the FIRST offending line so the user sees
    a precise error.
    """
    out: list[ResolvedTask] = []
    for line_idx, line in enumerate(tasks_md_text.splitlines(), start=1):
        if not _TASK_LINE_RE.match(line):
            continue
        try:
            mode, has_explicit, body = resolve_task_mode(line)
        except ModeAnnotationError as exc:
            raise ModeAnnotationError(f"line {line_idx}: {exc}") from None
        out.append(
            ResolvedTask(
                line_number=line_idx,
                raw_line=line,
                mode=mode,
                has_explicit_annotation=has_explicit,
                body=body,
            )
        )
    return out


@dataclass(frozen=True)
class ModeSummary:
    """Aggregate counts across a tasks.md."""

    headless: int = 0
    interactive: int = 0
    explicit_count: int = 0
    default_count: int = 0  # tasks that defaulted to interactive (no annotation)

    @classmethod
    def from_resolved(cls, tasks: list[ResolvedTask]) -> ModeSummary:
        h = sum(1 for t in tasks if t.mode == "headless")
        i = sum(1 for t in tasks if t.mode == "interactive")
        explicit = sum(1 for t in tasks if t.has_explicit_annotation)
        default = sum(1 for t in tasks if not t.has_explicit_annotation)
        return cls(headless=h, interactive=i, explicit_count=explicit, default_count=default)


def scan_tasks_md(tasks_md_path: Path) -> tuple[list[ResolvedTask], ModeSummary] | None:
    """Read and resolve a tasks.md file. Returns ``None`` if file missing."""
    if not tasks_md_path.is_file():
        return None
    text = tasks_md_path.read_text(encoding="utf-8")
    tasks = resolve_tasks_md(text)
    return (tasks, ModeSummary.from_resolved(tasks))


__all__ = [
    "Mode",
    "ModeAnnotationError",
    "ResolvedTask",
    "ModeSummary",
    "resolve_task_mode",
    "resolve_tasks_md",
    "scan_tasks_md",
]
