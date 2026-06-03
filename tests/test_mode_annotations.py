"""Tests for [headless]/[interactive] task-mode annotations (task 3.5).

Covers grammar from auto-session-spawn-on-bus-events/design.md Q2 Resolved
2026-05-21:
- Annotation appears after @otaman-<repo>, space-separated
- Default `[interactive]` when absent
- Conflict (both annotations) → error
- Unknown bracketed token in mode position → error
- Case-sensitive (no `[Headless]` etc.)
"""

from __future__ import annotations

import pytest

from otaman_cli.hitl.mode_annotations import (
    ModeAnnotationError,
    ModeSummary,
    ResolvedTask,
    resolve_task_mode,
    resolve_tasks_md,
)


# ---------------------------------------------------------------------------
# resolve_task_mode


def test_headless_explicit():
    mode, explicit, _body = resolve_task_mode("- [ ] 1.1 @otaman-bridge [headless] Rotate logs")
    assert mode == "headless"
    assert explicit is True


def test_interactive_explicit():
    mode, explicit, _ = resolve_task_mode("- [ ] 1.1 @otaman-cli [interactive] Review the help text")
    assert mode == "interactive"
    assert explicit is True


def test_default_to_interactive_when_missing():
    mode, explicit, _ = resolve_task_mode("- [ ] 1.1 @otaman-cli Do something")
    assert mode == "interactive"
    assert explicit is False


def test_checked_task_also_parsed():
    """`- [x] N.N` task lines parse the same as unchecked."""
    mode, _, _ = resolve_task_mode("- [x] 2.3 @otaman-bridge [headless] Already done")
    assert mode == "headless"


def test_non_task_line_returns_default():
    mode, explicit, _ = resolve_task_mode("Some prose, not a task line.")
    assert mode == "interactive"
    assert explicit is False


def test_conflict_two_annotations_raises():
    with pytest.raises(ModeAnnotationError, match="conflicting"):
        resolve_task_mode("- [ ] 1.1 @otaman-cli [headless] [interactive] mixed")


def test_conflict_two_same_annotations_raises():
    """Even two of the same kind is treated as a conflict."""
    with pytest.raises(ModeAnnotationError, match="conflicting"):
        resolve_task_mode("- [ ] 1.1 @otaman-cli [headless] [headless] x")


def test_unknown_bracketed_token_in_mode_position_raises():
    with pytest.raises(ModeAnnotationError, match="unknown mode annotation"):
        resolve_task_mode("- [ ] 1.1 @otaman-bridge [batch] Do it")


def test_case_mismatch_raises():
    with pytest.raises(ModeAnnotationError, match="lowercase"):
        resolve_task_mode("- [ ] 1.1 @otaman-cli [Headless] case-wrong")


def test_case_mismatch_interactive_raises():
    with pytest.raises(ModeAnnotationError, match="lowercase"):
        resolve_task_mode("- [ ] 1.1 @otaman-cli [INTERACTIVE] case-wrong")


def test_annotation_strips_from_body():
    _mode, _, body = resolve_task_mode("- [ ] 1.1 @otaman-bridge [headless] Rotate logs")
    assert "[headless]" not in body
    assert "Rotate logs" in body


def test_bracketed_body_marker_not_mode_position_is_fine():
    """`[B-15]` or similar bracketed references in the BODY (not the mode slot)
    should NOT trigger the unknown-mode error.
    """
    mode, explicit, _body = resolve_task_mode(
        "- [ ] 1.1 @otaman-cli Review the new --help output. *(B-15)*"
    )
    assert mode == "interactive"
    assert explicit is False


# ---------------------------------------------------------------------------
# resolve_tasks_md (multi-line)


def test_resolve_tasks_md_mixed():
    text = """\
# Tasks

- [ ] 1.1 @otaman-bridge [headless] Rotate logs
- [ ] 1.2 @otaman-cli [interactive] Review help
- [ ] 1.3 @otaman-cli Default interactive

Some prose between tasks.

- [x] 2.1 @otaman-runner [headless] Done
"""
    tasks = resolve_tasks_md(text)
    assert len(tasks) == 4
    assert [t.mode for t in tasks] == ["headless", "interactive", "interactive", "headless"]
    assert [t.has_explicit_annotation for t in tasks] == [True, True, False, True]


def test_resolve_tasks_md_error_includes_line_number():
    text = """\
# Tasks

- [ ] 1.1 @otaman-bridge [headless] OK
- [ ] 1.2 @otaman-cli [batch] BAD MODE
"""
    with pytest.raises(ModeAnnotationError, match="line 4"):
        resolve_tasks_md(text)


def test_resolve_tasks_md_empty():
    assert resolve_tasks_md("") == []


# ---------------------------------------------------------------------------
# ModeSummary


def test_summary_counts():
    tasks = [
        ResolvedTask(1, "x", "headless", True, "x"),
        ResolvedTask(2, "x", "interactive", True, "x"),
        ResolvedTask(3, "x", "interactive", False, "x"),  # defaulted
        ResolvedTask(4, "x", "headless", True, "x"),
    ]
    s = ModeSummary.from_resolved(tasks)
    assert s.headless == 2
    assert s.interactive == 2
    assert s.explicit_count == 3
    assert s.default_count == 1
