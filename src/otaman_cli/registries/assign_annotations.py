"""`@solution:<id>` annotation parser for ``otaman assign`` (task 4.2).

The existing task-assignment parser in ``otaman_plugin.map_tasks`` handles
``@<repo-name>`` and ``[headless]`` annotations. This module is a
complementary, self-contained parser that extracts ``@solution:<id>``
annotations from tasks.md task lines and validates them against
``solutions.yaml``.

Annotation grammar (unified with parser-extension-design-note.md):
    @solution:SOL-N-slug

May appear anywhere in a task line, alongside other annotations. The
parser only extracts the solution annotation kind; other annotations are
ignored (they're parsed by ``otaman_plugin.map_tasks``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from otaman_cli.registries.loader import resolve_registry_path, yaml_load

# Matches @solution:SOL-N-slug — solution id must follow the Appendix B.3 regex.
_SOLUTION_ANNOTATION_RE = re.compile(r"@solution:(SOL-\d+-[a-z0-9-]+)")

# Detect task lines: "- [ ] foo" or "- [x] foo" (Markdown task list format).
_TASK_LINE_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+")


@dataclass(frozen=True)
class SolutionAnnotation:
    """One @solution:<id> annotation found in tasks.md."""

    solution_id: str
    line_number: int  # 1-based
    task_text: str  # the full task line (without the - [ ] prefix)


@dataclass(frozen=True)
class AnnotationFindings:
    """Result of scanning a tasks.md file for @solution: annotations."""

    annotations: list[SolutionAnnotation]
    valid_ids: list[str]  # ids that exist in solutions.yaml
    missing_ids: list[str]  # ids referenced but not in solutions.yaml
    solutions_yaml_path: Path | None

    @property
    def total(self) -> int:
        return len(self.annotations)

    @property
    def has_findings(self) -> bool:
        return self.total > 0


def parse_solution_annotations(tasks_md_text: str) -> list[SolutionAnnotation]:
    """Extract @solution:<id> annotations from *tasks_md_text*.

    Returns a list in document order. One task may carry multiple
    @solution: annotations (rare but supported); they're returned as
    separate entries.
    """
    out: list[SolutionAnnotation] = []
    for line_idx, line in enumerate(tasks_md_text.splitlines(), start=1):
        task_match = _TASK_LINE_RE.match(line)
        if not task_match:
            continue
        task_text = line[task_match.end() :].rstrip()
        for m in _SOLUTION_ANNOTATION_RE.finditer(line):
            out.append(
                SolutionAnnotation(
                    solution_id=m.group(1),
                    line_number=line_idx,
                    task_text=task_text,
                )
            )
    return out


def load_solution_ids_from_yaml(solutions_yaml_path: Path) -> set[str]:
    """Read ``solutions.yaml`` and return the set of declared solution ids.

    Returns an empty set when the file is missing — callers can decide
    whether that's an error.
    """
    raw = yaml_load(solutions_yaml_path)
    if not isinstance(raw, dict):
        return set()
    sols = raw.get("solutions") or []
    return {s["id"] for s in sols if isinstance(s, dict) and "id" in s}


def scan_tasks_md(tasks_md_path: Path, project_root: Path | None) -> AnnotationFindings:
    """Extract @solution: annotations and validate against solutions.yaml.

    *project_root* is the otaman-meta directory (used to resolve
    solutions.yaml location). If ``None`` or solutions.yaml can't be
    located, validation is skipped (all ids treated as valid).
    """
    if not tasks_md_path.is_file():
        return AnnotationFindings(
            annotations=[],
            valid_ids=[],
            missing_ids=[],
            solutions_yaml_path=None,
        )

    text = tasks_md_path.read_text(encoding="utf-8")
    annotations = parse_solution_annotations(text)

    sol_path: Path | None = None
    known_ids: set[str] = set()
    if project_root is not None:
        sol_path = resolve_registry_path(project_root, "solutions")
        if sol_path is not None and sol_path.is_file():
            known_ids = load_solution_ids_from_yaml(sol_path)

    referenced = {a.solution_id for a in annotations}
    if known_ids:
        valid = sorted(referenced & known_ids)
        missing = sorted(referenced - known_ids)
    else:
        # Cannot validate — treat all as unknown
        valid = []
        missing = sorted(referenced)

    return AnnotationFindings(
        annotations=annotations,
        valid_ids=valid,
        missing_ids=missing,
        solutions_yaml_path=sol_path,
    )


def resolve_tasks_md_path(target: str) -> Path | None:
    """Resolve a user-provided *target* into the actual tasks.md path.

    *target* may be:
        - The tasks.md file itself
        - The change directory (we look for tasks.md inside)
    """
    p = Path(target)
    if p.is_file() and p.name == "tasks.md":
        return p
    if p.is_dir():
        cand = p / "tasks.md"
        if cand.is_file():
            return cand
    return None


__all__ = [
    "SolutionAnnotation",
    "AnnotationFindings",
    "parse_solution_annotations",
    "load_solution_ids_from_yaml",
    "scan_tasks_md",
    "resolve_tasks_md_path",
]
