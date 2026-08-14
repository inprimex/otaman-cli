"""Tests for cli.maestro.resolve_agent_identity().

Covers the 2026-04-29 fix: per-repo identity from CWD→platform.yaml→owner
takes priority over the project-global .agents/current-agent file.

Before the fix, every tab read the same global identity (set last by
`maestro set-agent`), so /maestro:check from GreenBin.Deploy showed
mobile-agent's messages and the bus-status-hook's "[maestro] N pending"
line was wrong in 7/8 tabs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# resolve_agent_identity is provided by otaman_cli.identity
# (pythonpath includes src/ via pyproject.toml).
from otaman_cli.identity import resolve_agent_identity

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture(autouse=True)
def clear_otaman_agent_env(monkeypatch):
    """Ensure OTAMAN_AGENT env var doesn't bleed in from the test runner's environment."""
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Lay out a maestro project: maestro folder + 3 sibling repos.

    Returns the maestro folder path (the conventional ``project_root``).

    Each repo gets a ``.otaman`` file with ``agent: <owner>`` so the new
    per-directory resolution chain finds identity without needing platform.yaml.
    This matches the post-``otaman init --update`` state the new spec requires.
    """
    parent = tmp_path / "myplatform"
    parent.mkdir()

    maestro = parent / "myplatform-maestro"
    maestro.mkdir()
    (maestro / ".agents").mkdir()

    backend = parent / "auth-service"
    backend.mkdir()
    (backend / ".otaman").write_text("agent: backend-agent\n", encoding="utf-8")

    web = parent / "web-app"
    web.mkdir()
    (web / "src").mkdir()  # nested cwd
    (web / ".otaman").write_text("agent: frontend-agent\n", encoding="utf-8")

    (maestro / "platform.yaml").write_text(
        """
project: myplatform
version: "1.0"
agents:
  # R3: .agents/current-agent's value is now validated against the
  # declared-agents roster -- these extra names are declared so the
  # deprecated-fallback tests below can keep using distinctive
  # placeholder values instead of reusing a real repo owner name.
  - name: global-fallback
  - name: orphan-agent
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
    assert (
        resolve_agent_identity(project, backend_cwd, explicit="cli-passed-agent")
        == "cli-passed-agent"
    )


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


def test_malformed_platform_yaml_does_not_crash(project: Path, monkeypatch) -> None:
    """Bad YAML should fall back to current-agent, not raise.

    We monkeypatch the .otaman walk to return None so this test exercises
    the platform.yaml → current-agent fallback path specifically.
    """
    import otaman_cli.identity as _id_mod

    monkeypatch.setattr(_id_mod, "_read_otaman_agent_field", lambda cwd: None)
    (project / "platform.yaml").write_text("not: valid: yaml: ::: [", encoding="utf-8")
    (project / ".agents" / "current-agent").write_text("rescue-agent\n")
    assert resolve_agent_identity(project, project.parent / "auth-service") == "rescue-agent"


def test_repo_missing_owner_field_skipped(project: Path, monkeypatch) -> None:
    """A repo entry without `owner` shouldn't match — fall through to next or fallback.

    We monkeypatch the .otaman walk to return None so this test exercises
    the platform.yaml → current-agent fallback path specifically.
    """
    import otaman_cli.identity as _id_mod

    monkeypatch.setattr(_id_mod, "_read_otaman_agent_field", lambda cwd: None)
    (project / "platform.yaml").write_text(
        """
project: myplatform
version: "1.0"
agents:
  - name: default-fallback
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
    # web-app still resolves correctly (via platform.yaml, walk is mocked out)
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


# ---------------------------------------------------------------------------
# Worktree-aware identity — added 2026-05-14 (Spec C: Claude Code interop)
# ---------------------------------------------------------------------------
#
# When an agent works in a linked worktree of a managed repo, the worktree
# directory is OUTSIDE the repo path declared in platform.yaml. The old
# resolver walked cwd → repo paths and missed the match, so ownership
# rules silently fell through to the project-global current-agent
# fallback. The fix: resolve_agent_identity now also tries matching the
# worktree's main-repo path before falling through.
#
# Anthropic explicitly recommends worktrees for parallel sessions; without
# this, otaman's PreToolUse ownership hook denies legitimate writes from
# any worktree.


def _make_worktree(main_repo: Path, name: str, sibling_dir: Path) -> Path:
    """Create a linked worktree pointing into ``main_repo``.

    Returns the worktree's working-tree path (where the agent would cwd).
    """
    gitdir = main_repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True, exist_ok=True)
    worktree = sibling_dir / f"{main_repo.name}-{name}"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return worktree


def test_cwd_in_worktree_of_owned_repo_resolves_owner(project: Path) -> None:
    """CWD in a worktree of auth-service should resolve to backend-agent."""
    # Stale global identity must NOT win over worktree-aware match.
    (project / ".agents" / "current-agent").write_text("stale-frontend-agent\n")
    # Mark auth-service as a git repo so the worktree marker can point in.
    auth_main = project.parent / "auth-service"
    (auth_main / ".git").mkdir()
    worktree = _make_worktree(auth_main, "feature-login", project.parent)
    assert resolve_agent_identity(project, worktree) == "backend-agent"


def test_cwd_nested_in_worktree_resolves_owner(project: Path) -> None:
    """CWD in a subdir of a worktree should still resolve to the main owner."""
    auth_main = project.parent / "auth-service"
    (auth_main / ".git").mkdir()
    worktree = _make_worktree(auth_main, "feature-2fa", project.parent)
    nested = worktree / "src" / "auth"
    nested.mkdir(parents=True)
    assert resolve_agent_identity(project, nested) == "backend-agent"


def test_worktrees_of_different_repos_resolve_to_different_owners(project: Path) -> None:
    """Two worktrees, two repos — each must get its own owner."""
    auth_main = project.parent / "auth-service"
    web_main = project.parent / "web-app"
    (auth_main / ".git").mkdir()
    (web_main / ".git").mkdir()
    auth_wt = _make_worktree(auth_main, "feat-a", project.parent)
    web_wt = _make_worktree(web_main, "feat-b", project.parent)
    assert resolve_agent_identity(project, auth_wt) == "backend-agent"
    assert resolve_agent_identity(project, web_wt) == "frontend-agent"


def test_cwd_in_orphan_worktree_falls_back_to_current_agent(project: Path) -> None:
    """Worktree of a repo NOT in platform.yaml → falls through to current-agent."""
    (project / ".agents" / "current-agent").write_text("orphan-agent\n")
    unmanaged = project.parent / "unmanaged-repo"
    unmanaged.mkdir()
    (unmanaged / ".git").mkdir()
    worktree = _make_worktree(unmanaged, "feature-z", project.parent)
    assert resolve_agent_identity(project, worktree) == "orphan-agent"


def test_direct_cwd_match_still_preferred_over_worktree_logic(project: Path) -> None:
    """When cwd IS the managed repo directly, no worktree lookup is needed.

    Regression guard: make sure the worktree path didn't accidentally
    override the original cwd-match path for the normal (non-worktree) case.
    """
    (project / ".agents" / "current-agent").write_text("stale-agent\n")
    backend_cwd = project.parent / "auth-service"
    assert resolve_agent_identity(project, backend_cwd) == "backend-agent"
