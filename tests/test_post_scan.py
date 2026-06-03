"""Tests for the post-scan UX hardening (otaman-scan-ux-hardening cli-side).

Covers:
- analyze_draft detects each gap independently
- scaffold_specs_repo creates the directory + git repo + skeleton files
- scaffold_openspec lays out a minimal openspec/ directory
- update_draft adds specs entry, launcher block, flips specs.format=openspec
- run() in non-TTY mode skips prompts but still emits launcher block if missing
- run() lifts an existing empty -specs/ sibling without prompting
- run() does nothing when the draft is already complete
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml as _pyyaml

from otaman_cli.onboard.post_scan import (
    PostScanGaps,
    analyze_draft,
    run,
    scaffold_openspec,
    scaffold_specs_repo,
    update_draft,
)


def _write_draft(path: Path, **overrides) -> None:
    doc = {
        "project": "myprog",
        "repos": [
            {"name": "myprog-backend", "path": "./myprog-backend", "owner": "backend-agent"},
        ],
        "specs": {"format": "fallback"},
    }
    doc.update(overrides)
    path.write_text(_pyyaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# analyze_draft


def test_analyze_detects_all_four_gaps(tmp_path: Path):
    scan_root = tmp_path
    (scan_root / "myprog-backend").mkdir()
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)
    gaps = analyze_draft(draft, scan_root, "myprog")
    assert gaps.specs_repo_missing is True
    assert gaps.specs_repo_unrecognised_path is None
    assert gaps.launcher_block_missing is True
    assert gaps.openspec_missing is True


def test_analyze_detects_unrecognised_existing_specs_dir(tmp_path: Path):
    scan_root = tmp_path
    (scan_root / "myprog-specs").mkdir()
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)  # no specs in repos[]
    gaps = analyze_draft(draft, scan_root, "myprog")
    assert gaps.specs_repo_missing is False
    assert gaps.specs_repo_unrecognised_path == scan_root / "myprog-specs"


def test_analyze_clean_when_draft_complete(tmp_path: Path):
    scan_root = tmp_path
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(
        draft,
        repos=[
            {"name": "myprog-backend", "path": "./myprog-backend", "owner": "backend-agent"},
            {"name": "myprog-specs", "path": "../myprog-specs", "owner": "spec-agent"},
        ],
        specs={"path": "../myprog-specs/openspec", "format": "openspec"},
        launcher={"local": {"enabled": True}},
    )
    gaps = analyze_draft(draft, scan_root, "myprog")
    assert gaps.any() is False


def test_analyze_returns_empty_when_draft_missing(tmp_path: Path):
    gaps = analyze_draft(tmp_path / "no-such-file.yaml", tmp_path, "x")
    assert gaps.any() is False


# ---------------------------------------------------------------------------
# scaffold_specs_repo


def test_scaffold_specs_creates_files_and_git_repo(tmp_path: Path):
    target = tmp_path / "myprog-specs"
    scaffold_specs_repo(target, "myprog", "MyProg")
    assert (target / "CLAUDE.md").is_file()
    assert (target / "README.md").is_file()
    assert (target / ".gitignore").is_file()
    assert "MyProg" in (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert "spec-agent" in (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert (target / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(target),
        capture_output=True, text=True,
    )
    assert log.returncode == 0
    lines = [l for l in log.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    assert "scaffold: initialize myprog-specs" in lines[0]


def test_scaffold_specs_refuses_if_target_exists(tmp_path: Path):
    target = tmp_path / "myprog-specs"
    target.mkdir()
    with pytest.raises(FileExistsError):
        scaffold_specs_repo(target, "myprog", "MyProg")


# ---------------------------------------------------------------------------
# scaffold_openspec


def test_scaffold_openspec_creates_minimal_layout(tmp_path: Path):
    specs_repo = tmp_path / "myprog-specs"
    specs_repo.mkdir()
    openspec = scaffold_openspec(specs_repo)
    assert openspec == specs_repo / "openspec"
    assert (openspec / ".openspec.yaml").is_file()
    assert (openspec / "README.md").is_file()
    assert (openspec / "specs" / ".gitkeep").is_file()
    assert (openspec / "changes" / ".gitkeep").is_file()


def test_scaffold_openspec_returns_existing_path_idempotently(tmp_path: Path):
    specs_repo = tmp_path / "myprog-specs"
    specs_repo.mkdir()
    (specs_repo / "openspec").mkdir()
    # Existing — function should not raise
    result = scaffold_openspec(specs_repo)
    assert result == specs_repo / "openspec"


# ---------------------------------------------------------------------------
# update_draft


def test_update_draft_adds_specs_repo_but_skips_launcher_until_schema_ready(tmp_path: Path):
    """Schema in otaman-core doesn't yet accept top-level `launcher:`. Until
    core-agent extends it, post_scan must NOT emit the block (would fail
    `otaman init` validation). The detection path stays for diagnostics."""
    draft = tmp_path / "platform.yaml.draft"
    _write_draft(draft)
    update_draft(
        draft,
        add_specs_repo={
            "name": "myprog-specs", "path": "../myprog-specs",
            "owner": "spec-agent", "description": "specs",
        },
        add_launcher=True,  # caller still requests it
    )
    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    names = [r["name"] for r in doc["repos"]]
    assert "myprog-specs" in names
    # Launcher block NOT emitted — schema-gated
    assert "launcher" not in doc
    assert doc["specs"]["path"] == "../myprog-specs"


def test_update_draft_flips_specs_format_to_openspec(tmp_path: Path):
    draft = tmp_path / "platform.yaml.draft"
    _write_draft(draft)
    openspec_dir = tmp_path.parent / "myprog-specs" / "openspec"
    update_draft(draft, set_specs_format_openspec=openspec_dir)
    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    assert doc["specs"]["format"] == "openspec"


def test_update_draft_does_not_duplicate_specs_repo(tmp_path: Path):
    draft = tmp_path / "platform.yaml.draft"
    _write_draft(draft, repos=[
        {"name": "myprog-specs", "path": "../myprog-specs", "owner": "spec-agent"},
    ])
    update_draft(draft, add_specs_repo={
        "name": "myprog-specs", "path": "../myprog-specs",
        "owner": "spec-agent", "description": "x",
    })
    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    spec_entries = [r for r in doc["repos"] if r["name"] == "myprog-specs"]
    assert len(spec_entries) == 1


# ---------------------------------------------------------------------------
# run() — non-TTY path


def test_run_non_tty_skips_scaffolding_and_holds_launcher_for_schema(tmp_path: Path):
    scan_root = tmp_path
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)
    result = run(
        draft_path=draft, scan_root=scan_root, otaman_dir=otaman_dir,
        program_slug="myprog", interactive=False,
    )
    # No prompts, no scaffolding
    assert result.specs_repo_created is None
    assert result.openspec_scaffolded is None
    # Launcher emission is currently schema-gated (see update_draft note);
    # the gap is detected but the block is NOT written.
    assert result.launcher_block_added is False
    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    assert "launcher" not in doc
    # And the skip reasons are recorded
    assert any("non-TTY" in s for s in result.skipped)


def test_run_lifts_unrecognised_specs_dir_without_prompting(tmp_path: Path):
    scan_root = tmp_path
    (scan_root / "myprog-specs").mkdir()
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)
    result = run(
        draft_path=draft, scan_root=scan_root, otaman_dir=otaman_dir,
        program_slug="myprog", interactive=False,
    )
    assert result.specs_repo_lifted == scan_root / "myprog-specs"
    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    names = [r["name"] for r in doc["repos"]]
    assert "myprog-specs" in names


def test_run_noop_when_draft_already_complete(tmp_path: Path):
    scan_root = tmp_path
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(
        draft,
        repos=[
            {"name": "myprog-backend", "path": "./myprog-backend", "owner": "backend-agent"},
            {"name": "myprog-specs", "path": "../myprog-specs", "owner": "spec-agent"},
        ],
        specs={"path": "../myprog-specs/openspec", "format": "openspec"},
        launcher={"local": {"enabled": True}},
    )
    before = draft.read_text(encoding="utf-8")
    result = run(
        draft_path=draft, scan_root=scan_root, otaman_dir=otaman_dir,
        program_slug="myprog", interactive=False,
    )
    after = draft.read_text(encoding="utf-8")
    assert before == after  # no mutations
    assert result.specs_repo_created is None
    assert result.specs_repo_lifted is None
    assert result.launcher_block_added is False
    assert result.openspec_scaffolded is None


# ---------------------------------------------------------------------------
# run() — interactive path (mocked input)


def test_run_interactive_scaffolds_specs_and_openspec(tmp_path: Path, monkeypatch):
    scan_root = tmp_path
    (scan_root / "myprog-backend").mkdir()  # one regular repo
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)

    # Two prompts: create specs? (Y) then scaffold openspec? (Y)
    answers = iter(["y", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = run(
        draft_path=draft, scan_root=scan_root, otaman_dir=otaman_dir,
        program_slug="myprog", interactive=True,
    )
    assert result.specs_repo_created == scan_root / "myprog-specs"
    assert (scan_root / "myprog-specs" / "CLAUDE.md").is_file()
    assert result.openspec_scaffolded == scan_root / "myprog-specs" / "openspec"
    assert (scan_root / "myprog-specs" / "openspec" / ".openspec.yaml").is_file()
    # Launcher is schema-gated — not emitted yet
    assert result.launcher_block_added is False

    doc = _pyyaml.safe_load(draft.read_text(encoding="utf-8"))
    assert any(r["name"] == "myprog-specs" for r in doc["repos"])
    assert doc["specs"]["format"] == "openspec"
    assert "launcher" not in doc


def test_run_interactive_user_declines_specs(tmp_path: Path, monkeypatch):
    scan_root = tmp_path
    otaman_dir = scan_root / "myprog-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    _write_draft(draft)

    monkeypatch.setattr("builtins.input", lambda _: "n")
    result = run(
        draft_path=draft, scan_root=scan_root, otaman_dir=otaman_dir,
        program_slug="myprog", interactive=True,
    )
    assert result.specs_repo_created is None
    assert any("declined" in s for s in result.skipped)
    # Launcher schema-gated — not added even though gap is detected
    assert result.launcher_block_added is False
