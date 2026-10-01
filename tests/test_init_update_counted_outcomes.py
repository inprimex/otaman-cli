"""instruction-regeneration 1.1 — `init --update` accounts for what it regenerated.

The change was filed on a measured four-month silence (proposal, 20261001T075846):
all 18 CLAUDE.local.md on this fleet dated to 2026-08-28 while the generator had
taken 19 commits, and `otaman init --update` — the command that regenerates them —
said nothing about them. It printed `Updated: N`, which counts `.otaman` MARKERS,
plus a generator exit code. Two merged instruction fixes (plugin #92's retraction,
cli #211 removing the line it names) reached no running agent and nothing reported
it.

Four positions, each the opposite of a cheaper one:

* outcomes are measured from CONTENT, before and after — a generator that exits 0
  having written nothing is the exact state being fixed, and only a hash comparison
  catches it
* zero work is STATED, because printing nothing when nothing changed is how four
  months passed
* a repo with no directory is SKIPPED, not failed — the generator warns and moves
  on, and conflating the two makes every partial workspace look broken
* a file missing after a SUCCESSFUL generator run is a failure of the run, whatever
  the exit code said
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from otaman_cli import generated_instructions as gi

CONFIG = {
    "project": "demo",
    "repos": [
        {"name": "otaman-cli", "path": "otaman-cli"},
        {"name": "otaman-core", "path": "otaman-core"},
    ],
}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "meta"
    root.mkdir()
    for rel in ("otaman-cli", "otaman-core"):
        (tmp_path / rel).mkdir()
    # repos[].path is relative to the meta root, so point them at the siblings.
    config = {
        "project": "demo",
        "repos": [
            {"name": "otaman-cli", "path": "../otaman-cli"},
            {"name": "otaman-core", "path": "../otaman-core"},
        ],
    }
    return root, config, tmp_path


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    path = tmp_path / rel / gi.GENERATED_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The file set is derived, not hardcoded.


def test_the_expected_set_comes_from_repos_paths(workspace):
    """A fleet that grows a repo must grow the count with it — a hardcoded list
    would under-report the same way the silence did."""
    root, config, _tmp = workspace

    states = gi.expected_files(root, config)

    assert [s.repo for s in states] == ["otaman-cli", "otaman-core"]
    assert all(s.path.name == gi.GENERATED_FILENAME for s in states)


def test_a_repo_with_no_directory_is_marked_missing_not_failed(workspace):
    root, config, _tmp = workspace
    config["repos"].append({"name": "otaman-ghost", "path": "../otaman-ghost"})

    states = {s.repo: s for s in gi.expected_files(root, config)}

    assert states["otaman-ghost"].repo_missing is True
    assert states["otaman-cli"].repo_missing is False


def test_a_repo_with_no_path_is_skipped_entirely(workspace):
    """A malformed entry must not become a phantom file nothing will ever write."""
    root, config, _tmp = workspace
    config["repos"].append({"name": "nameless"})

    assert [s.repo for s in gi.expected_files(root, config)] == ["otaman-cli", "otaman-core"]


# ---------------------------------------------------------------------------
# Outcomes come from content.


def test_changed_bytes_count_as_refreshed(workspace):
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "old\n")
    _write(tmp, "otaman-core", "same\n")
    before = gi.expected_files(root, config)
    _write(tmp, "otaman-cli", "new\n")

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert outcome.refreshed == ["otaman-cli"]
    assert outcome.unchanged == ["otaman-core"]
    assert outcome.failed == []


def test_a_file_that_appears_counts_as_refreshed(workspace):
    root, config, tmp = workspace
    before = gi.expected_files(root, config)
    _write(tmp, "otaman-cli", "fresh\n")
    _write(tmp, "otaman-core", "fresh\n")

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert sorted(outcome.refreshed) == ["otaman-cli", "otaman-core"]


def test_a_file_missing_after_the_run_is_a_failure(workspace):
    """THE DEFECT, in its sharpest form: the generator exits 0 and the file it owns
    is not there. A skip here would reproduce the silence exactly."""
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "was here\n")
    _write(tmp, "otaman-core", "was here\n")
    before = gi.expected_files(root, config)
    (tmp / "otaman-cli" / gi.GENERATED_FILENAME).unlink()

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert [r for r, _ in outcome.failed] == ["otaman-cli"]
    assert "no CLAUDE.local.md after regeneration" in outcome.failed[0][1]


def test_the_identical_bytes_case_is_unchanged_not_refreshed(workspace):
    """The distinction the whole task rests on: 18 unchanged files is the signal."""
    root, config, tmp = workspace
    body = "identical\n"
    _write(tmp, "otaman-cli", body)
    _write(tmp, "otaman-core", body)
    before = gi.expected_files(root, config)
    _write(tmp, "otaman-cli", body)  # rewritten with the same content

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert outcome.refreshed == []
    assert sorted(outcome.unchanged) == ["otaman-cli", "otaman-core"]
    assert outcome.did_work is False


def test_the_digest_is_of_content_not_mtime(workspace):
    """A rewrite with identical bytes must not read as refreshed — which an mtime
    comparison would, and every generator run rewrites every file."""
    root, config, tmp = workspace
    path = _write(tmp, "otaman-cli", "body\n")
    before = gi.expected_files(root, config)
    import os
    import time

    os.utime(path, (time.time() + 100, time.time() + 100))

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert "otaman-cli" in outcome.unchanged


def test_an_unreadable_file_is_a_failure_distinct_from_absent(workspace, monkeypatch):
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "x\n")
    _write(tmp, "otaman-core", "x\n")
    before = gi.expected_files(root, config)

    real = Path.read_bytes

    def boom(self, *a, **kw):
        if self.name == gi.GENERATED_FILENAME and "otaman-cli" in str(self):
            raise OSError("permission denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_bytes", boom)
    outcome = gi.compare(before, gi.expected_files(root, config))

    assert [r for r, _ in outcome.failed] == ["otaman-cli"]
    assert "cannot be read" in outcome.failed[0][1]
    assert outcome.unchanged == ["otaman-core"], "only the unreadable file is a failure"


def test_a_missing_repo_is_skipped_in_the_outcome(workspace):
    root, config, tmp = workspace
    config["repos"].append({"name": "otaman-ghost", "path": "../otaman-ghost"})
    _write(tmp, "otaman-cli", "x\n")
    _write(tmp, "otaman-core", "x\n")
    before = gi.expected_files(root, config)

    outcome = gi.compare(before, gi.expected_files(root, config))

    assert [r for r, _ in outcome.skipped] == ["otaman-ghost"]
    assert outcome.failed == []
    assert outcome.examined == 2, "a skipped repo is not an examined file"


# ---------------------------------------------------------------------------
# Zero work is stated.


def test_zero_refreshed_is_a_sentence_not_an_absence(workspace):
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "same\n")
    _write(tmp, "otaman-core", "same\n")
    before = gi.expected_files(root, config)

    lines = gi.render_lines(gi.compare(before, gi.expected_files(root, config)))
    rendered = "\n".join(lines)

    assert "0 refreshed, 2 unchanged, 0 failed" in rendered
    assert "0 refreshed —" in rendered, "zero work must say so in words, not just a digit"


def test_nothing_examined_says_nothing_was_examined(workspace):
    """A program with no repos, or none checked out — distinct from 'all unchanged'."""
    root, config, _tmp = workspace
    config["repos"] = []

    lines = gi.render_lines(gi.compare([], gi.expected_files(root, config)))

    assert "no generated instruction file was examined" in "\n".join(lines)


def test_the_counts_name_the_repos_that_moved(workspace):
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "old\n")
    before = gi.expected_files(root, config)
    _write(tmp, "otaman-cli", "new\n")
    _write(tmp, "otaman-core", "new\n")

    rendered = "\n".join(gi.render_lines(gi.compare(before, gi.expected_files(root, config))))

    assert "refreshed: otaman-cli" in rendered
    assert "refreshed: otaman-core" in rendered


# ---------------------------------------------------------------------------
# The generation stamp is reported, not written.


STAMPED = """\
<!-- otaman:begin -->
<!-- otaman:generated generator=0.3.0 -->
rules here
<!-- otaman:end -->
"""


def test_a_stamp_is_read_from_the_file(workspace):
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", STAMPED)
    _write(tmp, "otaman-core", STAMPED)

    outcome = gi.compare([], gi.expected_files(root, config))

    assert outcome.stamps == {"otaman-cli": "0.3.0", "otaman-core": "0.3.0"}
    assert outcome.unstamped == []
    assert "generation stamp: generator 0.3.0" in "\n".join(gi.render_lines(outcome))


def test_an_unstamped_file_is_named_and_is_not_a_failure(workspace):
    """The honest state today: plugin's generator does not emit the stamp yet. Its
    absence is the frozen-files signal, and it is not this command's failure."""
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "no stamp here\n")
    _write(tmp, "otaman-core", "no stamp here\n")

    outcome = gi.compare([], gi.expected_files(root, config))
    rendered = "\n".join(gi.render_lines(outcome))

    assert sorted(outcome.unstamped) == ["otaman-cli", "otaman-core"]
    assert outcome.failed == []
    assert "no generation stamp in 2 file(s)" in rendered
    assert "otaman-plugin" in rendered, "the stamp's owner must be named, not implied"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<!-- otaman:generated generator=0.3.0 -->", "0.3.0"),
        ("<!-- otaman:generated  generator=1.2.3-rc1  -->", "1.2.3-rc1"),
        # Tolerant about extra fields, so plugin can add a timestamp or a commit
        # without this becoming a second format to keep in step.
        ("<!-- otaman:generated generator=0.4.0 at=2026-10-01T10:00:00Z -->", "0.4.0"),
        ("<!-- OTAMAN:GENERATED generator=0.4.0 -->", "0.4.0"),
        ("nothing here", ""),
        ("<!-- otaman:begin --> rules <!-- otaman:end -->", ""),
        ("", ""),
    ],
)
def test_the_stamp_matcher(text, expected):
    assert gi.stamp_of(text) == expected


def test_the_module_writes_nothing(workspace):
    """Reported, not written: a stamp written by anyone but the generator can
    desynchronize from the content it describes the moment someone runs the
    generator directly."""
    root, config, tmp = workspace
    _write(tmp, "otaman-cli", "body\n")
    digests = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp.rglob(gi.GENERATED_FILENAME)
    }

    gi.compare(gi.expected_files(root, config), gi.expected_files(root, config))
    gi.render_lines(gi.compare([], gi.expected_files(root, config)))

    for path, digest in digests.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest

    src = Path(gi.__file__).read_text(encoding="utf-8")
    assert "write_text" not in src and "write_bytes" not in src


# ---------------------------------------------------------------------------
# End to end through `init --update`, which is the surface the task names.


class _Gen:
    """A stand-in generator: writes what it is told, reports what it is told.

    Injected rather than running plugin's real generator, because the point of the
    counts is that they do NOT trust what the generator says about itself — so the
    two have to be separable in a test.
    """

    def __init__(self, writes: dict[str, str], returncode: int = 0):
        self.writes = writes
        self.returncode = returncode
        self.called = False

    def __call__(self, script, *args, **kw):
        self.called = True
        for path, body in self.writes.items():
            Path(path).write_text(body, encoding="utf-8")
        return self


@pytest.fixture
def program(isolate_bus, monkeypatch, tmp_path):
    """A program root whose two repos sit beside it, as a real workspace does."""
    root = tmp_path / "meta"
    root.mkdir()
    for rel in ("otaman-cli", "otaman-core"):
        (tmp_path / rel).mkdir()
        (tmp_path / rel / ".otaman").write_text("../meta\n", encoding="utf-8")
    (root / "platform.yaml").write_text(
        "project: demo\n"
        "version: '1.0'\n"
        "repos:\n"
        "  - name: otaman-cli\n    path: ../otaman-cli\n    owner: cli-agent\n"
        "  - name: otaman-core\n    path: ../otaman-core\n    owner: core-agent\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return root, tmp_path


def _run_update(monkeypatch, root, gen, dry_run=False):
    from otaman_cli.commands import init as init_cmd

    monkeypatch.setattr(init_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(init_cmd, "run_script", gen)
    return init_cmd._cmd_init_update(dry_run=dry_run)


def test_end_to_end_the_counts_reach_the_operator(program, monkeypatch, capsys):
    root, tmp = program
    _write(tmp, "otaman-cli", "old\n")
    _write(tmp, "otaman-core", "same\n")
    gen = _Gen(
        {
            str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): "new\n",
            str(tmp / "otaman-core" / gi.GENERATED_FILENAME): "same\n",
        }
    )

    rc = _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert gen.called
    assert rc == 0
    assert "1 refreshed, 1 unchanged, 0 failed" in out
    assert "refreshed: otaman-cli" in out


def test_end_to_end_zero_work_is_still_reported(program, monkeypatch, capsys):
    """The four-month case: every file already matches, and the run SAYS so."""
    root, tmp = program
    _write(tmp, "otaman-cli", "same\n")
    _write(tmp, "otaman-core", "same\n")
    gen = _Gen(
        {
            str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): "same\n",
            str(tmp / "otaman-core" / gi.GENERATED_FILENAME): "same\n",
        }
    )

    rc = _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert rc == 0
    assert "0 refreshed, 2 unchanged, 0 failed" in out
    assert "0 refreshed —" in out


def test_end_to_end_a_generator_that_exits_zero_without_writing_fails_the_run(
    program, monkeypatch, capsys
):
    """THE silent-success shape. The generator reports success, a file it owns is
    absent, and before this the command exited 0 with `Updated: 2`."""
    root, tmp = program
    gen = _Gen({})  # writes nothing, returncode 0

    rc = _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert rc == 1, "a run that regenerated nothing must not report success"
    assert "0 refreshed, 0 unchanged, 2 failed" in out
    assert "exited 0 but did not write every expected" in out


def test_end_to_end_a_failing_generator_still_counts_what_it_wrote(program, monkeypatch, capsys):
    """A non-zero generator may still have written some files, and which ones is
    exactly what the operator needs in order to know where they stand."""
    root, tmp = program
    gen = _Gen(
        {str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): "new\n"},
        returncode=1,
    )

    rc = _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert rc == 1
    assert "1 refreshed, 0 unchanged, 1 failed" in out
    assert "refreshed: otaman-cli" in out


def test_end_to_end_dry_run_counts_nothing_and_writes_nothing(program, monkeypatch, capsys):
    root, tmp = program
    _write(tmp, "otaman-cli", "untouched\n")
    gen = _Gen({str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): "WRITTEN\n"})

    rc = _run_update(monkeypatch, root, gen, dry_run=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert gen.called is False, "dry run must not invoke the generator"
    assert "would account for 2 generated" in out
    assert (tmp / "otaman-cli" / gi.GENERATED_FILENAME).read_text() == "untouched\n"


def test_end_to_end_the_stamp_absence_is_surfaced(program, monkeypatch, capsys):
    root, tmp = program
    gen = _Gen(
        {
            str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): "no stamp\n",
            str(tmp / "otaman-core" / gi.GENERATED_FILENAME): "no stamp\n",
        }
    )

    _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert "no generation stamp in 2 file(s)" in out


def test_end_to_end_a_present_stamp_is_surfaced(program, monkeypatch, capsys):
    root, tmp = program
    gen = _Gen(
        {
            str(tmp / "otaman-cli" / gi.GENERATED_FILENAME): STAMPED,
            str(tmp / "otaman-core" / gi.GENERATED_FILENAME): STAMPED,
        }
    )

    _run_update(monkeypatch, root, gen)
    out = capsys.readouterr().out

    assert "generation stamp: generator 0.3.0" in out
    assert "no generation stamp" not in out
