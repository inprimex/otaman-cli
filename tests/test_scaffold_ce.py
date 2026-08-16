"""Tests for the CE-mode companion-repos scaffolder (ce-companion-repos-scaffold).

Covers tasks 1.6 (idempotency) + 1.7 (fresh / dry-run / force / no-network /
post-scaffold registry usability).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.onboard.scaffold_ce import (
    ScaffoldError,
    scaffold_companion_repos_ce,
)


@pytest.fixture
def meta(tmp_path: Path) -> Path:
    """Set up parent/<program>-specs with platform.yaml + .agents."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "epicbridge-specs"
    meta.mkdir()
    (meta / ".agents").mkdir()
    (meta / "platform.yaml").write_text(
        "project: epicbridge\n"
        "repos:\n"
        "  - name: epicbridge-specs\n"
        "    path: .\n"
        "    owner: spec-agent\n"
        "processes:\n"
        "  outcomes: true\n"
        "  solutions: true\n"
        "  personas: true\n",
        encoding="utf-8",
    )
    return meta


# ---------------------------------------------------------------------------
# Fresh scaffold


def test_scaffold_creates_business_repo_with_all_files(meta: Path) -> None:
    result = scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes", "solutions", "personas"],
        meta_dir=meta,
        program_name="EpicBridge",
    )
    target = meta.parent / "epicbridge-business"

    assert target.is_dir()
    assert (target / "CLAUDE.md").is_file()
    assert (target / "README.md").is_file()
    assert (target / ".gitignore").is_file()
    assert (target / "outcomes.yaml").is_file()
    assert (target / "solutions.yaml").is_file()
    assert (target / "personas.yaml").is_file()

    # Templates rendered correctly
    claude = (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert "EpicBridge" in claude
    assert "epicbridge" in claude
    assert "cpo-agent" in claude

    # Result struct populated
    assert len(result.created) == 1
    assert result.created[0].kind == "business"
    assert result.created[0].owner == "cpo-agent"
    assert result.platform_yaml_updated is True


def test_scaffold_initialises_git_repo(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    target = meta.parent / "epicbridge-business"
    assert (target / ".git").is_dir()
    # Exactly one initial commit
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(target),
        capture_output=True,
        text=True,
    )
    assert log.returncode == 0
    lines = [line for line in log.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "scaffold: initialize epicbridge-business" in lines[0]


def test_scaffold_updates_platform_yaml_repos(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    import yaml

    doc = yaml.safe_load((meta / "platform.yaml").read_text(encoding="utf-8"))
    repo_names = [r["name"] for r in doc["repos"]]
    assert "epicbridge-business" in repo_names
    entry = next(r for r in doc["repos"] if r["name"] == "epicbridge-business")
    assert entry["owner"] == "cpo-agent"
    assert entry["path"] == "../epicbridge-business"


def test_scaffold_strategy_repo_owned_by_cofounder(meta: Path) -> None:
    result = scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=[],
        meta_dir=meta,
        repo_kinds=["strategy"],
    )
    target = meta.parent / "epicbridge-strategy"
    assert (target / "CLAUDE.md").is_file()
    assert (target / "program-meta.yaml").is_file()
    assert "cofounder-agent" in (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert result.created[0].owner == "cofounder-agent"


def test_no_processes_means_no_repos(meta: Path) -> None:
    """No business-triggering processes → no companion repos."""
    result = scaffold_companion_repos_ce(
        program_slug="x",
        processes=[],
        meta_dir=meta,
    )
    assert result.repos == []


def test_unknown_kind_raises(meta: Path) -> None:
    with pytest.raises(ScaffoldError, match="Unknown companion repo kind"):
        scaffold_companion_repos_ce(
            program_slug="x",
            processes=[],
            meta_dir=meta,
            repo_kinds=["nonsense"],
        )


# ---------------------------------------------------------------------------
# Idempotency (task 1.6)


def test_scaffold_idempotent_second_run_skips(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    result = scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    assert result.created == []
    assert len(result.skipped) == 1
    assert "already exists" in result.skipped[0].skipped_reason


def test_idempotent_does_not_duplicate_platform_yaml_entries(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    import yaml

    doc = yaml.safe_load((meta / "platform.yaml").read_text(encoding="utf-8"))
    business_entries = [r for r in doc["repos"] if r["name"] == "epicbridge-business"]
    assert len(business_entries) == 1


def test_idempotent_does_not_duplicate_git_commits(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    target = meta.parent / "epicbridge-business"
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(target),
        capture_output=True,
        text=True,
    )
    lines = [line for line in log.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"Expected 1 commit after idempotent re-run; got {lines}"


# ---------------------------------------------------------------------------
# --dry-run


def test_dry_run_does_not_write_filesystem(meta: Path) -> None:
    result = scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
        dry_run=True,
    )
    target = meta.parent / "epicbridge-business"
    assert not target.exists()
    # Plan still computed
    assert len(result.created) == 1
    assert result.created[0].kind == "business"
    # platform.yaml NOT updated
    assert result.platform_yaml_updated is False
    import yaml

    doc = yaml.safe_load((meta / "platform.yaml").read_text(encoding="utf-8"))
    names = [r["name"] for r in doc["repos"]]
    assert "epicbridge-business" not in names


# ---------------------------------------------------------------------------
# --force


def test_force_recreates_existing_repo(meta: Path) -> None:
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
    )
    target = meta.parent / "epicbridge-business"
    sentinel = target / "user-edit.txt"
    sentinel.write_text("evidence the dir existed", encoding="utf-8")
    assert sentinel.is_file()

    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes"],
        meta_dir=meta,
        force=True,
    )
    # Force removes the old dir; the user file is gone
    assert not sentinel.is_file()
    assert (target / "outcomes.yaml").is_file()  # but the scaffold contents are back


# ---------------------------------------------------------------------------
# No network access (task 1.7)


def test_scaffold_runs_without_network(meta: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No HTTP / DNS calls. Block all socket use during the scaffold."""
    import socket

    original_socket = socket.socket

    def _blocked(*args, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("network access attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    try:
        scaffold_companion_repos_ce(
            program_slug="epicbridge",
            processes=["outcomes"],
            meta_dir=meta,
        )
    finally:
        monkeypatch.setattr(socket, "socket", original_socket)


# ---------------------------------------------------------------------------
# Post-scaffold integration: otaman outcome add works


def test_outcome_add_succeeds_after_scaffold(meta: Path) -> None:
    """End-to-end: after scaffold, `otaman outcome add` writes to the new business repo."""
    scaffold_companion_repos_ce(
        program_slug="epicbridge",
        processes=["outcomes", "solutions", "personas"],
        meta_dir=meta,
    )

    env = {**os.environ, "OTAMAN_AGENT": "human"}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "outcome",
            "add",
            "JTBD-1-create-account",
            "--as-a",
            "user",
            "--i-want-to",
            "x",
            "--incremental-outcome",
            "y",
            "--so-i-can",
            "z",
        ],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout
    business_outcomes = meta.parent / "epicbridge-business" / "outcomes.yaml"
    import yaml

    doc = yaml.safe_load(business_outcomes.read_text(encoding="utf-8"))
    assert len(doc["outcomes"]) == 1
    assert doc["outcomes"][0]["id"] == "JTBD-1-create-account"


# ---------------------------------------------------------------------------
# CLI subcommand integration: `otaman init companion-repos`


def test_cli_init_companion_repos_subcommand(meta: Path) -> None:
    env = {**os.environ, "OTAMAN_AGENT": "human"}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "init",
            "companion-repos",
            "--program",
            "epicbridge",
        ],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout
    target = meta.parent / "epicbridge-business"
    assert target.is_dir()
    assert (target / "outcomes.yaml").is_file()


def test_cli_init_companion_repos_dry_run(meta: Path) -> None:
    env = {**os.environ, "OTAMAN_AGENT": "human"}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "init",
            "companion-repos",
            "--program",
            "epicbridge",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )
    assert rc.returncode == 0
    target = meta.parent / "epicbridge-business"
    assert not target.exists()
    assert "would create" in rc.stdout.lower() or "dry-run" in rc.stdout.lower()


def test_cli_init_companion_repos_repos_flag(meta: Path) -> None:
    """--repos strategy creates only the strategy repo, even when business
    processes are enabled."""
    env = {**os.environ, "OTAMAN_AGENT": "human"}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "init",
            "companion-repos",
            "--program",
            "epicbridge",
            "--repos",
            "strategy",
        ],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )
    assert rc.returncode == 0, rc.stderr or rc.stdout
    assert (meta.parent / "epicbridge-strategy").is_dir()
    assert not (meta.parent / "epicbridge-business").exists()
