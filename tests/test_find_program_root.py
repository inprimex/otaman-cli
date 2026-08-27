"""repo-materialization gate note 1 — resolve from the PROGRAM dir.

`otaman sync-repos` / `otaman doctor` are naturally launched from
``orgs/<org>/programs/<program>/`` (no marker of its own, but it CONTAINS the
program meta). `find_program_root` adds a single-child fallback so those tools
resolve there instead of erroring "Not in an otaman project".
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli import identity


def _meta(base: Path, name: str, project: str = "p") -> Path:
    d = base / name
    (d / ".agents").mkdir(parents=True)
    (d / "platform.yaml").write_text(
        f"project: {project}\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return d


def test_single_child_finds_one_meta(tmp_path):
    progdir = tmp_path / "programs" / "prog"
    progdir.mkdir(parents=True)
    meta = _meta(progdir, "prog-meta")
    (progdir / "repo").mkdir()  # a non-meta sibling (a checked-out repo)
    assert identity._single_child_program_root(progdir) == meta.resolve()


def test_single_child_none_when_absent(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "repo").mkdir()
    assert identity._single_child_program_root(d) is None


def test_single_child_none_when_ambiguous(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    _meta(d, "a", project="a")
    _meta(d, "b", project="b")
    assert identity._single_child_program_root(d) is None  # don't guess


def test_single_child_skips_projectless_platform_yaml(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "org").mkdir()
    (d / "org" / "platform.yaml").write_text("models: {}\n", encoding="utf-8")  # no project
    assert identity._single_child_program_root(d) is None


def test_single_child_ignores_repo_with_platform_yaml_but_no_bus(tmp_path):
    # The real program dir holds the meta AND a repo (e.g. otaman-deploy) that
    # ships its own platform.yaml with a project but has no `.agents/` bus.
    # Only the bus-bearing meta counts, so this is NOT ambiguous.
    progdir = tmp_path / "programs" / "prog"
    progdir.mkdir(parents=True)
    meta = _meta(progdir, "prog-meta")  # has .agents
    repo = progdir / "some-repo"
    repo.mkdir()
    (repo / "platform.yaml").write_text(  # project but NO bus → not a meta
        "project: some-repo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    assert identity._single_child_program_root(progdir) == meta.resolve()


def test_find_program_root_uses_fallback_from_program_dir(tmp_path, monkeypatch):
    # No OTAMAN_ROOT → standard resolution returns None from the program dir,
    # so the child fallback resolves the meta (the real Roman scenario).
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    progdir = tmp_path / "programs" / "prog"
    progdir.mkdir(parents=True)
    meta = _meta(progdir, "prog-meta")
    assert identity.find_project_root(progdir) is None  # standard resolution finds nothing
    assert identity.find_program_root(progdir) == meta.resolve()


def test_find_program_root_prefers_standard_resolution(tmp_path, monkeypatch):
    # When standard resolution yields a real program root, it's returned as-is
    # (the child fallback is not needed).
    meta = _meta(tmp_path, "resolved-meta")  # real program root (project + bus)
    monkeypatch.setattr(identity, "find_project_root", lambda start=None: meta)
    assert identity.find_program_root(tmp_path) == meta


def test_find_program_root_falls_back_from_projectless_org_dir(tmp_path, monkeypatch):
    # The real gate-note-1 scenario: standard walk-up resolves to a stale
    # ORG-LEVEL dir (platform.yaml without project). That's not a program root,
    # so the single-child fallback from the launch dir wins.
    progdir = tmp_path / "programs" / "prog"
    progdir.mkdir(parents=True)
    meta = _meta(progdir, "prog-meta")
    org_dir = tmp_path / "org"
    org_dir.mkdir()
    (org_dir / "platform.yaml").write_text("models: {}\n", encoding="utf-8")  # no project
    monkeypatch.setattr(identity, "find_project_root", lambda start=None: org_dir)
    assert identity.find_program_root(progdir) == meta.resolve()
