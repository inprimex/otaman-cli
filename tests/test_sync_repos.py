"""repo-registration-materialization 1.1 — `otaman sync-repos`.

Registration edits platform.yaml but changes nothing on disk; sync-repos
clones registered-but-absent repos from their remote and (re)generates the
per-repo agent artifacts (`.otaman` marker + gitignored CLAUDE.local.md).

The materialize tests use a LOCAL git repo as the "remote" (git clones
happily from a filesystem path), so they exercise the real clone + the real
generate-agent-config path without a network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from otaman_cli.commands.sync_repos import cmd_sync_repos


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _remote_src(parent: Path, name: str = "remote-src") -> Path:
    """A local git repo with one real commit, usable as a clone source."""
    src = parent / name
    src.mkdir()
    _git("init", "-q", cwd=src)
    (src / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", "README.md", cwd=src)
    _git("commit", "-q", "-m", "init", cwd=src)
    return src


def _program(tmp_path: Path, repos_yaml: str) -> Path:
    """A program root: git repo, platform.yaml, and an `.agents` bus dir
    (generate-agent-config writes queue files under it)."""
    meta = tmp_path / "meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: probeprog\nversion: '1.0'\nrepos:\n" + repos_yaml,
        encoding="utf-8",
    )
    _git("init", "-q", cwd=meta)
    _git("add", "-A", cwd=meta)
    _git("commit", "-q", "-m", "init", cwd=meta)
    return meta


# ---------------------------------------------------------------------------
# dry-run: report the plan, touch nothing


def test_dry_run_reports_plan_and_writes_nothing(tmp_path, capsys):
    src = _remote_src(tmp_path)
    meta = _program(
        tmp_path,
        f"  - name: probe-svc\n    path: ../probe-svc\n    owner: cli-agent\n    remote: {src}\n",
    )
    rc = cmd_sync_repos(["--dry-run", "--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would clone probe-svc" in out
    assert not (tmp_path / "probe-svc").exists()  # nothing materialized


def test_dry_run_materialized_repo_is_not_flagged_stale(tmp_path, capsys):
    # spec-agent gate note 2: a present, fully-materialized repo must read as
    # "no action", NOT "would regenerate" (which looked like false staleness).
    meta = _program(tmp_path, "  - name: docs\n    path: ../docs\n    owner: cli-agent\n")
    repo = (tmp_path / "docs").resolve()
    repo.mkdir()
    (repo / ".otaman").write_text("../meta\nagent: cli-agent\n", encoding="utf-8")
    (repo / "CLAUDE.local.md").write_text("rules\n", encoding="utf-8")
    rc = cmd_sync_repos(["--dry-run", "--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "present and materialized (no action)" in out
    assert "would regenerate" not in out
    assert "already materialized" in out


def test_dry_run_flags_present_but_unmaterialized(tmp_path, capsys):
    meta = _program(tmp_path, "  - name: bare\n    path: ../bare\n    owner: cli-agent\n")
    (tmp_path / "bare").mkdir()  # present but no marker/rules
    rc = cmd_sync_repos(["--dry-run", "--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would materialize bare (marker/rules missing)" in out


# ---------------------------------------------------------------------------
# the spec's key scenario: an absent repo is fully materialized


def test_sync_materializes_absent_repo(tmp_path, capsys):
    src = _remote_src(tmp_path)
    meta = _program(
        tmp_path,
        f"  - name: probe-svc\n    path: ../probe-svc\n    owner: cli-agent\n    remote: {src}\n",
    )
    rc = cmd_sync_repos(["--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 0, out

    repo = tmp_path / "probe-svc"
    assert (repo / "README.md").is_file()  # cloned from the remote
    assert (repo / ".otaman").is_file()  # marker generated
    assert (repo / "CLAUDE.local.md").is_file()  # orchestration rules generated
    assert "agent: cli-agent" in (repo / ".otaman").read_text(encoding="utf-8")
    assert "Materialized" in out and "probe-svc" in out


def test_idempotent_leaves_existing_tree(tmp_path):
    src = _remote_src(tmp_path)
    meta = _program(
        tmp_path,
        f"  - name: probe-svc\n    path: ../probe-svc\n    owner: cli-agent\n    remote: {src}\n",
    )
    assert cmd_sync_repos(["--path", str(meta)]) == 0
    # a local, uncommitted edit must survive a second run (no re-clone)
    sentinel = tmp_path / "probe-svc" / "local-note.txt"
    sentinel.write_text("LOCAL EDIT", encoding="utf-8")
    assert cmd_sync_repos(["--path", str(meta)]) == 0
    assert sentinel.read_text(encoding="utf-8") == "LOCAL EDIT"


# ---------------------------------------------------------------------------
# honest reporting


def test_absent_repo_without_remote_fails(tmp_path, capsys):
    meta = _program(
        tmp_path,
        "  - name: orphan-svc\n    path: ../orphan-svc\n    owner: cli-agent\n",
    )
    rc = cmd_sync_repos(["--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "orphan-svc" in out and "Cannot materialize" in out
    assert not (tmp_path / "orphan-svc").exists()


def test_generation_failure_not_reported_as_success(tmp_path, capsys, monkeypatch):
    """A clone that succeeds but whose artifact generation fails must be
    reported as a failure, never as materialized (spec: honest reporting)."""
    src = _remote_src(tmp_path)
    meta = _program(
        tmp_path,
        f"  - name: probe-svc\n    path: ../probe-svc\n    owner: cli-agent\n    remote: {src}\n",
    )
    # Stub the generation step so the clone lands but no .otaman/CLAUDE.local.md
    # is written — simulating a generator crash.
    import otaman_cli.commands.init as _init

    monkeypatch.setattr(_init, "_cmd_init_update", lambda dry_run=False: 1)

    rc = cmd_sync_repos(["--path", str(meta)])
    out = capsys.readouterr().out
    assert rc == 1
    assert (tmp_path / "probe-svc" / "README.md").is_file()  # clone happened
    assert "Artifact generation failed" in out and "probe-svc" in out
    assert "Materialized" not in out  # NOT reported as success


# ---------------------------------------------------------------------------
# guards


def test_rejects_org_level_platform_yaml(tmp_path, capsys):
    meta = tmp_path / "org"
    meta.mkdir()
    (meta / "platform.yaml").write_text("models: {}\nbus: {}\n", encoding="utf-8")
    rc = cmd_sync_repos(["--path", str(meta)])
    assert rc == 2
    assert "no 'project' key" in capsys.readouterr().out


def test_no_repos_registered_is_noop(tmp_path, capsys):
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "platform.yaml").write_text(
        "project: probeprog\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    rc = cmd_sync_repos(["--path", str(meta)])
    assert rc == 0
    assert "nothing to materialize" in capsys.readouterr().out.lower()


def test_resolves_from_program_dir_without_explicit_path(tmp_path, monkeypatch, capsys):
    # gate note 1: launched from the PROGRAM dir (which holds the meta but has
    # no marker of its own), sync-repos resolves via the single-child fallback
    # instead of erroring "Not in an otaman project".
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    progdir = tmp_path / "programs" / "prog"
    meta = progdir / "prog-meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: p\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    monkeypatch.chdir(progdir)
    rc = cmd_sync_repos([])  # no --path → find_program_root() → the child meta
    assert rc == 0
    assert "nothing to materialize" in capsys.readouterr().out.lower()
