"""A registered repo that is not version-controlled must not read as healthy.

deploy-agent, 20260921T191148: four of five managed repos on haulops were plain
directories — no `.git` anywhere — and `otaman doctor` printed `[OK]` for every
one. `_check_repo_materialization` looked for `.otaman` and `CLAUDE.local.md`
only, and a directory carrying those two markers is indistinguishable from a
checkout.

It went unnoticed for three weeks *because* doctor was green, while an agent
wrote ~36K of research documents into one of them. The work survived; nothing
was protecting it.

Severity is FAIL rather than WARN, and that is a deliberate escalation: the
other materialization states describe a repo that is merely incomplete, and
`sync-repos` repairs them. This one means work dispatched into that path has no
history and no recovery. It is the state most worth interrupting a human for.

Two shapes must NOT be flagged, or the check trades a false green for a false
red:
  * `.git` as a FILE — worktrees and submodules spell it that way.
  * a repo nested inside a parent checkout — genuinely versioned, by the parent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from otaman_cli.commands.doctor import (
    _check_repo_materialization,
    _print_repo_materialization_report,
)


def _program(tmp_path: Path, repos_yaml: str) -> Path:
    root = tmp_path / "meta"
    root.mkdir()
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n" + repos_yaml, encoding="utf-8"
    )
    return root


def _markers(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".otaman").write_text("../meta\nagent: x\n", encoding="utf-8")
    (repo / "CLAUDE.local.md").write_text("rules\n", encoding="utf-8")
    return repo


def test_markers_without_git_is_a_fail_not_ok(tmp_path):
    """deploy's exact case: the two markers present, no version control."""
    root = _program(tmp_path, "  - name: sim\n    path: ../sim\n    owner: x\n")
    _markers(root.parent / "sim")

    rc, results = _check_repo_materialization(root)

    assert results[0]["status"] == "not-a-git-repo"
    assert rc == 1, "an unversioned repo must fail doctor, not pass it"


def test_real_git_repo_is_ok(tmp_path):
    root = _program(tmp_path, "  - name: sim\n    path: ../sim\n    owner: x\n")
    repo = _markers(root.parent / "sim")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "ok"


def test_dot_git_as_a_file_is_ok(tmp_path):
    """Worktrees and submodules write `.git` as a file holding `gitdir: …`.

    Checking `is_dir()` here would fail every worktree-based checkout — trading
    the reported false green for a false red on a perfectly valid layout.
    """
    root = _program(tmp_path, "  - name: sim\n    path: ../sim\n    owner: x\n")
    repo = _markers(root.parent / "sim")
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/sim\n", encoding="utf-8")

    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "ok"


def test_repo_nested_inside_a_parent_checkout_is_ok(tmp_path):
    """Versioned by an ancestor — the work IS protected, so this is not the bug."""
    root = _program(tmp_path, "  - name: sim\n    path: ../mono/sim\n    owner: x\n")
    mono = root.parent / "mono"
    mono.mkdir()
    subprocess.run(["git", "init", "-q", str(mono)], check=True)
    _markers(mono / "sim")

    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "ok"


def test_missing_markers_still_outranks_the_git_check(tmp_path):
    """A bare directory is unmaterialized first — `sync-repos` is the right hint.

    Reporting "not a git repo" for a path that was never checked out at all
    would send the reader chasing `git init` when the actual fix is sync-repos.
    """
    root = _program(tmp_path, "  - name: sim\n    path: ../sim\n    owner: x\n")
    (root.parent / "sim").mkdir()

    rc, results = _check_repo_materialization(root)
    assert results[0]["status"] == "unmaterialized"
    assert rc == 0


def test_missing_remote_is_advisory_only(tmp_path):
    """No `remote:` means no clone, no push, no recovery — worth saying, not failing.

    A local-only repo is a legitimate choice, so this annotates rather than
    changing the verdict or the exit code.
    """
    root = _program(tmp_path, "  - name: sim\n    path: ../sim\n    owner: x\n")
    repo = _markers(root.parent / "sim")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    rc, results = _check_repo_materialization(root)
    assert rc == 0
    assert results[0]["status"] == "ok"
    assert results[0]["no_remote"] is True


def test_declared_remote_is_not_flagged(tmp_path):
    root = _program(
        tmp_path,
        "  - name: sim\n    path: ../sim\n    owner: x\n    remote: git@h:o/sim.git\n",
    )
    repo = _markers(root.parent / "sim")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    _rc, results = _check_repo_materialization(root)
    assert results[0].get("no_remote") is not True


def test_report_names_the_risk_and_the_fix(tmp_path, capsys):
    """The printed line has to say why it matters — the reader is deciding
    whether to interrupt what they are doing."""
    _print_repo_materialization_report(
        [{"name": "sim", "path": "../sim", "status": "not-a-git-repo", "no_remote": True}]
    )
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "not a git repository" in out
    assert "git init" in out
    assert "no remote" in out.lower()
