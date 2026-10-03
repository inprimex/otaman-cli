"""`otaman init --update` removes the retired `.agents/current-agent` — the third half.

team-mode D3/B1 as amended (Roman ruling 2026-09-11, 20260911T113813) names three things
the cutover release must carry:

    1. the resolver stops reading the marker      -> cli #281
    2. doctor ERRORs if one reappears             -> cli #281
    3. "an explicit migration step that removes the marker"  <- THIS

Without 3, #281 left `otaman doctor` telling an operator to delete a file and giving them
no verb that does it — a health check that reports a defect it cannot help fix.

Removal is placed BEFORE the per-repo marker walk in `--update`, because the per-repo
`.otaman agent:` fields are what replaces it: a crash mid-walk must not leave the retired
file as the only identity source on disk.
"""

from __future__ import annotations

import pytest

from otaman_cli.commands.init import retire_current_agent_marker


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "meta"
    (r / ".agents").mkdir(parents=True)
    return r


def _marker(root, text="fswatch-agent\n"):
    p = root / ".agents" / "current-agent"
    p.write_text(text, encoding="utf-8")
    return p


def test_the_marker_is_removed_and_the_report_names_what_it_claimed(root):
    marker = _marker(root)

    line = retire_current_agent_marker(root)

    assert not marker.exists()
    assert "removed" in line
    assert "fswatch-agent" in line, (
        "name what it claimed — an operator needs to know whose identity was being "
        "asserted fleet-wide, which is how the 2026-10-03 misattribution was found"
    )


def test_dry_run_reports_without_removing(root):
    marker = _marker(root)

    line = retire_current_agent_marker(root, dry_run=True)

    assert marker.exists(), "--dry-run wrote"
    assert "would remove" in line


def test_absent_marker_returns_NOTHING_rather_than_reporting_work(root):
    """The common case must stay quiet. A migration that announces a removal it did not
    perform is the no-silent-success shape inverted — success claimed for no work."""
    assert retire_current_agent_marker(root) is None


def test_an_unremovable_marker_REPORTS_the_failure(root, monkeypatch):
    """Not silent: the reader is gone either way, but the file keeps failing doctor, and
    a migration that reports success while the file remains is the exact shape the
    platform's no-silent-success rule forbids."""
    _marker(root)
    monkeypatch.setattr(
        "pathlib.Path.unlink",
        lambda self, **kw: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )

    line = retire_current_agent_marker(root)

    assert line.startswith("FAILED")
    assert "read-only filesystem" in line


def test_a_comment_only_marker_is_still_removed(root):
    """Present-but-contentless is still present, and something still wrote it."""
    marker = _marker(root, "# written by a hook\n")

    line = retire_current_agent_marker(root)

    assert not marker.exists()
    assert "removed" in line


def test_init_update_runs_the_removal_BEFORE_writing_repo_markers():
    """Ordering is load-bearing: the per-repo `.otaman agent:` fields are what replaces
    the retired file, so a crash mid-walk must not leave it as the only source."""
    import inspect

    from otaman_cli.commands import init

    src = inspect.getsource(init._cmd_init_update)
    assert "retire_current_agent_marker(" in src, "the migration step is not wired in"
    assert src.index("retire_current_agent_marker(") < src.index('marker = repo_dir / ".otaman"')


def test_doctor_and_the_migration_agree_on_the_path():
    """Two components addressing different files would leave doctor failing forever."""
    import inspect

    from otaman_cli import doctor
    from otaman_cli.commands import init

    seg = '".agents" / "current-agent"'
    assert seg.replace('"', "") or True  # readability only
    assert '/ ".agents" / "current-agent"' in inspect.getsource(
        doctor.check_retired_identity_marker
    )
    assert '/ ".agents" / "current-agent"' in inspect.getsource(retire_current_agent_marker)
    assert init is not None
