"""Tests for `@solution:<id>` annotation parser (task 4.2).

Covers:
- parse_solution_annotations: extracts from task lines, ignores non-task lines
- Multiple @solution: per task supported
- scan_tasks_md cross-validates against solutions.yaml
- cmd_assign integration surfaces findings in output
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.registries.assign_annotations import (
    load_solution_ids_from_yaml,
    parse_solution_annotations,
    resolve_tasks_md_path,
    scan_tasks_md,
)
from otaman_cli.registries.loader import yaml_dump

# ---------------------------------------------------------------------------
# parse_solution_annotations


def test_parse_extracts_single_annotation():
    text = """\
# Tasks

- [ ] 1.1 @otaman-cli @solution:SOL-1-foo Implement X
- [ ] 1.2 @otaman-cli Implement Y
"""
    out = parse_solution_annotations(text)
    assert len(out) == 1
    assert out[0].solution_id == "SOL-1-foo"
    assert out[0].line_number == 3


def test_parse_ignores_non_task_lines():
    text = """\
# Tasks
> See @solution:SOL-99-fake — this is prose, not a task line.

- [ ] 1.1 @solution:SOL-1-foo Real annotation
"""
    out = parse_solution_annotations(text)
    assert len(out) == 1
    assert out[0].solution_id == "SOL-1-foo"


def test_parse_extracts_multiple_annotations_per_task():
    text = """\
- [ ] 1.1 @solution:SOL-1-foo @solution:SOL-2-bar Implements both
"""
    out = parse_solution_annotations(text)
    assert len(out) == 2
    assert {a.solution_id for a in out} == {"SOL-1-foo", "SOL-2-bar"}


def test_parse_handles_checked_tasks():
    text = "- [x] 1.1 @solution:SOL-1-done Already done\n"
    out = parse_solution_annotations(text)
    assert len(out) == 1
    assert out[0].solution_id == "SOL-1-done"


def test_parse_rejects_malformed_solution_ids():
    """Annotation must follow SOL-N-slug regex."""
    text = """\
- [ ] 1.1 @solution:not-a-sol-id ignore me
- [ ] 1.2 @solution:SOL-7-good keep me
"""
    out = parse_solution_annotations(text)
    assert len(out) == 1
    assert out[0].solution_id == "SOL-7-good"


def test_parse_handles_empty_tasks_md():
    assert parse_solution_annotations("") == []


def test_parse_no_annotations_in_repo_only_task():
    text = "- [ ] 1.1 @otaman-cli Just a repo annotation\n"
    assert parse_solution_annotations(text) == []


# ---------------------------------------------------------------------------
# load_solution_ids_from_yaml


def test_load_solution_ids_from_yaml(tmp_path):
    p = tmp_path / "solutions.yaml"
    yaml_dump(
        {
            "solutions": [
                {"id": "SOL-1-a", "description": "x"},
                {"id": "SOL-2-b", "description": "y"},
            ],
        },
        p,
    )
    assert load_solution_ids_from_yaml(p) == {"SOL-1-a", "SOL-2-b"}


def test_load_solution_ids_returns_empty_for_missing_file(tmp_path):
    assert load_solution_ids_from_yaml(tmp_path / "missing.yaml") == set()


def test_load_solution_ids_returns_empty_for_empty_yaml(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("solutions: []\n")
    assert load_solution_ids_from_yaml(p) == set()


# ---------------------------------------------------------------------------
# scan_tasks_md (cross-file validation)


@pytest.fixture
def project_with_solutions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up: meta + biz with solutions.yaml + a change with tasks.md."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    biz = parent / "biz"
    biz.mkdir()
    (meta / "platform.yaml").write_text(
        "project: testprog\nrepos:\n  - name: biz\n    path: ../biz\n    owner: cpo-agent\n",
        encoding="utf-8",
    )
    yaml_dump(
        {
            "solutions": [
                {"id": "SOL-1-real", "description": "exists"},
                {"id": "SOL-2-real", "description": "exists"},
            ],
        },
        biz / "solutions.yaml",
    )

    # A separate "specs" subtree under meta with tasks.md
    tasks_dir = meta / "changes" / "my-feature"
    tasks_dir.mkdir(parents=True)
    return meta


def test_scan_reports_valid_and_missing(project_with_solutions: Path) -> None:
    meta = project_with_solutions
    tasks_md = meta / "changes" / "my-feature" / "tasks.md"
    tasks_md.write_text(
        """\
# Tasks
- [ ] 1.1 @solution:SOL-1-real First task
- [ ] 1.2 @solution:SOL-2-real Second task
- [ ] 1.3 @solution:SOL-99-ghost Bogus
""",
        encoding="utf-8",
    )

    findings = scan_tasks_md(tasks_md, meta)
    assert findings.total == 3
    assert findings.valid_ids == ["SOL-1-real", "SOL-2-real"]
    assert findings.missing_ids == ["SOL-99-ghost"]
    assert findings.solutions_yaml_path is not None


def test_scan_with_no_annotations(project_with_solutions: Path) -> None:
    meta = project_with_solutions
    tasks_md = meta / "changes" / "my-feature" / "tasks.md"
    tasks_md.write_text("- [ ] 1.1 @otaman-cli No solution annotation\n", encoding="utf-8")
    findings = scan_tasks_md(tasks_md, meta)
    assert findings.total == 0
    assert findings.has_findings is False


def test_scan_returns_empty_findings_for_missing_tasks_md(tmp_path):
    findings = scan_tasks_md(tmp_path / "nope.md", tmp_path)
    assert findings.total == 0


def test_scan_treats_all_as_unknown_when_no_solutions_yaml(tmp_path):
    """If solutions.yaml can't be located, no validation possible."""
    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text("- [ ] @solution:SOL-1-x foo\n")
    findings = scan_tasks_md(tasks_md, None)
    assert findings.total == 1
    assert findings.valid_ids == []
    assert findings.missing_ids == ["SOL-1-x"]


# ---------------------------------------------------------------------------
# resolve_tasks_md_path


def test_resolve_accepts_tasks_md_file(tmp_path):
    p = tmp_path / "tasks.md"
    p.write_text("# Tasks")
    assert resolve_tasks_md_path(str(p)) == p


def test_resolve_accepts_change_directory(tmp_path):
    d = tmp_path / "my-feature"
    d.mkdir()
    (d / "tasks.md").write_text("# Tasks")
    assert resolve_tasks_md_path(str(d)) == d / "tasks.md"


def test_resolve_returns_none_for_unknown_path(tmp_path):
    assert resolve_tasks_md_path(str(tmp_path / "nope")) is None


# ---------------------------------------------------------------------------
# Multi-annotation interaction with existing @<repo> annotations (regression)


def test_parse_coexists_with_repo_annotation():
    """Both @<repo> and @solution: can appear on the same line; only the
    solution kind is extracted by this parser (the @<repo> kind is handled
    elsewhere in otaman_plugin.map_tasks)."""
    text = "- [ ] 1.1 @otaman-cli @solution:SOL-1-x do the thing\n"
    out = parse_solution_annotations(text)
    assert [a.solution_id for a in out] == ["SOL-1-x"]


def test_parse_skips_solution_annotation_outside_task_line():
    """Bullet items without [ ] checkbox shape are NOT task lines."""
    text = """\
- This is a bullet, not a task: @solution:SOL-1-x
- [ ] 1.1 @solution:SOL-2-y actual task
"""
    out = parse_solution_annotations(text)
    assert [a.solution_id for a in out] == ["SOL-2-y"]
