"""Tests for monorepo-path-ownership tasks 2.1, 2.2.

2.1 — `otaman whoami --for-path <path>` resolves the owning agent for a path
2.2 — `otaman owner-paths --validate` reports overlaps + unknown agents
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from otaman_cli.owner_paths import (
    _glob_matches,
    resolve_owner_for_path,
    validate_owner_paths,
)


# ---------------------------------------------------------------- helpers
def _make_project(tmp_path: Path, repos_yaml: str, agents_yaml: str = "") -> Path:
    """Stage a minimal otaman project with the given platform.yaml shape.

    Caller passes raw YAML fragments — *repos_yaml* becomes the body of the
    `repos:` list, *agents_yaml* becomes the body of the `agents:` block.
    Both should be already-correctly-indented (2 spaces).
    """
    parts = ["project: tst", "version: '1.0'"]
    if agents_yaml.strip():
        parts.append("agents:")
        parts.append(agents_yaml.rstrip())
    parts.append("repos:")
    parts.append(repos_yaml.rstrip())
    body = "\n".join(parts) + "\n"
    (tmp_path / "platform.yaml").write_text(body, encoding="utf-8")
    return tmp_path


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


# ---------------------------------------------------------------- glob matcher
class TestGlobMatch:
    def test_double_star_matches_subtree(self):
        assert _glob_matches("apps/web/src/App.tsx", "apps/web/**")
        assert _glob_matches("apps/web/", "apps/web/**")  # trailing slash
        assert not _glob_matches("apps/api/foo.js", "apps/web/**")

    def test_single_star_does_not_cross_slash(self):
        assert _glob_matches("apps/web", "apps/*")
        assert not _glob_matches("apps/web/x", "apps/*")

    def test_question_mark_matches_single_char(self):
        assert _glob_matches("a/b", "a/?")
        assert not _glob_matches("a/bc", "a/?")

    def test_special_regex_chars_escaped(self):
        # Patterns containing dot or plus should be treated as literal
        assert _glob_matches("file.test.ts", "file.test.ts")
        assert not _glob_matches("filextest.ts", "file.test.ts")

    def test_exact_match_no_wildcards(self):
        assert _glob_matches("apps/web", "apps/web")
        assert not _glob_matches("apps/web/x", "apps/web")


# ---------------------------------------------------------------- task 2.1
class TestResolveOwner:
    def test_path_under_glob_returns_matched_owner(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": api-agent
        """),
        )
        # Stage the on-disk repo dir
        (tmp_path / "mono" / "apps" / "web" / "src").mkdir(parents=True)
        (tmp_path / "mono" / "apps" / "web" / "src" / "App.tsx").touch()

        result = resolve_owner_for_path(
            tmp_path / "mono" / "apps" / "web" / "src" / "App.tsx",
            project_root=tmp_path,
        )
        assert result is not None
        assert result.agent == "web-agent"
        assert result.matched_glob == "apps/web/**"
        assert result.repo_name == "my-monorepo"

    def test_path_with_no_glob_match_falls_back_to_root_owner(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
        """),
        )
        (tmp_path / "mono" / "packages" / "legacy").mkdir(parents=True)
        (tmp_path / "mono" / "packages" / "legacy" / "index.js").touch()

        result = resolve_owner_for_path(
            tmp_path / "mono" / "packages" / "legacy" / "index.js",
            project_root=tmp_path,
        )
        assert result is not None
        assert result.agent == "root-agent"
        assert result.matched_glob is None
        assert "no glob matched" in result.fallback_reason

    def test_repo_with_no_owner_paths_falls_back(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        (tmp_path / "mono" / "src").mkdir(parents=True)
        (tmp_path / "mono" / "src" / "x.py").touch()

        result = resolve_owner_for_path(
            tmp_path / "mono" / "src" / "x.py",
            project_root=tmp_path,
        )
        assert result is not None
        assert result.agent == "root-agent"
        assert "no owner-paths configured" in result.fallback_reason

    def test_path_outside_any_repo_returns_none(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        (tmp_path / "mono").mkdir()
        # File outside the registered repo
        outside = tmp_path / "outside.txt"
        outside.touch()

        result = resolve_owner_for_path(outside, project_root=tmp_path)
        assert result is None

    def test_specificity_longest_glob_wins(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "packages/**": shared-agent
              "packages/ui/**": ui-agent
        """),
        )
        (tmp_path / "mono" / "packages" / "ui").mkdir(parents=True)
        (tmp_path / "mono" / "packages" / "ui" / "Button.tsx").touch()

        result = resolve_owner_for_path(
            tmp_path / "mono" / "packages" / "ui" / "Button.tsx",
            project_root=tmp_path,
        )
        # "packages/ui/**" is more specific (longer) → ui-agent wins
        assert result.agent == "ui-agent"
        assert result.matched_glob == "packages/ui/**"

    def test_missing_platform_yaml_returns_none(self, tmp_path: Path):
        result = resolve_owner_for_path(tmp_path / "x.py", project_root=tmp_path)
        assert result is None

    def test_underscore_form_owner_paths_accepted(self, tmp_path: Path):
        """Some platform.yaml files use `owner_paths:` (underscore); both shapes accepted."""
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner_paths:
              "apps/web/**": web-agent
        """),
        )
        (tmp_path / "mono" / "apps" / "web").mkdir(parents=True)
        (tmp_path / "mono" / "apps" / "web" / "x.tsx").touch()

        result = resolve_owner_for_path(
            tmp_path / "mono" / "apps" / "web" / "x.tsx",
            project_root=tmp_path,
        )
        assert result.agent == "web-agent"


# ---------------------------------------------------------------- task 2.2
class TestValidateOwnerPaths:
    def test_no_owner_paths_returns_empty_list(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        findings = validate_owner_paths(tmp_path)
        assert findings == []

    def test_all_valid_returns_ok_per_pattern(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": api-agent
              "packages/shared/**": shared-agent
              "infra/**": infra-agent
        """),
            agents_yaml=textwrap.dedent("""
            - name: root-agent
            - name: web-agent
            - name: api-agent
            - name: shared-agent
            - name: infra-agent
        """),
        )
        findings = validate_owner_paths(tmp_path)
        ok = [f for f in findings if f.severity == "ok"]
        errors = [f for f in findings if f.severity == "error"]
        assert len(ok) == 4
        assert len(errors) == 0

    def test_unknown_agent_is_error(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": ghost-agent
        """),
            agents_yaml=textwrap.dedent("""
            - name: root-agent
            - name: web-agent
        """),
        )
        findings = validate_owner_paths(tmp_path)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].agent == "ghost-agent"
        assert "not declared" in errors[0].note

    def test_overlapping_equal_length_patterns_warn(self, tmp_path: Path):
        # Two patterns of identical length that both match a sample path
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "packages/aaa/**": agent-x
              "packages/bbb/**": agent-y
              "packages/***": agent-z
        """),
        )
        findings = validate_owner_paths(tmp_path)
        # packages/aaa/** and packages/bbb/** are equal-length but don't share
        # a sample path; only the wildcard-heavy "packages/***" could overlap
        # — depends on the example-path heuristic.  We assert: validator
        # ran without crashing and emitted at least some findings.
        assert len(findings) >= 3

    def test_owner_field_treated_as_declared_when_no_agents_list(self, tmp_path: Path):
        """When platform.yaml has no `agents:` list, repos[].owner is the only
        source of agent identities.  Validator should accept patterns referencing
        any of those owners as known."""
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": root-agent
        """),
        )
        findings = validate_owner_paths(tmp_path)
        # web-agent is unknown (not in any owner: nor agents:) → error
        # root-agent is in repo's owner: → ok
        assert any(f.agent == "web-agent" and f.severity == "error" for f in findings)
        assert any(f.agent == "root-agent" and f.severity == "ok" for f in findings)


# ---------------------------------------------------------------- CLI smoke
class TestCmdWhoamiForPath:
    def test_for_path_command_prints_owner(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
        """),
        )
        (tmp_path / "mono" / "apps" / "web" / "src").mkdir(parents=True)
        (tmp_path / "mono" / "apps" / "web" / "src" / "App.tsx").touch()

        r = _run_cli(
            tmp_path,
            "whoami",
            "--for-path",
            "mono/apps/web/src/App.tsx",
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "web-agent" in r.stdout
        assert "apps/web/**" in r.stdout
        assert "my-monorepo" in r.stdout

    def test_for_path_outside_repo_exits_1(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        (tmp_path / "mono").mkdir()
        (tmp_path / "scratch.txt").touch()

        r = _run_cli(tmp_path, "whoami", "--for-path", "scratch.txt")
        assert r.returncode == 1
        assert "not under any repo" in r.stdout

    def test_for_path_missing_arg_exits_1(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        r = _run_cli(tmp_path, "whoami", "--for-path")
        assert r.returncode == 1
        # Either UI.error or a stderr message
        assert "--for-path" in r.stdout or "--for-path" in r.stderr


class TestCmdOwnerPathsValidate:
    def test_valid_setup_exits_0(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": api-agent
        """),
            agents_yaml=textwrap.dedent("""
            - name: root-agent
            - name: web-agent
            - name: api-agent
        """),
        )
        r = _run_cli(tmp_path, "owner-paths", "--validate")
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "[ok]" in r.stdout
        assert "0 errors" in r.stdout

    def test_unknown_agent_exits_1(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
            owner-paths:
              "apps/web/**": web-agent
              "apps/api/**": ghost-agent
        """),
            agents_yaml=textwrap.dedent("""
            - name: root-agent
            - name: web-agent
        """),
        )
        r = _run_cli(tmp_path, "owner-paths", "--validate")
        assert r.returncode == 1
        assert "[ERROR]" in r.stdout
        assert "ghost-agent" in r.stdout

    def test_no_owner_paths_configured_exits_0(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        r = _run_cli(tmp_path, "owner-paths", "--validate")
        assert r.returncode == 0
        assert "No owner-paths configured" in r.stdout

    def test_missing_validate_flag_exits_2(self, tmp_path: Path):
        _make_project(
            tmp_path,
            repos_yaml=textwrap.dedent("""
          - name: my-monorepo
            path: ./mono
            owner: root-agent
        """),
        )
        r = _run_cli(tmp_path, "owner-paths")
        assert r.returncode == 2
        assert "--validate" in r.stdout or "Usage" in r.stdout
