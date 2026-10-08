"""Two defects deploy-agent root-caused via the permanent `.agents/current-agent`
ERROR on otaman-dev (20261008T113746).

1. `check_retired_identity_marker` had ONE remedy — "find the writer" — and a
   marker committed to the otaman repo has no writer, so the remedy sent the
   operator hunting a hook that does not exist while the ERROR returned on
   every pull.
2. `scan`'s runtime-exclusion `.gitignore` was write-once, so a program that
   already had a `.gitignore` never received the five exclusion lines. That is
   how the marker got committed in the first place.

Behavioural tests throughout — the pre-existing guard for this check is an
`inspect.getsource` grep, which proves the string is present and nothing about
what the check decides.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from otaman_cli import doctor
from otaman_cli.commands.scan import (
    _RUNTIME_EXCLUSIONS,
    _gitignore_missing_exclusions,
    ensure_runtime_exclusions,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def meta(tmp_path: Path) -> Path:
    """A git-inited otaman folder. Nested under parent/ — tmp_path itself is
    never git-inited, because pytest gives sibling tests a shared parent."""
    m = tmp_path / "parent" / "meta"
    (m / ".agents").mkdir(parents=True)
    _git("init", "-q", cwd=m)
    return m


# ---------------------------------------------------------------------------
# 1. doctor: tracked marker vs live writer


def test_untracked_marker_still_tells_you_to_find_the_writer(meta: Path):
    (meta / ".agents" / "current-agent").write_text("fswatch-agent\n", encoding="utf-8")

    result = doctor.check_retired_identity_marker(meta)

    assert result["status"] == "fail"
    assert result["details"]["tracked"] is False
    issue = result["issues"][0]
    assert "WROTE it" in issue["message"]
    assert "find the writer" in issue["fix"]


def test_tracked_marker_names_git_as_the_cause_not_a_writer(meta: Path):
    """The whole defect: a committed marker has no writer to hunt."""
    marker = meta / ".agents" / "current-agent"
    marker.write_text("romans\n", encoding="utf-8")
    _git("add", ".agents/current-agent", cwd=meta)
    _git("commit", "-q", "-m", "collateral", cwd=meta)

    result = doctor.check_retired_identity_marker(meta)

    assert result["status"] == "fail", "severity is unchanged — this stays a fail"
    assert result["details"]["tracked"] is True
    issue = result["issues"][0]
    assert "COMMITTED" in issue["message"]
    assert "checkout restores it" in issue["message"]
    assert "NO writer to hunt" in issue["fix"]
    assert "git rm --cached" in issue["fix"]
    assert "find the writer" not in issue["fix"], (
        "the find-the-writer remedy is what sent operators hunting a nonexistent hook"
    )


def test_tracked_marker_reports_its_content(meta: Path):
    marker = meta / ".agents" / "current-agent"
    marker.write_text("romans\n", encoding="utf-8")
    _git("add", ".agents/current-agent", cwd=meta)
    _git("commit", "-q", "-m", "collateral", cwd=meta)

    result = doctor.check_retired_identity_marker(meta)
    assert result["details"]["contains"] == "romans"
    assert "'romans'" in result["issues"][0]["message"]


def test_absent_marker_is_ok_and_says_nothing_about_tracking(meta: Path):
    result = doctor.check_retired_identity_marker(meta)
    assert result["status"] == "ok"
    assert not result.get("issues")


def test_marker_outside_a_git_repo_routes_to_the_writer_remedy(tmp_path: Path):
    """No git at all → tracked-ness is unknowable; the writer remedy is the
    safe default because it starts with 'delete it'."""
    root = tmp_path / "nogit"
    (root / ".agents").mkdir(parents=True)
    (root / ".agents" / "current-agent").write_text("someone\n", encoding="utf-8")

    result = doctor.check_retired_identity_marker(root)
    assert result["status"] == "fail"
    assert result["details"]["tracked"] is False
    assert "find the writer" in result["issues"][0]["fix"]


def test_consequence_is_stated_conditionally_not_as_present_fact(meta: Path):
    """A finding that overstates its impact gets discounted. On a host whose
    hook resolves via `otaman whoami --resolve-only`, nothing reads this file."""
    (meta / ".agents" / "current-agent").write_text("x\n", encoding="utf-8")
    message = doctor.check_retired_identity_marker(meta)["issues"][0]["message"]
    assert "any tenant whose hook still reads it" in message


# ---------------------------------------------------------------------------
# 2. scan: runtime exclusions are append-if-absent, not write-once


def test_gitignore_missing_exclusions_reports_only_the_absent_ones(tmp_path: Path):
    gi = tmp_path / ".gitignore"
    gi.write_text(".agents/bus/\n.agents/queue/\n", encoding="utf-8")
    assert _gitignore_missing_exclusions(gi) == [
        ".agents/blocked/",
        ".agents/sessions/",
        ".agents/current-agent",
    ]


def test_trailing_slash_difference_is_not_a_missing_line(tmp_path: Path):
    """A hand-written `.agents/bus` covers `.agents/bus/` — appending a
    near-duplicate would be noise, not protection."""
    gi = tmp_path / ".gitignore"
    gi.write_text(".agents/bus\n", encoding="utf-8")
    assert ".agents/bus/" not in _gitignore_missing_exclusions(gi)


def test_commented_out_line_does_not_count_as_covered(tmp_path: Path):
    gi = tmp_path / ".gitignore"
    gi.write_text("# .agents/bus/\n", encoding="utf-8")
    assert ".agents/bus/" in _gitignore_missing_exclusions(gi)


def test_existing_gitignore_gains_the_missing_lines(meta: Path):
    """THE defect: otaman-meta's .gitignore had only three `.otaman/*` secret
    lines, so the write-once guard skipped it and all five paths stayed
    trackable."""
    gi = meta / ".gitignore"
    gi.write_text("# Maestro runtime state\n.otaman/secrets.env\n", encoding="utf-8")

    ensure_runtime_exclusions(meta)

    body = gi.read_text(encoding="utf-8")
    for line in _RUNTIME_EXCLUSIONS:
        assert line in body, f"{line} was not appended"


def test_append_preserves_hand_written_rules_and_comments(meta: Path):
    gi = meta / ".gitignore"
    original = "# Maestro runtime state\n.otaman/secrets.env\n.otaman/afk\n"
    gi.write_text(original, encoding="utf-8")

    ensure_runtime_exclusions(meta)

    body = gi.read_text(encoding="utf-8")
    assert body.startswith(original), "the file must be appended to, never rewritten"


def test_running_twice_appends_nothing_the_second_time(meta: Path):
    gi = meta / ".gitignore"
    gi.write_text(".otaman/secrets.env\n", encoding="utf-8")

    ensure_runtime_exclusions(meta)
    once = gi.read_text(encoding="utf-8")
    ensure_runtime_exclusions(meta)

    assert gi.read_text(encoding="utf-8") == once, "idempotent — no duplicate lines"


def test_absent_gitignore_is_created_with_every_exclusion(meta: Path):
    ensure_runtime_exclusions(meta)
    body = (meta / ".gitignore").read_text(encoding="utf-8")
    for line in _RUNTIME_EXCLUSIONS:
        assert line in body


def test_a_newer_exclusion_line_reaches_an_old_complete_gitignore(meta: Path):
    """The second half of write-once: a line added to the list later never
    reached a program whose .gitignore already had the earlier four."""
    gi = meta / ".gitignore"
    gi.write_text("".join(f"{x}\n" for x in _RUNTIME_EXCLUSIONS[:-1]), encoding="utf-8")

    ensure_runtime_exclusions(meta)

    assert _RUNTIME_EXCLUSIONS[-1] in gi.read_text(encoding="utf-8")


def test_already_tracked_paths_are_reported_and_never_untracked(meta: Path, capsys):
    """Report-and-stop. On otaman-dev the 7,936 committed bus files are the
    only durable copy of the bus — untracking would destroy it."""
    bus = meta / ".agents" / "bus"
    bus.mkdir(parents=True)
    (bus / "msg.md").write_text("x\n", encoding="utf-8")
    _git("add", ".agents/bus/msg.md", cwd=meta)
    _git("commit", "-q", "-m", "bus", cwd=meta)

    ensure_runtime_exclusions(meta)
    out = capsys.readouterr().out

    assert "already TRACKED" in out
    assert ".agents/bus: 1 tracked file(s)" in out
    assert "NOT done for you" in out
    # The file is still tracked — the helper reported, it did not act.
    assert (
        subprocess.run(
            ["git", "ls-files", "--", ".agents/bus"],
            cwd=str(meta),
            capture_output=True,
            text=True,
        ).stdout.strip()
        == ".agents/bus/msg.md"
    )


def test_dry_run_reports_without_writing(meta: Path, capsys):
    gi = meta / ".gitignore"
    gi.write_text(".otaman/secrets.env\n", encoding="utf-8")

    ensure_runtime_exclusions(meta, dry_run=True)

    assert gi.read_text(encoding="utf-8") == ".otaman/secrets.env\n", "dry-run wrote to the file"
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "would append 5 missing exclusion(s)" in out


def test_dry_run_on_a_complete_gitignore_says_so(meta: Path, capsys):
    """no-silent-success: 'already excludes everything' and 'did not check'
    are different states and must read differently."""
    gi = meta / ".gitignore"
    gi.write_text("".join(f"{x}\n" for x in _RUNTIME_EXCLUSIONS), encoding="utf-8")

    ensure_runtime_exclusions(meta, dry_run=True)

    assert "already excludes every runtime path" in capsys.readouterr().out
