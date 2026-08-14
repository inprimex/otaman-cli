"""Tests for `otaman project` subcommands — Phase 1 (otaman-project-command).

Covers tasks 10.5–10.14 (assign / list / show / update / disable / enable / remove),
plus _platform.py helpers. Phase 2 (10.1–10.4 / 10.15–10.17 — `add` and
`remove --delete-remote`) lands after otaman-core 1.x ships.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from otaman_cli.project._platform import (
    append_repo,
    find_repo,
    is_git_repo,
    load_platform_yaml,
    remove_repo,
    save_platform_yaml,
    update_repo,
)

# ---------------------------------------------------------------------------
# _platform.py helpers


def test_append_repo_creates_list_if_absent():
    data: dict[str, Any] = {"project": "x"}
    append_repo(data, {"name": "svc", "owner": "x", "path": "./svc"})
    assert data["repos"] == [{"name": "svc", "owner": "x", "path": "./svc"}]


def test_append_repo_extends_existing():
    data = {"repos": [{"name": "a"}]}
    append_repo(data, {"name": "b"})
    assert [r["name"] for r in data["repos"]] == ["a", "b"]


def test_find_repo_returns_matching():
    data = {"repos": [{"name": "a"}, {"name": "b"}]}
    assert find_repo(data, "b") == {"name": "b"}


def test_find_repo_returns_none_when_missing():
    assert find_repo({"repos": []}, "x") is None
    assert find_repo({}, "x") is None


def test_remove_repo_drops_entry_and_reports_true():
    data = {"repos": [{"name": "a"}, {"name": "b"}]}
    assert remove_repo(data, "a") is True
    assert [r["name"] for r in data["repos"]] == ["b"]


def test_remove_repo_returns_false_when_missing():
    assert remove_repo({"repos": [{"name": "a"}]}, "b") is False


def test_update_repo_changes_fields_only():
    data = {"repos": [{"name": "a", "owner": "old", "path": "./a"}]}
    assert update_repo(data, "a", {"owner": "new", "url": "https://x"}) is True
    e = data["repos"][0]
    assert e["owner"] == "new"
    assert e["url"] == "https://x"
    assert e["path"] == "./a"  # untouched


def test_update_repo_immune_to_name_rename():
    data = {"repos": [{"name": "a"}]}
    update_repo(data, "a", {"name": "b", "owner": "x"})
    assert data["repos"][0]["name"] == "a"  # name is immutable per helper contract
    assert data["repos"][0]["owner"] == "x"


def test_update_repo_skips_none_values():
    data = {"repos": [{"name": "a", "owner": "x"}]}
    update_repo(data, "a", {"owner": None, "url": "https://y"})
    assert data["repos"][0]["owner"] == "x"
    assert data["repos"][0]["url"] == "https://y"


def test_save_and_reload_preserves_data(tmp_path: Path):
    data = {"project": "x", "repos": [{"name": "a", "owner": "y"}]}
    save_platform_yaml(tmp_path, data)
    assert (tmp_path / "platform.yaml").is_file()
    re = load_platform_yaml(tmp_path)
    assert re["project"] == "x"
    assert re["repos"][0]["name"] == "a"


def test_load_platform_yaml_raises_when_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_platform_yaml(tmp_path)


def test_is_git_repo(tmp_path: Path):
    assert is_git_repo(tmp_path) is False
    (tmp_path / ".git").mkdir()
    assert is_git_repo(tmp_path) is True


# ---------------------------------------------------------------------------
# Integration fixtures


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal otaman project: meta/ with platform.yaml + sibling backend git repo."""
    parent = tmp_path / "parent"
    parent.mkdir()
    meta = parent / "meta"
    meta.mkdir()
    (meta / ".agents").mkdir()
    save_platform_yaml(
        meta,
        {
            "project": "testprog",
            "repos": [
                {"name": "existing-svc", "path": "../existing-svc", "owner": "backend-agent"},
            ],
        },
    )
    # Plant the existing repo on disk so find_project_root walks up correctly
    existing = parent / "existing-svc"
    existing.mkdir()
    (existing / ".git").mkdir()
    # Init the meta as a git repo so commit operations don't fail
    subprocess.run(["git", "init", "-q"], cwd=str(meta), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "platform.yaml"],
        cwd=str(meta),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=str(meta),
        check=True,
        capture_output=True,
    )
    return meta


def _run(meta: Path, *cli_args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *cli_args],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
        # Force a real closed pipe rather than inheriting the runner's
        # stdin: on Windows CI it isn't reliably a non-TTY handle the way
        # it is on Linux/macOS, so sys.stdin.isatty() can take the TTY
        # branch instead of the deterministic non-interactive one (same
        # class of bug fixed for the upgrade batch-confirm tests, 42eb7a4).
        input="",
    )


# ---------------------------------------------------------------------------
# project assign (10.5 – 10.9)


def _make_local_git_repo(parent: Path, name: str, with_remote: str | None = None) -> Path:
    repo = parent / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    if with_remote:
        subprocess.run(
            ["git", "remote", "add", "origin", with_remote],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
    return repo


def test_assign_existing_git_repo_with_origin(project: Path):
    """Origin URL is stored under the schema-accepted `remote:` field
    (not `url:`, which was the spec's term but doesn't validate)."""
    new_repo = _make_local_git_repo(
        project.parent,
        "new-svc",
        with_remote="https://github.com/org/new-svc.git",
    )
    rc = _run(project, "project", "assign", str(new_repo), "--owner", "ops-agent")
    assert rc.returncode == 0, rc.stderr or rc.stdout
    data = load_platform_yaml(project)
    entry = find_repo(data, "new-svc")
    assert entry is not None
    assert entry["owner"] == "ops-agent"
    assert entry["remote"] == "https://github.com/org/new-svc.git"
    # NOT under `url:` — that would break otaman-core schema validation
    assert "url" not in entry


def test_assign_runs_otaman_init_and_writes_dot_otaman_marker(project: Path):
    """Spec scenario 10.5: AND `otaman init` runs in `../my-service`.

    The post-assign `_cmd_init_update()` call must write the per-repo
    `.otaman` marker with `agent: <owner>` so identity resolution from
    inside the assigned repo works without falling back to the deprecated
    .agents/current-agent file.
    """
    new_repo = _make_local_git_repo(
        project.parent,
        "deploy-svc",
        with_remote="https://github.com/org/deploy-svc.git",
    )
    rc = _run(project, "project", "assign", str(new_repo), "--owner", "deploy-agent")
    assert rc.returncode == 0, rc.stderr or rc.stdout

    # The .otaman marker must be present (file shape: "agent: deploy-agent" line)
    marker = new_repo / ".otaman"
    assert marker.exists(), f"`.otaman` marker missing in {new_repo} after assign"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8")
        assert "agent: deploy-agent" in text, (
            f"`.otaman` marker present but missing `agent: deploy-agent` line:\n{text}"
        )


def test_assign_existing_git_repo_no_remote_skips_remote_field(project: Path):
    new_repo = _make_local_git_repo(project.parent, "no-remote-svc")
    rc = _run(project, "project", "assign", str(new_repo), "--owner", "ops-agent")
    assert rc.returncode == 0, rc.stderr or rc.stdout
    entry = find_repo(load_platform_yaml(project), "no-remote-svc")
    assert entry is not None
    assert "remote" not in entry
    assert "url" not in entry


def test_assign_rejects_non_git_directory(project: Path):
    plain = project.parent / "plain"
    plain.mkdir()
    rc = _run(project, "project", "assign", str(plain), "--owner", "ops")
    assert rc.returncode != 0
    assert "not a git repository" in (rc.stdout + rc.stderr)
    # platform.yaml unchanged
    data = load_platform_yaml(project)
    assert {r["name"] for r in data["repos"]} == {"existing-svc"}


def test_assign_rejects_already_registered_name(project: Path):
    new_repo = _make_local_git_repo(project.parent, "fresh")
    rc = _run(
        project,
        "project",
        "assign",
        str(new_repo),
        "--owner",
        "ops-agent",
        "--name",
        "existing-svc",
    )
    assert rc.returncode != 0
    assert "already registered" in (rc.stdout + rc.stderr)


def test_assign_rejects_already_registered_path(project: Path):
    """existing-svc is already registered at ../existing-svc — re-assigning errors."""
    existing_path = project.parent / "existing-svc"
    rc = _run(project, "project", "assign", str(existing_path), "--owner", "ops-agent")
    assert rc.returncode != 0
    assert "already registered" in (rc.stdout + rc.stderr)


# ---------------------------------------------------------------------------
# project list (10.10)


def test_list_default_shows_active_only(project: Path):
    # Disable existing-svc first (via the schema-accepted `disabled` field)
    data = load_platform_yaml(project)
    find_repo(data, "existing-svc")["disabled"] = True
    save_platform_yaml(project, data)
    rc = _run(project, "project", "list")
    assert rc.returncode == 0
    assert "existing-svc" not in rc.stdout
    assert "No repos registered" in rc.stdout


def test_list_status_all_shows_everything(project: Path):
    data = load_platform_yaml(project)
    find_repo(data, "existing-svc")["disabled"] = True
    save_platform_yaml(project, data)
    rc = _run(project, "project", "list", "--status", "all")
    assert rc.returncode == 0
    assert "existing-svc" in rc.stdout
    # Status column derived from `disabled:`; user-facing label = 'inactive'
    assert "inactive" in rc.stdout


def test_list_marks_missing_local_dirs(project: Path):
    """When the repo's path doesn't exist on disk, list marks it [missing]."""
    data = load_platform_yaml(project)
    append_repo(data, {"name": "ghost-svc", "path": "../ghost-svc", "owner": "x"})
    save_platform_yaml(project, data)
    rc = _run(project, "project", "list")
    assert rc.returncode == 0
    assert "ghost-svc" in rc.stdout
    assert "[missing]" in rc.stdout


# ---------------------------------------------------------------------------
# project show (10.11)


def test_show_renders_all_fields(project: Path):
    rc = _run(project, "project", "show", "existing-svc")
    assert rc.returncode == 0
    out = rc.stdout
    assert "existing-svc" in out
    assert "backend-agent" in out
    assert "Resolved:" in out  # local state section


def test_show_unknown_repo_errors(project: Path):
    rc = _run(project, "project", "show", "no-such-svc")
    assert rc.returncode != 0
    assert "not found" in (rc.stdout + rc.stderr).lower()


# ---------------------------------------------------------------------------
# project update (10.12, 10.13)


def test_update_owner_field(project: Path):
    rc = _run(project, "project", "update", "existing-svc", "--owner", "new-owner")
    assert rc.returncode == 0
    entry = find_repo(load_platform_yaml(project), "existing-svc")
    assert entry["owner"] == "new-owner"


def test_update_multiple_fields_one_command(project: Path):
    rc = _run(
        project,
        "project",
        "update",
        "existing-svc",
        "--owner",
        "ops",
        "--url",
        "https://example.com/repo",
    )
    assert rc.returncode == 0
    e = find_repo(load_platform_yaml(project), "existing-svc")
    assert e["owner"] == "ops"
    # --url flag maps to schema-accepted `remote:` field
    assert e["remote"] == "https://example.com/repo"
    assert "url" not in e


def test_update_no_flags_errors(project: Path):
    rc = _run(project, "project", "update", "existing-svc")
    assert rc.returncode != 0
    assert "No field flags" in (rc.stdout + rc.stderr)


def test_update_unknown_repo_errors(project: Path):
    rc = _run(project, "project", "update", "no-such", "--owner", "x")
    assert rc.returncode != 0


# ---------------------------------------------------------------------------
# project disable / enable (10.14)


def test_disable_then_enable_round_trip(project: Path):
    """Uses schema-accepted `disabled: bool` (not `status:`); user-facing
    CLI surface still says disable/enable."""
    rc = _run(project, "project", "disable", "existing-svc")
    assert rc.returncode == 0
    entry = find_repo(load_platform_yaml(project), "existing-svc")
    assert entry["disabled"] is True
    # And NO `status:` field — that would break otaman-core schema validation
    assert "status" not in entry

    rc = _run(project, "project", "enable", "existing-svc")
    assert rc.returncode == 0
    entry = find_repo(load_platform_yaml(project), "existing-svc")
    assert "disabled" not in entry  # field dropped on enable


def test_disable_changes_default_list_visibility(project: Path):
    _run(project, "project", "disable", "existing-svc")
    rc = _run(project, "project", "list")
    assert "existing-svc" not in rc.stdout
    rc = _run(project, "project", "list", "--status", "all")
    assert "existing-svc" in rc.stdout


# ---------------------------------------------------------------------------
# project remove (10.15)


def test_remove_drops_entry_leaves_local_dir(project: Path):
    existing_dir = project.parent / "existing-svc"
    assert existing_dir.is_dir()
    rc = _run(project, "project", "remove", "existing-svc")
    assert rc.returncode == 0
    assert find_repo(load_platform_yaml(project), "existing-svc") is None
    # Local dir intact
    assert existing_dir.is_dir()


def test_remove_unknown_repo_errors(project: Path):
    rc = _run(project, "project", "remove", "no-such")
    assert rc.returncode != 0


def test_remove_delete_remote_non_tty_rejected(project: Path):
    """Spec Q6 — refuse in non-TTY (subprocess never is)."""
    rc = _run(project, "project", "remove", "existing-svc", "--delete-remote")
    assert rc.returncode != 0
    assert "TTY" in (rc.stdout + rc.stderr)


def test_remove_delete_remote_unknown_repo_reports_unknown_not_tty(project: Path):
    """Order-of-checks: unknown repo errors with 'not found', not 'TTY required'.
    Otherwise the operator sees a misleading error and may waste time finding a TTY."""
    rc = _run(project, "project", "remove", "no-such-svc", "--delete-remote")
    assert rc.returncode != 0
    msg = (rc.stdout + rc.stderr).lower()
    assert "not found" in msg
    assert "tty" not in msg, f"TTY error reported for unknown repo: {msg!r}"


# ---------------------------------------------------------------------------
# project add — gated on otaman-core 1.x


def test_add_not_yet_implemented(project: Path):
    """Phase 1: add returns a clear gated message until core ships 1.x."""
    rc = _run(project, "project", "add", "new-svc", "--owner", "x")
    assert rc.returncode != 0
    assert "not yet implemented" in (rc.stdout + rc.stderr)
