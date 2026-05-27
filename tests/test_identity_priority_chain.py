"""Tests for the new identity resolution priority chain.

Covers agent-identity-per-directory spec (D1):
1. explicit arg
2. OTAMAN_AGENT env var
3. ~/.otaman-session file
4a. .otaman agent: field (CWD walk)
4b. platform.yaml CWD->owner (backwards-compat)
5. .agents/current-agent (deprecated, emits warning)
6. None when nothing resolves
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


def test_env_var_wins_over_session_file(project: Path, tmp_path: Path, monkeypatch) -> None:
    session = tmp_path / ".otaman-session"
    session.write_text("session-agent\n")
    monkeypatch.setenv("OTAMAN_AGENT", "env-agent")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert resolve_agent_identity(project) == "env-agent"


def test_env_var_wins_over_dotoman_field(project: Path, monkeypatch) -> None:
    (project.parent / "svc-api" / ".otaman").write_text("agent: api-agent\n")
    monkeypatch.setenv("OTAMAN_AGENT", "env-override")
    assert resolve_agent_identity(project, project.parent / "svc-api") == "env-override"


def test_empty_env_var_skipped(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("OTAMAN_AGENT", "  ")
    (project / ".agents" / "current-agent").write_text("fallback-agent")
    result = resolve_agent_identity(project, project)
    assert result == "fallback-agent"


# ---------------------------------------------------------------------------
# Priority 3: ~/.otaman-session


def test_session_file_wins_over_dotoman(project: Path, tmp_path: Path, monkeypatch) -> None:
    session = tmp_path / ".otaman-session"
    session.write_text("session-agent\n")
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (project.parent / "svc-api" / ".otaman").write_text("agent: api-agent\n")
    result = resolve_agent_identity(project, project.parent / "svc-api")
    assert result == "session-agent"


def test_comment_line_in_session_file_skipped(project: Path, tmp_path: Path, monkeypatch) -> None:
    session = tmp_path / ".otaman-session"
    session.write_text("# comment\n")
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (project.parent / "svc-api" / ".otaman").write_text("agent: api-agent\n")
    result = resolve_agent_identity(project, project.parent / "svc-api")
    assert result == "api-agent"


# ---------------------------------------------------------------------------
# Priority 4a: .otaman agent: field


def test_dotoman_agent_field_resolves(project: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent")
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


# ---------------------------------------------------------------------------
# Priority 5: .agents/current-agent (deprecated)


def test_current_agent_fallback_returns_value(project: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent")
    (project / ".agents" / "current-agent").write_text("legacy-agent\n")
    result = resolve_agent_identity(project, project)
    assert result == "legacy-agent"


def test_current_agent_fallback_emits_deprecated_warning(project: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent")
    (project / ".agents" / "current-agent").write_text("legacy-agent\n")
    resolve_agent_identity(project, project)
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err
    assert "legacy-agent" in captured.err


def test_current_agent_deprecation_marker_lines_skipped(project: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    """Lines starting with # in current-agent are skipped (deprecation markers)."""
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent")
    (project / ".agents" / "current-agent").write_text(
        "# DEPRECATED: identity now stored in ~/.otaman-session\n"
        "real-agent\n"
    )
    result = resolve_agent_identity(project, project)
    assert result == "real-agent"
    assert "DEPRECATED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Priority 6: None when nothing resolves


def test_returns_none_when_all_sources_absent(project: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent")
    result = resolve_agent_identity(project, project)
    assert result is None
