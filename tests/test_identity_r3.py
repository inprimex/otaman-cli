"""Tests for the R3 security/correctness fix (2026-07-08, greenbin incident).

`resolve_agent_identity()` used to trust `OTAMAN_AGENT` unconditionally
(no cross-check against the actual repo owner) and duplicated a simpler,
`owner-paths`-unaware version of `owner_paths.resolve_owner_for_path()`.
`.agents/current-agent`'s value was accepted with no validation at all.
These tests exercise all three fixes directly, distinct from
test_identity_priority_chain.py / test_resolve_agent_identity.py (which
cover the general priority-chain behavior these fixes sit inside).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.identity import resolve_agent_identity
from otaman_cli.owner_paths import declared_agents_from_platform


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def project(tmp_path: Path) -> Path:
    """otaman root + one sibling repo with an owner-paths glob override."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "platform-meta"
    meta.mkdir()
    (meta / ".agents").mkdir()
    repo = parent / "monorepo"
    (repo / "apps" / "web" / "src").mkdir(parents=True)
    (repo / "apps" / "api").mkdir(parents=True)
    (meta / "platform.yaml").write_text(
        "project: platform\n"
        "agents:\n"
        "  - name: root-agent\n"
        "  - name: web-agent\n"
        "repos:\n"
        "  - name: monorepo\n"
        "    path: ../monorepo\n"
        "    owner: root-agent\n"
        "    owner-paths:\n"
        "      apps/web/**: web-agent\n",
        encoding="utf-8",
    )
    return meta


# ---------------------------------------------------------------- OTAMAN_AGENT cross-check
class TestOtamanAgentCrossCheck:
    def test_agreeing_env_var_returns_silently(self, project: Path, monkeypatch, capsys):
        monkeypatch.setenv("OTAMAN_AGENT", "root-agent")
        result = resolve_agent_identity(project, project.parent / "monorepo" / "apps" / "api")
        assert result == "root-agent"
        assert capsys.readouterr().err == ""

    def test_disagreeing_env_var_is_overridden_with_warning(
        self, project: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("OTAMAN_AGENT", "stale-agent")
        result = resolve_agent_identity(project, project.parent / "monorepo" / "apps" / "api")
        assert result == "root-agent"
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "stale-agent" in err
        assert "root-agent" in err

    def test_disagreeing_env_var_against_owner_paths_override(
        self, project: Path, monkeypatch, capsys
    ):
        """The cross-check must use the FULL owner-paths-aware resolution
        (web-agent for apps/web/**), not just the repo root owner."""
        monkeypatch.setenv("OTAMAN_AGENT", "stale-agent")
        result = resolve_agent_identity(
            project, project.parent / "monorepo" / "apps" / "web" / "src"
        )
        assert result == "web-agent"
        assert "web-agent" in capsys.readouterr().err

    def test_env_var_trusted_when_cwd_outside_any_repo(self, project: Path, monkeypatch, capsys):
        """No CWD owner to cross-check against -- OTAMAN_AGENT is trusted,
        same as before R3 (e.g. running from otaman-meta itself)."""
        monkeypatch.setenv("OTAMAN_AGENT", "some-agent")
        result = resolve_agent_identity(project, project)
        assert result == "some-agent"
        assert capsys.readouterr().err == ""

    def test_explicit_arg_bypasses_cross_check_entirely(self, project: Path, monkeypatch):
        """--agent / explicit still wins unconditionally -- intentional
        cross-repo/override invocations use this, not OTAMAN_AGENT."""
        monkeypatch.setenv("OTAMAN_AGENT", "stale-agent")
        result = resolve_agent_identity(
            project,
            project.parent / "monorepo" / "apps" / "api",
            explicit="deliberate-agent",
        )
        assert result == "deliberate-agent"


# ---------------------------------------------------------------- owner-paths delegation
class TestOwnerPathsDelegation:
    def test_owner_paths_glob_resolves_via_resolve_agent_identity(self, project: Path, monkeypatch):
        """Before R3, step 3b's hand-rolled duplicate had no owner-paths
        support and would have returned the repo root owner (root-agent)
        here instead of the more specific web-agent."""
        monkeypatch.delenv("OTAMAN_AGENT", raising=False)
        result = resolve_agent_identity(
            project, project.parent / "monorepo" / "apps" / "web" / "src"
        )
        assert result == "web-agent"

    def test_non_glob_path_falls_back_to_repo_root_owner(self, project: Path, monkeypatch):
        monkeypatch.delenv("OTAMAN_AGENT", raising=False)
        result = resolve_agent_identity(project, project.parent / "monorepo" / "apps" / "api")
        assert result == "root-agent"


# -------------------------------------------------------- .agents/current-agent roster validation
class TestCurrentAgentRosterValidation:
    def test_declared_value_accepted(self, project: Path, monkeypatch, capsys):
        monkeypatch.delenv("OTAMAN_AGENT", raising=False)
        (project / ".agents" / "current-agent").write_text("root-agent\n")
        result = resolve_agent_identity(project, project)
        assert result == "root-agent"
        assert "DEPRECATED" in capsys.readouterr().err

    def test_undeclared_value_rejected_and_falls_through(self, project: Path, monkeypatch, capsys):
        monkeypatch.delenv("OTAMAN_AGENT", raising=False)
        (project / ".agents" / "current-agent").write_text("totally-unknown-agent\n")
        result = resolve_agent_identity(project, project)
        assert result is None
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "totally-unknown-agent" in err
        assert "DEPRECATED" not in err  # rejected before the trust/return path

    def test_empty_roster_is_permissive(self, tmp_path: Path, monkeypatch, capsys):
        """No agents:/repos: declared at all -- nothing to validate against,
        so the deprecated fallback still works (same permissive-by-default
        posture the rest of the chain already has)."""
        root = tmp_path / "bare"
        (root / ".agents").mkdir(parents=True)
        monkeypatch.delenv("OTAMAN_AGENT", raising=False)
        (root / ".agents" / "current-agent").write_text("anything-goes\n")
        result = resolve_agent_identity(root, tmp_path)
        assert result == "anything-goes"


# ---------------------------------------------------------------- declared_agents_from_platform
class TestDeclaredAgentsFromPlatform:
    def test_agents_list_and_repo_owners_both_included(self):
        platform = {
            "agents": [{"name": "a-agent"}, {"name": "b-agent"}],
            "repos": [{"name": "r1", "owner": "c-agent"}],
        }
        assert declared_agents_from_platform(platform) == {"a-agent", "b-agent", "c-agent"}

    def test_empty_platform_returns_empty_set(self):
        assert declared_agents_from_platform({}) == set()

    def test_malformed_entries_skipped(self):
        platform = {
            "agents": ["not-a-dict", {"name": ""}, {"no_name_key": "x"}],
            "repos": [{"owner": ""}, {"owner": 123}, "not-a-dict"],
        }
        assert declared_agents_from_platform(platform) == set()
