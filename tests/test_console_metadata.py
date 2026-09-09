"""console-ux-redesign wave 1, task 1.4 — artifact ownership + tracking metadata (S9/S10).

Every artifact row/detail shows creator + when, priority inherited from the
linked outcome, a reserved deadline slot, and — when pm-sync is enabled — the
linked issue id. These cover the readers (pm-sync file, git first-commit fallback
to .openspec.yaml, priority inheritance) and the detail-line rendering.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from otaman_cli.console import bus, metadata


@pytest.fixture
def program(tmp_path):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "specs" / "openspec" / "changes").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\nspecs:\n  path: specs\n", encoding="utf-8"
    )
    return bus.Program(name="demo", root=root)


def _change(program, name, *, openspec=None, pm_sync=None):
    d = program.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    (d / ".openspec.yaml").write_text(yaml.safe_dump(openspec or {"stage": "authored"}), "utf-8")
    if pm_sync is not None:
        (d / ".pm-sync.yaml").write_text(yaml.safe_dump(pm_sync), "utf-8")
    return d


# ---------------------------------------------------------------------------
# read_pm_sync


def test_read_pm_sync_present(program):
    d = _change(program, "c", pm_sync={"provider": "easy8", "change_issue_id": 4242})
    issue, provider = metadata.read_pm_sync(d)
    assert issue == "4242" and provider == "easy8"


def test_read_pm_sync_absent(program):
    d = _change(program, "c")
    assert metadata.read_pm_sync(d) == (None, None)


# ---------------------------------------------------------------------------
# change_metadata — creator/when fallback to .openspec.yaml, deadline reserved


def test_metadata_falls_back_to_openspec_when_no_git(program):
    _change(
        program,
        "c",
        openspec={"stage": "authored", "created": "2026-09-01", "spec_owner": "spec-agent"},
    )
    meta = metadata.change_metadata(program, "c")
    assert meta.created == "2026-09-01"
    assert meta.creator == "spec-agent"
    assert meta.deadline is None  # RESERVED — never a fabricated value


def test_metadata_prefers_git_first_commit(program):
    _change(program, "c", openspec={"created": "2026-09-01", "spec_owner": "spec-agent"})
    root = program.root

    def _run(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)

    _run("init", "-q")
    _run("config", "user.email", "roman@x.io")
    _run("config", "user.name", "roman")
    _run("add", "-A")
    _run("commit", "-q", "-m", "seed", "--date=2026-08-15T00:00:00")
    meta = metadata.change_metadata(program, "c")
    # git first-commit author wins over .openspec spec_owner
    assert meta.creator == "roman"


def test_pm_sync_id_surfaces_on_change(program):
    _change(program, "c", pm_sync={"provider": "easy8", "change_issue_id": "GB-77"})
    assert metadata.change_metadata(program, "c").pm_sync_id == "GB-77"


# ---------------------------------------------------------------------------
# metadata_lines rendering


def test_metadata_lines_reserve_deadline_and_show_pm():
    meta = metadata.ChangeMeta(
        creator="roman",
        created="2026-09-01",
        priority="P0",
        pm_sync_id="GB-77",
        pm_sync_provider="easy8",
    )
    lines = metadata.metadata_lines(meta)
    joined = "\n".join(lines)
    assert "creator: roman" in joined and "created: 2026-09-01" in joined
    assert "priority: P0" in joined
    assert "deadline: (reserved)" in joined  # slot present, never a fake value
    assert "pm-sync: GB-77 (easy8)" in joined


def test_metadata_lines_omit_pm_when_absent():
    lines = metadata.metadata_lines(metadata.ChangeMeta())
    assert not any("pm-sync" in line for line in lines)
    assert any("deadline: (reserved)" in line for line in lines)
