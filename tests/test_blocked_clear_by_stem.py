"""Tests for auto-clear-blocked-entries task 2.1, 2.2, 2.3.

`otaman blocked clear <proposal-stem>` tombstones blocked entries across
all agent files by Proposal-stem match.  Idempotent: no-match exits 0 and
already-tombstoned entries are not double-tombstoned.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from otaman_cli.commands.blocked import _cmd_blocked_clear_by_stem


# ---------------------------------------------------------------- helpers
def _setup_root(tmp_path: Path) -> Path:
    (tmp_path / ".agents" / "blocked").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    return tmp_path


def _write_blocked(root: Path, agent: str, body: str) -> Path:
    p = root / ".agents" / "blocked" / f"{agent}.md"
    p.write_text(body, encoding="utf-8")
    return p


def _entry(title: str, stem: str, change: str = "") -> str:
    """Build a `## Blocked:` entry matching the schema used by /otaman:propose."""
    lines = [
        f"## Blocked: {title}",
        f"- **Proposal**: {stem}",
        "- **Blocked since**: 2026-06-10T10:00:00Z",
    ]
    if change:
        lines.append(f"- **Change**: {change}")
    lines.append("- **Depends on**: spec-change-approved + spec-change notification")
    lines.append("")
    return "\n".join(lines) + "\n"


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": "cli-agent",
        "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        "NO_COLOR": "1",
    }
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------- task 2.1
class TestClearByStem:
    def test_clear_tombstones_matching_entry(self, tmp_path: Path):
        root = _setup_root(tmp_path)
        stem = "20260610T100000-cli-agent-to-human-spec-change-request-feat-x"
        bf = _write_blocked(root, "cli-agent", _entry("Add feature X", stem))

        rc = _cmd_blocked_clear_by_stem(root, stem)
        assert rc == 0
        text = bf.read_text(encoding="utf-8")
        # Entry now wrapped in HTML comment
        assert "<!-- ## Blocked: Add feature X" in text
        assert "cleared 2026-" in text
        assert "manually-cleared -->" in text

    def test_clear_scans_all_agent_files(self, tmp_path: Path):
        """Stem present in another agent's file — should still tombstone."""
        root = _setup_root(tmp_path)
        stem = "20260610T100000-runner-agent-to-human-spec-change-request-feat-y"
        bf_self = _write_blocked(root, "cli-agent", _entry("Some other task", "different-stem"))
        bf_other = _write_blocked(root, "runner-agent", _entry("Add feature Y", stem))

        rc = _cmd_blocked_clear_by_stem(root, stem)
        assert rc == 0
        # cli-agent's file untouched
        assert "<!--" not in bf_self.read_text(encoding="utf-8")
        # runner-agent's file tombstoned
        assert "<!-- ## Blocked: Add feature Y" in bf_other.read_text(encoding="utf-8")

    def test_clear_prints_agent_and_title_on_success(self, tmp_path: Path, capsys):
        root = _setup_root(tmp_path)
        stem = "20260610T100000-cli-agent-to-human-spec-change-request-feat-z"
        _write_blocked(root, "cli-agent", _entry("Add feature Z", stem))

        _cmd_blocked_clear_by_stem(root, stem)
        captured = capsys.readouterr().out
        assert "Cleared" in captured
        assert "cli-agent" in captured
        assert "Add feature Z" in captured


# ---------------------------------------------------------------- task 2.2
class TestNoMatchIdempotent:
    def test_no_match_exits_zero_with_message(self, tmp_path: Path, capsys):
        root = _setup_root(tmp_path)
        _write_blocked(root, "cli-agent", _entry("Some task", "different-stem"))

        rc = _cmd_blocked_clear_by_stem(root, "stem-that-matches-nothing")
        assert rc == 0
        out = capsys.readouterr().out
        assert "No blocked entry found for stem" in out
        assert "stem-that-matches-nothing" in out

    def test_no_blocked_dir_exits_zero_with_message(self, tmp_path: Path, capsys):
        # platform.yaml exists but .agents/blocked is absent
        (tmp_path / ".agents").mkdir()
        (tmp_path / "platform.yaml").write_text(
            "project: x\nversion: '1.0'\nrepos:\n  - {name: r, path: ., owner: a-agent}\n",
            encoding="utf-8",
        )
        rc = _cmd_blocked_clear_by_stem(tmp_path, "anything")
        assert rc == 0
        assert "No blocked entry" in capsys.readouterr().out

    def test_empty_stem_rejected(self, tmp_path: Path):
        root = _setup_root(tmp_path)
        rc = _cmd_blocked_clear_by_stem(root, "")
        assert rc == 1


# ---------------------------------------------------------------- task 2.3
class TestNoDoubleTombstone:
    def test_already_tombstoned_entry_skipped(self, tmp_path: Path):
        """Entries already wrapped in <!-- ... --> are skipped by the
        line-leading `^## Blocked:` regex — second clear is a no-op."""
        root = _setup_root(tmp_path)
        stem = "20260610T100000-cli-agent-to-human-spec-change-request-feat-w"
        bf = _write_blocked(root, "cli-agent", _entry("Already done", stem))

        # First clear: tombstones the entry
        rc1 = _cmd_blocked_clear_by_stem(root, stem)
        assert rc1 == 0
        text1 = bf.read_text(encoding="utf-8")
        first_clear_count = text1.count("cleared 2026-")
        assert first_clear_count == 1

        # Second clear: must be no-op (no double-wrap, no nested cleared lines)
        rc2 = _cmd_blocked_clear_by_stem(root, stem)
        assert rc2 == 0
        text2 = bf.read_text(encoding="utf-8")
        # Still exactly one `cleared` trailer
        assert text2.count("cleared 2026-") == 1
        # No double comment wrapping
        assert text2.count("<!--") == 1
        assert text2.count("-->") == 1


# ------------------------------------------------------------ CLI subprocess (task 2.1 end-to-end)
class TestClearByStemCli:
    def test_cli_clear_subcommand_dispatches(self, tmp_path: Path):
        root = _setup_root(tmp_path)
        stem = "20260610T100000-cli-agent-to-human-spec-change-request-cli-e2e"
        bf = _write_blocked(root, "cli-agent", _entry("CLI e2e task", stem))

        r = _run_cli(root, "blocked", "clear", stem)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "Cleared" in r.stdout and stem not in bf.read_text(encoding="utf-8").split("<!--")[0]
        # Tombstone visible
        assert "manually-cleared -->" in bf.read_text(encoding="utf-8")

    def test_cli_clear_no_match_prints_message(self, tmp_path: Path):
        root = _setup_root(tmp_path)
        r = _run_cli(root, "blocked", "clear", "nothing-matches-this")
        assert r.returncode == 0
        assert "No blocked entry found" in r.stdout

    def test_cli_legacy_clear_slug_still_works(self, tmp_path: Path):
        """`otaman blocked --clear <slug>` (legacy) still works — backward compat."""
        root = _setup_root(tmp_path)
        _write_blocked(root, "cli-agent", _entry("Legacy task", "some-stem"))
        r = _run_cli(root, "blocked", "--clear", "Legacy task")
        # Legacy --clear is regex match against slug (entry title); should succeed
        assert r.returncode == 0
