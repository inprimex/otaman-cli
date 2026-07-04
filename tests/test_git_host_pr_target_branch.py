"""Tests for `otaman git-host pr target-branch` (git-flow-branch-config 2.1).

Read-only advisory: resolves and prints a default PR target branch from
`standards.git.environments` / `standards.git.development_branch`, creating
nothing and requiring no `git_host:` config. Covers the three spec.md
scenarios plus the branch-vs-tag_pattern discrimination.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from otaman_cli import main as cli_main


def _run_cli(args: list[str], cwd: Path):
    buf = io.StringIO()
    orig_argv = sys.argv[:]
    orig_cwd = Path.cwd()
    try:
        sys.argv = ["otaman"] + args
        import os as _os
        _os.chdir(str(cwd))
        with redirect_stdout(buf):
            rc = cli_main.main()
    finally:
        sys.argv = orig_argv
        import os as _os
        _os.chdir(str(orig_cwd))
    return rc, buf.getvalue()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".agents").mkdir()
    return tmp_path


def _write_platform(root: Path, body: str) -> None:
    (root / "platform.yaml").write_text(body, encoding="utf-8")


# Scenario: advisory resolves to the declared staging branch
def test_resolves_first_branch_keyed_environment(project: Path) -> None:
    _write_platform(project, """\
project: test
standards:
  git:
    environments:
      - {branch: develop, environment: staging, deploy_trigger: on_push}
      - {branch: main, environment: production, deploy_trigger: on_merge}
""")
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert "develop" in lines
    assert "main" not in lines  # only the FIRST branch-keyed entry is resolved


# Scenario: a tag_pattern-only environments list falls back to development_branch
def test_tag_pattern_only_falls_back_to_development_branch(project: Path) -> None:
    _write_platform(project, """\
project: test
standards:
  git:
    development_branch: main
    environments:
      - {tag_pattern: "v*", environment: production, deploy_trigger: on_tag}
""")
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    assert "main" in out
    assert "v*" not in out


# Scenario: no configuration declared
def test_no_configuration_prints_informational_message(project: Path) -> None:
    _write_platform(project, "project: test\n")
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    assert "no branch preference declared" in out.lower()


def test_mixed_environments_skips_tag_pattern_entries_to_find_branch(project: Path) -> None:
    """A tag_pattern-keyed entry ordered before a branch-keyed one must be
    skipped, not mistakenly treated as a branch name."""
    _write_platform(project, """\
project: test
standards:
  git:
    environments:
      - {tag_pattern: "v*", environment: production, deploy_trigger: on_tag}
      - {branch: develop, environment: staging, deploy_trigger: on_push}
""")
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    assert "develop" in out
    assert "v*" not in out


def test_no_platform_yaml_treated_as_no_configuration(project: Path) -> None:
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    assert "no branch preference declared" in out.lower()


def test_creates_no_pr_and_needs_no_git_host_config(project: Path) -> None:
    """No `git_host:` block at all — target-branch must still work, unlike
    every other `pr` action which requires one."""
    _write_platform(project, """\
project: test
standards:
  git:
    development_branch: main
""")
    rc, out = _run_cli(["git-host", "pr", "target-branch"], cwd=project)
    assert rc == 0
    assert "main" in out
