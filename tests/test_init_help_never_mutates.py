"""`otaman init --help` must print help, not perform an init (deploy-agent, urgent).

Reported 20261002T133408 and reproduced here before the fix. The chain:

1. `cmd_init`'s flag loop ended with `elif args[i].startswith("-"): i += 1  # skip
   unknown flags`, so `--help` was DISCARDED rather than refused;
2. that leaves the bare-`otaman init` shape, which the pre-flight routes;
3. no platform.yaml + sibling git repos (a repo inside a program dir has ~18) → the
   prompt "Scan and generate platform.yaml from them? [Y/n]", where EMPTY INPUT IS YES;
4. `cmd_scan` runs. On deploy-agent's tree it repointed `.otaman` from `../otaman-meta`
   to `.` — the split-brain-bus state — and a well-meant cleanup afterwards left
   `check-ownership.sh` refusing every Bash and Write call, needing a human outside the
   session to recover.

Three guards, and each is tested with the bug restored: help prints before any side
effect, an unknown flag is refused instead of discarded, and a bare init inside a REPO
of a program refuses rather than scaffolding over it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from otaman_cli.commands import init as init_cmd

MARKER = "# Path to otaman folder\n../p1-meta\n"


@pytest.fixture
def repo_of_a_program(tmp_path, monkeypatch):
    """A repo whose `.otaman` names its program's meta dir, with sibling git repos.

    The sibling repos matter: they are what makes the pre-flight offer the scan, which
    is the route that mutates. Without them the wizard branch is taken instead.
    """
    program = tmp_path / "orgs" / "acme" / "programs" / "p1"
    for name in ("p1-meta", "target-repo", "sib-a", "sib-b"):
        (program / name).mkdir(parents=True)
    (program / "p1-meta" / "platform.yaml").write_text(
        "project: p1\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    (program / "target-repo" / ".otaman").write_text(MARKER, encoding="utf-8")
    for name in ("target-repo", "sib-a", "sib-b"):
        subprocess.run(["git", "init", "-q", "."], cwd=program / name, check=False)
    monkeypatch.chdir(program / "target-repo")
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    return program


def _snapshot(path: Path) -> set[str]:
    return {p.name for p in path.iterdir()}


def _as_unattended_tty():
    """A TTY whose prompts get the empty answer an unattended agent session gives.

    This is the condition deploy-agent hit, and the reason the default-yes prompt is
    load-bearing: on a pipe the pre-flight refuses and nothing happens, so a test that
    did not fake the TTY would pass against the bug.
    """
    return patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="")


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_and_mutates_nothing(repo_of_a_program, capsys, flag):
    repo = repo_of_a_program / "target-repo"
    before = _snapshot(repo)
    marker_before = (repo / ".otaman").read_text(encoding="utf-8")

    tty, answer = _as_unattended_tty()
    with tty, answer:
        rc = init_cmd.cmd_init([flag])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage: otaman init" in out
    assert _snapshot(repo) == before, "help must not create anything"
    assert (repo / ".otaman").read_text(encoding="utf-8") == marker_before


def test_help_wins_even_after_a_positional(repo_of_a_program, capsys):
    """`otaman init . --help` is a request for help too — help wins over every shape."""
    repo = repo_of_a_program / "target-repo"
    before = _snapshot(repo)
    tty, answer = _as_unattended_tty()
    with tty, answer:
        rc = init_cmd.cmd_init([".", "--help"])
    assert rc == 0
    assert "Usage: otaman init" in capsys.readouterr().out
    assert _snapshot(repo) == before


def test_an_unknown_flag_is_refused_not_discarded(repo_of_a_program, capsys):
    """The root cause. Discarding it left the bare-init shape, which mutates."""
    repo = repo_of_a_program / "target-repo"
    before = _snapshot(repo)
    tty, answer = _as_unattended_tty()
    with tty, answer:
        rc = init_cmd.cmd_init(["--bogus"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "--bogus" in out
    assert _snapshot(repo) == before


def test_the_supported_flags_are_still_accepted(repo_of_a_program):
    """The refusal must not swallow the verb's real flags."""
    for flag in ("--update", "--shell", "--yes", "-y", "--dry-run", "--skip-doctor"):
        assert init_cmd._unsupported_flags([flag]) == [], flag
    assert init_cmd._unsupported_flags(["--bogus", "--dry-run", "--nope"]) == ["--bogus", "--nope"]


def test_a_bare_init_inside_a_repo_of_a_program_refuses(repo_of_a_program, capsys):
    """deploy-agent's third ask: the marker naming another dir IS the signal that this
    is not a program root, and the destructive run overwrote it instead of reading it."""
    repo = repo_of_a_program / "target-repo"
    before = _snapshot(repo)
    tty, answer = _as_unattended_tty()
    with tty, answer:
        rc = init_cmd.cmd_init([])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REPO of a program" in out
    assert "../p1-meta" in out, "the refusal names where the program actually is"
    assert _snapshot(repo) == before
    assert (repo / ".otaman").read_text(encoding="utf-8") == MARKER


def test_the_guard_does_not_block_a_program_root(tmp_path, monkeypatch):
    """A marker pointing at `.` (or absent) is a program root — init belongs there."""
    root = tmp_path / "p1-meta"
    root.mkdir()
    monkeypatch.chdir(root)
    assert init_cmd._refuse_inside_a_repo_of_a_program([]) is None
    (root / ".otaman").write_text("# Path to otaman folder\n.\n", encoding="utf-8")
    assert init_cmd._refuse_inside_a_repo_of_a_program([]) is None


def test_the_guard_is_scoped_to_the_bare_form(repo_of_a_program):
    """`--update` is meant to run from anywhere, and an explicit config path says what
    the caller meant — only the bare form routes into scan or the wizard."""
    assert init_cmd._refuse_inside_a_repo_of_a_program(["platform.yaml"]) is None
    assert init_cmd._refuse_inside_a_repo_of_a_program(["--update"]) is None


def test_an_agent_field_is_not_mistaken_for_the_marker_target(tmp_path, monkeypatch, capsys):
    """`init --update` appends `agent: <name>` to markers, and a hand-edited one can put
    it FIRST. The refusal must still name the path, not the field.

    Asserting only the exit code would be vacuous here — the refusal fires either way,
    because an unresolvable target is still "not a program root". What the filter buys is
    a message that names where the program actually is, which is the only actionable part.
    """
    repo = tmp_path / "some-repo"
    repo.mkdir()
    (repo / ".otaman").write_text(
        "# Path to otaman folder\nagent: cli-agent\n../p1-meta\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    assert init_cmd._refuse_inside_a_repo_of_a_program([]) == 2
    out = capsys.readouterr().out
    assert "names: ../p1-meta" in out
    assert "agent: cli-agent" not in out


def test_companion_repos_help_does_not_scaffold(repo_of_a_program, capsys):
    """The sub-action's help must be as inert as the verb's."""
    repo = repo_of_a_program / "target-repo"
    before = _snapshot(repo)
    tty, answer = _as_unattended_tty()
    with tty, answer:
        rc = init_cmd.cmd_init(["companion-repos", "--help"])
    assert rc == 0
    assert _snapshot(repo) == before
