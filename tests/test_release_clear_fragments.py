"""`otaman release clear-fragments` — the manifest is the authority (rnsc 1.2).

These tests are mostly about what the command REFUSES. Clearing fragments is a
delete, and the failure mode that matters is not "failed to delete" but "deleted
something the cut never consumed" — unreleased copy, gone silently, with the
next cut none the wiser. So the glob/traversal/protected refusals and the
content-hash skip get the coverage, not the happy path.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from otaman_cli.commands.release import cmd_release

pytest.importorskip("otaman_core.changelog_manifest")

from otaman_core.changelog_manifest import (  # noqa: E402
    build_manifest,
    fragment_hash,
    record_fragment,
    to_dict,
)

REPO = "otaman-cli"


def _repo(tmp_path):
    """A git repo with a changelog.d/ and a README that must survive."""
    root = tmp_path / REPO
    (root / "changelog.d").mkdir(parents=True)
    (root / "changelog.d" / "README.md").write_text("convention note\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


def _fragment(root, name, body):
    (root / "changelog.d" / name).write_text(body, encoding="utf-8")
    return record_fragment(REPO, name, body)


def _manifest_file(tmp_path, *fragments, release="v1.2.0"):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(to_dict(build_manifest(release, fragments))), encoding="utf-8")
    return path


def _run(root, monkeypatch, *argv):
    monkeypatch.chdir(root)
    return cmd_release(["clear-fragments", *argv])


def test_clears_exactly_the_manifest_named_files(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    consumed = _fragment(root, "101.feature.md", "shipped thing\n")
    _fragment(root, "102.fix.md", "NOT in the manifest\n")
    mf = _manifest_file(tmp_path, consumed)

    assert _run(root, monkeypatch, str(mf), "--repo", REPO, "--no-commit") == 0

    assert not (root / "changelog.d" / "101.feature.md").exists()
    # The unconsumed fragment and the convention note both survive — gate 3.1.
    assert (root / "changelog.d" / "102.fix.md").exists()
    assert (root / "changelog.d" / "README.md").exists()


def test_fragment_edited_since_the_cut_is_kept_not_deleted(tmp_path, monkeypatch):
    """The guard that matters: its current text was never released."""
    root = _repo(tmp_path)
    consumed = _fragment(root, "101.feature.md", "as consumed\n")
    mf = _manifest_file(tmp_path, consumed)
    (root / "changelog.d" / "101.feature.md").write_text("rewritten after the cut\n", "utf-8")

    assert _run(root, monkeypatch, str(mf), "--repo", REPO, "--no-commit") == 0
    assert (root / "changelog.d" / "101.feature.md").read_text() == "rewritten after the cut\n"


def test_force_deletes_the_changed_fragment(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    consumed = _fragment(root, "101.feature.md", "as consumed\n")
    mf = _manifest_file(tmp_path, consumed)
    (root / "changelog.d" / "101.feature.md").write_text("rewritten\n", "utf-8")

    assert _run(root, monkeypatch, str(mf), "--repo", REPO, "--no-commit", "--force") == 0
    assert not (root / "changelog.d" / "101.feature.md").exists()


@pytest.mark.parametrize(
    "bad",
    ["*.md", "10?.fix.md", "[0-9].md", "../outside.md", "nested/101.md", "README.md"],
)
def test_refuses_patterns_traversal_and_protected_names(tmp_path, monkeypatch, bad):
    """One forged entry invalidates the manifest — nothing is deleted.

    The forged row is written straight into the manifest JSON rather than built
    with ``record_fragment``, because that helper basenames its input: going
    through it would sanitize the traversal before the command ever saw it and
    the test would pass without the guard existing. A hand-edited manifest is
    the actual threat, and ``from_dict`` does not basename.
    """
    root = _repo(tmp_path)
    safe = _fragment(root, "101.feature.md", "real\n")
    mf = tmp_path / "manifest.json"
    data = to_dict(build_manifest("v1.2.0", [safe]))
    data["fragments"][REPO].append({"filename": bad, "sha256": "deadbeef"})
    mf.write_text(json.dumps(data), encoding="utf-8")

    assert _run(root, monkeypatch, str(mf), "--repo", REPO, "--no-commit") == 2
    # Refusal is whole-run: the legitimate entry is untouched too.
    assert (root / "changelog.d" / "101.feature.md").exists()
    assert (root / "changelog.d" / "README.md").exists()


def test_dry_run_deletes_nothing(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    consumed = _fragment(root, "101.feature.md", "shipped\n")
    mf = _manifest_file(tmp_path, consumed)

    assert _run(root, monkeypatch, str(mf), "--repo", REPO, "--no-commit", "--dry-run") == 0
    assert (root / "changelog.d" / "101.feature.md").exists()


def test_other_repos_entries_are_not_touched(tmp_path, monkeypatch):
    """An owner clears only its own repo's rows, never the whole manifest."""
    root = _repo(tmp_path)
    mine = _fragment(root, "101.feature.md", "mine\n")
    theirs = record_fragment("otaman-runner", "101.feature.md", "theirs\n")
    mf = _manifest_file(tmp_path, mine, theirs)

    assert _run(root, monkeypatch, str(mf), "--repo", "otaman-runner", "--no-commit") == 0
    assert (root / "changelog.d" / "101.feature.md").read_text() == "mine\n"


def test_unknown_repo_reports_what_the_manifest_holds(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    mf = _manifest_file(tmp_path, record_fragment("otaman-runner", "9.fix.md", "x"))

    assert _run(root, monkeypatch, str(mf), "--repo", "otaman-nope", "--no-commit") == 0
    assert "otaman-runner" in capsys.readouterr().out


def test_commits_the_clear_with_a_conventional_message(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    consumed = _fragment(root, "101.feature.md", "shipped\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
    mf = _manifest_file(tmp_path, consumed, release="v9.9.9")

    assert _run(root, monkeypatch, str(mf), "--repo", REPO) == 0

    log = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--pretty=%s"],
        capture_output=True,
        text=True,
    ).stdout
    assert log.startswith("chore(changelog): clear fragments consumed by v9.9.9")
    assert not (root / "changelog.d" / "101.feature.md").exists()


def test_missing_manifest_refuses_with_usage_code(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    assert _run(root, monkeypatch, str(tmp_path / "nope.json"), "--repo", REPO) == 2


def test_hash_recorded_is_the_content_hash(tmp_path):
    """Sanity on the shared helper the skip depends on."""
    assert record_fragment(REPO, "1.fix.md", "body\n").content_hash == fragment_hash("body\n")
