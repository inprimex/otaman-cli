"""A purge must say how much of what it destroys exists NOWHERE ELSE.

The line this replaces was "Unrecoverable unless this directory is version-controlled"
— true, and a condition the operator cannot evaluate at the moment of deciding, while
the run can. On the tenant that lost 591 messages the condition was FALSE (the bus had
not been committed in six weeks) and nothing said so. The forensics of 2026-10-02 then
measured 2,627 of 5,317 active messages untracked and the archive 0% tracked, which is
the finding that survived every retraction that day.

So the purge now MEASURES it: three states, never collapsed — all committed, some/all
absent from HEAD, and git could not be consulted. The third reads as unrecoverable,
because "I could not check" has the same consequence as "no backup" and the opposite
one from "all committed".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from otaman_cli import cleanup_bus as CB
from otaman_cli.commands import cleanup as CC


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo NESTED inside tmp_path, deliberately — never tmp_path itself.

    `tmp_path` directories are siblings under one shared `pytest-of-<user>/pytest-N/`
    parent, and `otaman init`'s preflight scans the PARENT of its cwd for git repos.
    Initialising a repo at `tmp_path` therefore made every later init test see "Found 6
    git repos in parent directory" and take the scan branch — three failures in the
    full suite that pass in isolation. One level down is invisible to that scan.
    """
    root = tmp_path / "bus-repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    return root


def _month(root: Path, name: str, n: int) -> Path:
    d = root / "archive" / name
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"msg-{i}.md").write_text(f"---\nid: m{i}\n---\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# the measurement


def test_a_committed_month_reads_as_fully_backed(repo):
    _month(repo, "2026-01", 3)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "bus", cwd=repo)

    assert CB.unbacked_count(repo / "archive" / "2026-01") == 0


def test_an_uncommitted_month_counts_every_file_as_unbacked(repo):
    _month(repo, "2026-02", 4)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "empty-ish", cwd=repo)
    # four MORE files, never committed — the shape of a bus with no commit for weeks
    for i in range(4, 8):
        (repo / "archive" / "2026-02" / f"msg-{i}.md").write_text("---\nid: x\n---\n", "utf-8")

    assert CB.unbacked_count(repo / "archive" / "2026-02") == 4


def test_a_directory_outside_any_repo_is_UNKNOWN_not_zero(tmp_path):
    """The distinction the old wording lost: unknown must never read as safe."""
    d = _month(tmp_path, "2026-03", 2)

    assert CB.unbacked_count(d) is None, "no repo must be unknown, not 'all backed'"
    assert CB.head_tracked_names(d) is None


def test_a_repo_with_no_commits_is_unknown(repo):
    """`git ls-tree HEAD` fails before the first commit — that is unknown, not clean."""
    d = _month(repo, "2026-04", 2)

    assert CB.unbacked_count(d) is None


# ---------------------------------------------------------------------------
# what the operator is told


def test_the_stake_line_names_the_unbacked_count(capsys):
    CC._render_backup_stake(
        {"deleted_unbacked": 1109, "deleted_detail": [{"month": "2026-07", "unbacked": 1109}]}
    )

    out = capsys.readouterr()
    text = out.out + out.err
    assert "1109" in text
    assert "NOT in git HEAD" in text
    assert "nothing to restore them from" in text


def test_the_stake_line_says_restorable_when_everything_is_committed(capsys):
    CC._render_backup_stake(
        {"deleted_unbacked": 0, "deleted_detail": [{"month": "2026-07", "unbacked": 0}]}
    )

    text = "".join(capsys.readouterr())
    assert "committed to git HEAD" in text and "restorable" in text
    assert "NOT in git" not in text


def test_an_unknown_month_is_reported_as_unrecoverable(capsys):
    CC._render_backup_stake(
        {"deleted_unbacked": 0, "deleted_detail": [{"month": "2026-07", "unbacked": None}]}
    )

    text = "".join(capsys.readouterr())
    assert "git could not be consulted" in text
    assert "unrecoverable" in text
    assert "restorable" not in text, "unknown must not be rendered as the safe case"


def test_nothing_is_claimed_when_no_month_is_being_purged(capsys):
    CC._render_backup_stake({"deleted_unbacked": 0, "deleted_detail": []})

    assert "".join(capsys.readouterr()).strip() == ""


# ---------------------------------------------------------------------------
# end to end, through the real cleanup


def _bus(root: Path) -> None:
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", "utf-8")
    (root / ".agents" / "agents.yaml").write_text(
        "agents:\n  - name: cli-agent\n    role: developer\n", "utf-8"
    )


def _old_month(root: Path, month: str, n: int) -> Path:
    d = root / ".agents" / "bus" / "archive" / month
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"old{i}.md").write_text("---\nid: x\n---\n", encoding="utf-8")
    return d


def test_the_withheld_report_carries_the_stake_so_the_operator_can_decide(tmp_path):
    """`--purge` is the decision this number exists to inform, so it must be in the
    WITHHELD report — not only in the output of the run that already destroyed them."""
    root = tmp_path / "p"
    _bus(root)
    _old_month(root, "2026-01", 3)

    report = CB.cleanup(root, dry_run=False)

    assert report["purge_withheld"] is True
    assert report["deleted_message_count"] == 3
    # not a git repo at all → unknown, which must NOT read as zero-unbacked
    assert report["deleted_detail"] == [{"month": "2026-01", "messages": 3, "unbacked": None}]
    assert report["deleted_unbacked"] == 0
    assert "git could not be consulted" in report["deleted"][0]


def test_an_uncommitted_month_is_counted_in_the_report(tmp_path):
    root = tmp_path / "p"
    _bus(root)
    _old_month(root, "2026-01", 3)
    _git("init", "-q", "-b", "main", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "init", cwd=root)
    # two messages that arrived after the last commit — the live shape
    for i in range(3, 5):
        (root / ".agents" / "bus" / "archive" / "2026-01" / f"old{i}.md").write_text(
            "---\nid: y\n---\n", encoding="utf-8"
        )

    report = CB.cleanup(root, dry_run=False)

    assert report["deleted_unbacked"] == 2, report["deleted_detail"]
    assert "2 with NO git backup" in report["deleted"][0]


def test_the_dry_run_preview_states_the_same_stake_as_the_real_run(tmp_path):
    """A preview that understates what would be destroyed is how the first incident
    was read as safe. Same month, same number, dry or not."""
    root = tmp_path / "p"
    _bus(root)
    _old_month(root, "2026-01", 3)
    _git("init", "-q", "-b", "main", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "init", cwd=root)
    (root / ".agents" / "bus" / "archive" / "2026-01" / "fresh.md").write_text(
        "---\nid: z\n---\n", encoding="utf-8"
    )

    preview = CB.cleanup(root, dry_run=True)
    real = CB.cleanup(root, dry_run=False)

    assert preview["deleted_unbacked"] == real["deleted_unbacked"] == 1
    assert preview["deleted_detail"] == real["deleted_detail"]
