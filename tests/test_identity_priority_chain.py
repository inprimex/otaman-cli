"""Tests for the identity resolution priority chain.

Covers agent-identity-per-directory spec (D1, amended 2026-05-28):
1. explicit arg
2. OTAMAN_AGENT env var
3a. .otaman agent: field (CWD walk — keeps walking past files without agent:)
3b. platform.yaml CWD->owner (backwards-compat for un-updated repos)
4. .agents/current-agent (deprecated, emits warning)
5. None when nothing resolves

~/.otaman-session is no longer in the chain (dropped by 2026-05-28 amendment).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from otaman_cli.identity import resolve_agent_identity, _read_otaman_agent_field


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal otaman project: meta dir + two sibling repos."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "platform-meta"
    meta.mkdir()
    (meta / ".agents").mkdir()
    (parent / "svc-api").mkdir()
    (parent / "svc-web").mkdir()
    (meta / "platform.yaml").write_text(
        "project: platform\nrepos:\n"
        "  - name: svc-api\n    path: ../svc-api\n    owner: api-agent\n"
        "  - name: svc-web\n    path: ../svc-web\n    owner: web-agent\n",
        encoding="utf-8",
    )
    return meta


# ---------------------------------------------------------------------------
# Priority 2: OTAMAN_AGENT env var


def test_env_var_wins_over_dotoman_field(project: Path, monkeypatch) -> None:
    (project.parent / "svc-api" / ".otaman").write_text("agent: api-agent\n")
    monkeypatch.setenv("OTAMAN_AGENT", "env-override")
    assert resolve_agent_identity(project, project.parent / "svc-api") == "env-override"


def test_env_var_wins_over_current_agent(project: Path, monkeypatch) -> None:
    (project / ".agents" / "current-agent").write_text("fallback-agent")
    monkeypatch.setenv("OTAMAN_AGENT", "env-agent")
    assert resolve_agent_identity(project, project) == "env-agent"


def test_empty_env_var_skipped(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("OTAMAN_AGENT", "  ")
    (project / ".agents" / "current-agent").write_text("fallback-agent")
    result = resolve_agent_identity(project, project)
    assert result == "fallback-agent"


def test_session_file_ignored_even_if_present(project: Path, tmp_path: Path, monkeypatch) -> None:
    """~/.otaman-session must be silently ignored (spec scenario: ignored even if present)."""
    session = tmp_path / ".otaman-session"
    session.write_text("stale-plugin-agent\n")
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (project.parent / "svc-api" / ".otaman").write_text("agent: api-agent\n")
    result = resolve_agent_identity(project, project.parent / "svc-api")
    # Must resolve from .otaman, NOT from ~/.otaman-session
    assert result == "api-agent"


# ---------------------------------------------------------------------------
# Priority 3a: .otaman agent: field


def test_dotoman_agent_field_resolves(project: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    (project.parent / "svc-api" / ".otaman").write_text(
        "# Path to otaman folder\n../platform-meta\nagent: api-agent\n"
    )
    result = resolve_agent_identity(project, project.parent / "svc-api")
    assert result == "api-agent"


def test_read_otaman_agent_field_walk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    (repo / ".otaman").write_text("agent: owner-agent\n")
    assert _read_otaman_agent_field(sub) == "owner-agent"


def test_read_otaman_agent_field_not_found(tmp_path: Path) -> None:
    assert _read_otaman_agent_field(tmp_path) is None


def test_cwd_walk_continues_past_dotoman_without_agent(tmp_path: Path) -> None:
    """A .otaman without agent: must NOT stop the walk — keep going up (spec D1 task 2.2)."""
    child_repo = tmp_path / "child"
    child_repo.mkdir()
    # child has .otaman with no agent: field
    (child_repo / ".otaman").write_text("# no agent field here\n../meta\n")
    # parent has .otaman with agent:
    (tmp_path / ".otaman").write_text("agent: parent-agent\n")
    result = _read_otaman_agent_field(child_repo)
    assert result == "parent-agent"


def test_cwd_walk_stops_at_first_agent_field(tmp_path: Path) -> None:
    """Walk stops as soon as an agent: field is found."""
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    mid = tmp_path / "a"
    (mid / ".otaman").write_text("agent: mid-agent\n")
    (tmp_path / ".otaman").write_text("agent: root-agent\n")
    assert _read_otaman_agent_field(child) == "mid-agent"


# ---------------------------------------------------------------------------
# Priority 4: .agents/current-agent (deprecated)


def test_current_agent_fallback_returns_value(project: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    (project / ".agents" / "current-agent").write_text("legacy-agent\n")
    result = resolve_agent_identity(project, project)
    assert result == "legacy-agent"


def test_current_agent_fallback_emits_deprecated_warning(project: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    (project / ".agents" / "current-agent").write_text("legacy-agent\n")
    resolve_agent_identity(project, project)
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err
    assert "legacy-agent" in captured.err


def test_current_agent_deprecation_marker_lines_skipped(project: Path, monkeypatch, capsys) -> None:
    """Lines starting with # in current-agent are skipped (deprecation markers)."""
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    (project / ".agents" / "current-agent").write_text(
        "# DEPRECATED: identity now stored in .otaman agent: field\n"
        "real-agent\n"
    )
    result = resolve_agent_identity(project, project)
    assert result == "real-agent"
    assert "DEPRECATED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Priority 5: None when nothing resolves


def test_returns_none_when_all_sources_absent(project: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    result = resolve_agent_identity(project, project)
    assert result is None
