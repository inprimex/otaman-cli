"""Acceptance tests for `otaman project add` + `remove --delete-remote`
(project-add-and-delete-remote 1.3).

Covers the eight cases task 1.3 names: add with token; add without token;
target dir exists; init rollback; `git_host.org:` as default --org;
`--provider gitlab` / unsupported routing; delete-remote TTY confirm;
delete-remote non-TTY reject.

The git-host adapter is MOCKED throughout — no provider API is ever
called. `monkeypatch` (never bare module assignment) does every patch, so
nothing leaks into later test files.

Fixtures are staged in-file rather than imported from
tests/test_project_command.py: a cross-test import passes locally and
fails CI collection.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from otaman_cli.project import cmd_add, cmd_remove
from otaman_cli.project._platform import find_repo, load_platform_yaml, save_platform_yaml

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def meta(tmp_path: Path) -> Path:
    """Minimal program: `<tmp>/parent/meta` with platform.yaml, git-inited.

    Note the nested `parent/` — tmp_path itself is never git-inited, because
    pytest gives sibling tests a shared parent and init's preflight scans it.
    """
    parent = tmp_path / "parent"
    parent.mkdir()
    m = parent / "meta"
    m.mkdir()
    (m / ".agents").mkdir()
    save_platform_yaml(
        m,
        {
            "project": "testprog",
            "repos": [
                {"name": "existing-svc", "path": "../existing-svc", "owner": "backend-agent"},
            ],
        },
    )
    existing = parent / "existing-svc"
    existing.mkdir()
    (existing / ".git").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(m), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "platform.yaml"],
        cwd=str(m),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=str(m),
        check=True,
        capture_output=True,
    )
    return m


@dataclass
class FakeRepoInfo:
    name: str
    owner: str
    clone_url: str
    ssh_url: str
    html_url: str
    private: bool


@dataclass
class FakeAdapter:
    """Records what it was asked to do; performs no I/O."""

    created: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    create_error: Exception | None = None
    delete_error: Exception | None = None

    def create_repo(
        self,
        name: str,
        org: str | None,
        private: bool = True,
        description: str = "",
    ) -> FakeRepoInfo:
        self.created.append(
            {"name": name, "org": org, "private": private, "description": description}
        )
        if self.create_error:
            raise self.create_error
        slug_owner = org or "testuser"
        return FakeRepoInfo(
            name=name,
            owner=slug_owner,
            clone_url=f"https://github.com/{slug_owner}/{name}.git",
            ssh_url=f"git@github.com:{slug_owner}/{name}.git",
            html_url=f"https://github.com/{slug_owner}/{name}",
            private=private,
        )

    def delete_repo(self, owner: str, name: str) -> None:
        self.deleted.append((owner, name))
        if self.delete_error:
            raise self.delete_error


def _real_config(org: str | None = None, provider: str = "github") -> Any:
    """A genuine GitHostConfig — `_resolve_adapter` calls dataclasses.replace()
    on it for --provider, so a stand-in dict would not exercise that path."""
    from otaman_core.git_host import GitHostConfig

    block: dict[str, Any] = {
        "provider": provider,
        "token": {"sources": [{"type": "env", "name": "TEST_GIT_TOKEN"}]},
    }
    if org:
        block["org"] = org
    return GitHostConfig.from_dict(block)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, meta: Path):
    """Point cmd_add at *meta*, stub the adapter, and fake clone + init.

    Returns a small handle the tests read back assertions from.
    """
    import otaman_core.git_host as gh

    adapter = FakeAdapter()
    seen: dict[str, Any] = {"adapter": adapter, "cfg": None, "init_rc": 0}

    monkeypatch.setattr(cmd_add, "find_project_root", lambda: meta)
    monkeypatch.setattr(gh, "load_git_host_config", lambda root: _real_config())

    def _get_adapter(cfg, *, maestro_root=None):
        seen["cfg"] = cfg
        return adapter

    monkeypatch.setattr(gh, "get_adapter", _get_adapter)

    def _fake_clone(clone_url: str, target: Path) -> tuple[int, str]:
        seen["clone_url"] = clone_url
        target.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(target), check=True)
        return 0, ""

    monkeypatch.setattr(cmd_add, "_git_clone", _fake_clone)

    import otaman_cli.commands.init as init_mod

    monkeypatch.setattr(init_mod, "_cmd_init_update", lambda: seen["init_rc"])
    return seen


# ---------------------------------------------------------------------------
# add — the happy paths


def test_add_with_token_creates_remote_clones_and_registers(wired, meta: Path):
    rc = cmd_add.cmd_project_add("my-service", owner="deploy-agent")
    assert rc == 0, "add should succeed when the adapter resolves"

    assert wired["adapter"].created == [
        {"name": "my-service", "org": None, "private": True, "description": ""}
    ]
    assert wired["clone_url"] == "https://github.com/testuser/my-service.git"
    assert (meta.parent / "my-service" / ".git").exists()

    entry = find_repo(load_platform_yaml(meta), "my-service")
    assert entry is not None
    assert entry["owner"] == "deploy-agent"
    assert entry["path"] == "../my-service"
    assert entry["remote"] == "https://github.com/testuser/my-service.git"
    assert entry["launch"]["commands"], "add must leave the repo launchable"


def test_add_commits_platform_yaml_with_the_conventional_message(wired, meta: Path):
    assert cmd_add.cmd_project_add("my-service", owner="deploy-agent") == 0
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=str(meta),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log == "feat(platform): add repo my-service (owner: deploy-agent)"


def test_add_without_token_creates_local_only_and_omits_remote(
    monkeypatch: pytest.MonkeyPatch, meta: Path, capsys
):
    """An unresolvable token is the local-only path, not a failure."""
    import otaman_core.git_host as gh

    import otaman_cli.commands.init as init_mod

    monkeypatch.setattr(cmd_add, "find_project_root", lambda: meta)
    monkeypatch.setattr(gh, "load_git_host_config", lambda root: _real_config())

    def _no_token(cfg, *, maestro_root=None):
        raise gh.GitHostError("git-host token could not be resolved")

    monkeypatch.setattr(gh, "get_adapter", _no_token)
    monkeypatch.setattr(init_mod, "_cmd_init_update", lambda: 0)

    rc = cmd_add.cmd_project_add("local-svc", owner="deploy-agent")
    assert rc == 0
    out = capsys.readouterr().out
    assert "No token found" in out

    target = meta.parent / "local-svc"
    assert (target / ".git").is_dir(), "local-only path must still git init"
    entry = find_repo(load_platform_yaml(meta), "local-svc")
    assert entry is not None
    assert "remote" not in entry, "no remote was created, so none may be recorded"


def test_add_uses_git_host_org_as_default_org(monkeypatch: pytest.MonkeyPatch, wired, meta: Path):
    """`git_host.org:` is the default --org (task 1.1)."""
    import otaman_core.git_host as gh

    monkeypatch.setattr(gh, "load_git_host_config", lambda root: _real_config(org="my-org"))
    assert cmd_add.cmd_project_add("org-svc", owner="deploy-agent") == 0
    assert wired["adapter"].created[0]["org"] == "my-org"


def test_add_explicit_org_flag_overrides_config(monkeypatch: pytest.MonkeyPatch, wired, meta: Path):
    import otaman_core.git_host as gh

    monkeypatch.setattr(gh, "load_git_host_config", lambda root: _real_config(org="my-org"))
    assert cmd_add.cmd_project_add("org-svc", owner="deploy-agent", org="other-org") == 0
    assert wired["adapter"].created[0]["org"] == "other-org"


# ---------------------------------------------------------------------------
# add — refusals and rollback


def test_add_existing_directory_errors_without_touching_platform_yaml(wired, meta: Path):
    (meta.parent / "taken").mkdir()
    before = (meta / "platform.yaml").read_text(encoding="utf-8")

    rc = cmd_add.cmd_project_add("taken", owner="deploy-agent")
    assert rc != 0
    assert (meta / "platform.yaml").read_text(encoding="utf-8") == before
    assert wired["adapter"].created == [], "must refuse before any remote call"


def test_add_duplicate_name_errors(wired, meta: Path):
    rc = cmd_add.cmd_project_add("existing-svc", owner="deploy-agent")
    assert rc != 0
    assert wired["adapter"].created == []


def test_add_without_owner_errors_with_usage(wired, meta: Path, capsys):
    rc = cmd_add.cmd_project_add("my-service", owner=None)
    assert rc != 0
    assert "--owner" in capsys.readouterr().out


def test_add_init_failure_rolls_back_entry_and_leaves_directory(wired, meta: Path):
    """Spec: entry removed, directory LEFT on disk, non-zero exit."""
    wired["init_rc"] = 3

    rc = cmd_add.cmd_project_add("doomed", owner="deploy-agent")
    assert rc != 0
    assert find_repo(load_platform_yaml(meta), "doomed") is None, "entry must be rolled back"
    assert (meta.parent / "doomed").is_dir(), "directory must be left for inspection"


def test_add_init_exception_also_rolls_back(monkeypatch: pytest.MonkeyPatch, wired, meta: Path):
    """An init that RAISES is the shape the spec names; it must roll back too."""
    import otaman_cli.commands.init as init_mod

    def _boom():
        raise RuntimeError("init exploded")

    monkeypatch.setattr(init_mod, "_cmd_init_update", _boom)

    rc = cmd_add.cmd_project_add("doomed", owner="deploy-agent")
    assert rc != 0
    assert find_repo(load_platform_yaml(meta), "doomed") is None
    assert (meta.parent / "doomed").is_dir()


def test_add_rollback_preserves_other_entries(wired, meta: Path):
    """Rolling back must drop only the new entry — not revert the file."""
    wired["init_rc"] = 3
    assert cmd_add.cmd_project_add("doomed", owner="deploy-agent") != 0
    data = load_platform_yaml(meta)
    assert find_repo(data, "existing-svc") is not None
    assert find_repo(data, "doomed") is None


def test_add_remote_creation_failure_leaves_platform_yaml_untouched(wired, meta: Path):
    import otaman_core.git_host as gh

    wired["adapter"].create_error = gh.GitHostError("422 name already exists")
    before = (meta / "platform.yaml").read_text(encoding="utf-8")

    rc = cmd_add.cmd_project_add("my-service", owner="deploy-agent")
    assert rc != 0
    assert (meta / "platform.yaml").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# add — provider routing


def test_add_provider_gitlab_routes_to_gitlab_host(
    monkeypatch: pytest.MonkeyPatch, wired, meta: Path
):
    """`--provider gitlab` must retarget BOTH provider and host — keeping
    github.com while switching provider would call GitLab's API at GitHub."""
    assert cmd_add.cmd_project_add("gl-svc", owner="deploy-agent", provider="gitlab") == 0
    cfg = wired["cfg"]
    assert cfg.provider == "gitlab"
    assert cfg.host == "gitlab.com"


def test_add_unsupported_provider_errors_clearly(wired, meta: Path, capsys):
    rc = cmd_add.cmd_project_add("x-svc", owner="deploy-agent", provider="sourceforge")
    assert rc != 0
    out = capsys.readouterr().out
    assert "Unsupported" in out
    assert "github" in out, "the error should name what IS supported"
    assert wired["adapter"].created == []


def test_add_self_hosted_provider_without_host_falls_back_to_local(wired, meta: Path, capsys):
    """gitea/forgejo have no SaaS default host, so the override cannot be
    honoured from platform.yaml alone — say that, don't guess a host."""
    rc = cmd_add.cmd_project_add("gt-svc", owner="deploy-agent", provider="gitea")
    assert rc == 0
    out = capsys.readouterr().out
    assert "self-hosted" in out
    assert "git_host.host" in out
    assert wired["adapter"].created == [], "no remote call without a resolved host"
    assert "remote" not in find_repo(load_platform_yaml(meta), "gt-svc")


# ---------------------------------------------------------------------------
# remove --delete-remote


@pytest.fixture
def removable(monkeypatch: pytest.MonkeyPatch, meta: Path):
    """A registered repo WITH a remote, plus a stubbed adapter."""
    import otaman_core.git_host as gh

    data = load_platform_yaml(meta)
    data["repos"].append(
        {
            "name": "doomed-svc",
            "path": "../doomed-svc",
            "owner": "backend-agent",
            "remote": "https://github.com/testorg/doomed-svc.git",
        }
    )
    save_platform_yaml(meta, data)

    adapter = FakeAdapter()
    monkeypatch.setattr(cmd_remove, "find_project_root", lambda: meta)
    monkeypatch.setattr(gh, "load_git_host_config", lambda root: _real_config(org="testorg"))
    monkeypatch.setattr(gh, "get_adapter", lambda cfg, *, maestro_root=None: adapter)
    return adapter


def _tty(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.setattr(cmd_remove.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: typed)


def test_delete_remote_tty_exact_name_deletes_and_deregisters(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path
):
    _tty(monkeypatch, "doomed-svc")
    rc = cmd_remove.cmd_project_remove("doomed-svc", delete_remote=True)
    assert rc == 0
    assert removable.deleted == [("testorg", "doomed-svc")]
    assert find_repo(load_platform_yaml(meta), "doomed-svc") is None


def test_delete_remote_tty_mismatch_aborts_with_nothing_changed(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path
):
    before = (meta / "platform.yaml").read_text(encoding="utf-8")
    _tty(monkeypatch, "doomed")  # near-miss: a prefix, not the name

    rc = cmd_remove.cmd_project_remove("doomed-svc", delete_remote=True)
    assert rc != 0
    assert removable.deleted == [], "a mismatch must not reach the provider"
    assert (meta / "platform.yaml").read_text(encoding="utf-8") == before


def test_delete_remote_non_tty_rejected_platform_yaml_unmodified(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path, capsys
):
    before = (meta / "platform.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(cmd_remove.sys.stdin, "isatty", lambda: False, raising=False)

    rc = cmd_remove.cmd_project_remove("doomed-svc", delete_remote=True)
    assert rc != 0
    assert "TTY" in capsys.readouterr().out
    assert removable.deleted == []
    assert (meta / "platform.yaml").read_text(encoding="utf-8") == before


def test_delete_remote_failure_keeps_the_repo_registered(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path
):
    """A stranded remote with no platform.yaml record is unfindable — so a
    failed deletion must leave the entry in place."""
    import otaman_core.git_host as gh

    removable.delete_error = gh.GitHostError("403 insufficient scope")
    _tty(monkeypatch, "doomed-svc")

    rc = cmd_remove.cmd_project_remove("doomed-svc", delete_remote=True)
    assert rc != 0
    assert find_repo(load_platform_yaml(meta), "doomed-svc") is not None


def test_delete_remote_without_recorded_remote_refuses(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path
):
    """existing-svc has no `remote:` — there is nothing to delete, and
    silently deregistering instead would be a different act than asked for."""
    _tty(monkeypatch, "existing-svc")
    rc = cmd_remove.cmd_project_remove("existing-svc", delete_remote=True)
    assert rc != 0
    assert removable.deleted == []
    assert find_repo(load_platform_yaml(meta), "existing-svc") is not None


def test_remove_without_delete_remote_makes_no_api_call(
    monkeypatch: pytest.MonkeyPatch, removable, meta: Path
):
    """Plain remove never reaches the provider, remote recorded or not."""
    rc = cmd_remove.cmd_project_remove("doomed-svc")
    assert rc == 0
    assert removable.deleted == []
    assert find_repo(load_platform_yaml(meta), "doomed-svc") is None


# ---------------------------------------------------------------------------
# CLI surface — the flags actually reach the implementation


def test_cli_add_rejects_missing_owner_non_tty(meta: Path):
    """End-to-end through main.py: no --owner is a usage error, not a prompt."""
    import os

    env = {**os.environ, "OTAMAN_AGENT": "cli-agent"}
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(var, None)
    r = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "project", "add", "new-svc"],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
        input="",
    )
    assert r.returncode != 0
    assert "--owner" in (r.stdout + r.stderr)
    assert "not yet implemented" not in (r.stdout + r.stderr), "add is implemented now"
