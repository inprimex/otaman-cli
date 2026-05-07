"""Tests for cli.maestro.resolve_agent_identity().

Covers the 2026-04-29 fix: per-repo identity from CWD→platform.yaml→owner
takes priority over the project-global .agents/current-agent file.

Before the fix, every tab read the same global identity (set last by
`maestro set-agent`), so /maestro:check from GreenBin.Deploy showed
mobile-agent's messages and the bus-status-hook's "[maestro] N pending"
line was wrong in 7/8 tabs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# resolve_agent_identity is provided by otaman_cli.identity
# (pythonpath includes src/ via pyproject.toml).
from otaman_cli.identity import resolve_agent_identity


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Lay out a maestro project: maestro folder + 3 sibling repos.

    Returns the maestro folder path (the conventional ``project_root``).
    """
    parent = tmp_path / "myplatform"
    parent.mkdir()

    maestro = parent / "myplatform-maestro"
    maestro.mkdir()
    (maestro / ".agents").mkdir()

    backend = parent / "auth-service"
    backend.mkdir()
    web = parent / "web-app"
    web.mkdir()
    (web / "src").mkdir()  # nested cwd

    (maestro / "platform.yaml").write_text(
        """
project: myplatform
version: "1.0"
repos:
  - name: auth-service
    path: ../auth-service
    owner: backend-agent
  - name: web-app
    path: ../web-app
    owner: frontend-agent
""".strip(),
        encoding="utf-8",
    )
    return maestro


# ---------------------------------------------------------------------------
# Priority 1: explicit arg always wins


def test_explicit_arg_wins_over_everything(project: Path) -> None:
    (project / ".agents" / "current-agent").write_text("global-agent\n")
    backend_cwd = project.parent / "auth-service"
    assert resolve_agent_identity(project, backend_cwd, explicit="cli-passed-agent") == "cli-passed-agent"


# ---------------------------------------------------------------------------
# Priority 2: CWD → platform.yaml → owner


def test_cwd_in_repo_root_resolves_owner(project: Path) -> None:
    """CWD == repo path should resolve to that repo's owner, not global."""
    (project / ".agents" / "current-agent").write_text("stale-global-agent\n")
    backend_cwd = project.parent / "auth-service"
    assert resolve_agent_identity(project, backend_cwd) == "backend-agent"


def test_cwd_nested_inside_repo_resolves_owner(project: Path) -> None:
    """CWD inside a repo subdir should still resolve to the repo's owner."""
    (project / ".agents" / "current-agent").write_text("stale-global-agent\n")
    nested_cwd = project.parent / "web-app" / "src"
    assert resolve_agent_identity(project, nested_cwd) == "frontend-agent"


def test_different_repos_resolve_to_different_owners(project: Path) -> None:
    """Same project, two repos — each tab must get its own owner."""
    (project / ".agents" / "current-agent").write_text("frontend-agent\n")  # last set
    backend_cwd = project.parent / "auth-service"
    web_cwd = project.parent / "web-app"
    assert resolve_agent_identity(project, backend_cwd) == "backend-agent"
    assert resolve_agent_identity(project, web_cwd) == "frontend-agent"


# ---------------------------------------------------------------------------
# Priority 3: .agents/current-agent fallback


def test_cwd_outside_any_repo_falls_back_to_current_agent(project: Path) -> None:
    """CWD == maestro folder (not in any repo) → use current-agent fallback."""
    (project / ".agents" / "current-agent").write_text("global-fallback\n")
    assert resolve_agent_identity(project, project) == "global-fallback"


def test_no_platform_yaml_falls_back_to_current_agent(tmp_path: Path) -> None:
    """Project root with no platform.yaml — fallback path still works."""
    root = tmp_path / "bare"
    (root / ".agents").mkdir(parents=True)
    (root / ".agents" / "current-agent").write_text("only-fallback\n")
    assert resolve_agent_identity(root, tmp_path) == "only-fallback"


def test_returns_none_when_nothing_resolves(project: Path) -> None:
    """No CWD match, no current-agent file → None (caller handles error)."""
    # Don't write current-agent; CWD is outside any repo
    assert resolve_agent_identity(project, project) is None


# ---------------------------------------------------------------------------
# Edge cases


def test_malformed_platform_yaml_does_not_crash(project: Path) -> None:
    """Bad YAML should fall back to current-agent, not raise."""
    (project / "platform.yaml").write_text("not: valid: yaml: ::: [", encoding="utf-8")
    (project / ".agents" / "current-agent").write_text("rescue-agent\n")
    assert resolve_agent_identity(project, project.parent / "auth-service") == "rescue-agent"


def test_repo_missing_owner_field_skipped(project: Path) -> None:
    """A repo entry without `owner` shouldn't match — fall through to next or fallback."""
    (project / "platform.yaml").write_text(
        """
project: myplatform
version: "1.0"
repos:
  - name: auth-service
    path: ../auth-service
    # owner missing
  - name: web-app
    path: ../web-app
    owner: frontend-agent
""".strip(),
        encoding="utf-8",
    )
    (project / ".agents" / "current-agent").write_text("default-fallback\n")
    backend_cwd = project.parent / "auth-service"
    # auth-service has no owner → falls through to current-agent
    assert resolve_agent_identity(project, backend_cwd) == "default-fallback"
    # web-app still resolves correctly
    web_cwd = project.parent / "web-app"
    assert resolve_agent_identity(project, web_cwd) == "frontend-agent"


def test_empty_current_agent_file_returns_none(project: Path) -> None:
    """Whitespace-only current-agent → treat as empty, return None when no CWD match."""
    (project / ".agents" / "current-agent").write_text("   \n\n")
    assert resolve_agent_identity(project, project) is None


def test_strips_whitespace_from_owner(project: Path) -> None:
    """Owner field with surrounding spaces should be normalised."""
    (project / "platform.yaml").write_text(
        """
project: myplatform
version: "1.0"
repos:
  - name: auth-service
    path: ../auth-service
    owner: "  backend-agent  "
""".strip(),
        encoding="utf-8",
    )
    backend_cwd = project.parent / "auth-service"
    assert resolve_agent_identity(project, backend_cwd) == "backend-agent"
