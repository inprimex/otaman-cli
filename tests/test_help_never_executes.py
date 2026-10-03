"""`--help` must print usage and perform NO side effect.

A hand-rolled argv loop ignores a flag it does not recognise and falls through to the
work, so `--help` reads as "no options given" and the command RUNS. Measured on the live
bus on 2026-10-03: `otaman cleanup --help` archived 12 real messages. Recoverable — #249
made archiving a move — but entirely unasked for. `otaman scan --help` wrote too, and the
same shape in `otaman init` cost a fleet-wide advisory on 2026-10-02 (it executed a scan
and could repoint a `.otaman` marker).

The guard has to supply WORK TO DO, which is the subtlety that makes a naive version of
this test vacuous: the first fixture I wrote had a single UNACKED message, `cleanup`
refuses to archive anything awaiting an ack, and so `--help` wrote nothing and the test
passed against the unfixed code. Every case here is primed so the unguarded command would
definitely act.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from otaman_cli.commands import HELP_FLAGS, wants_help


def _fingerprint(root: Path) -> str:
    """Every file path + content under *root*, so a MOVE is a change too."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program with an OLD, ACKED message — i.e. real work for cleanup to do."""
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "registry").mkdir(parents=True)
    (root / "platform.yaml").write_text(
        "project: t\nversion: '1.0'\nrepos:\n  - name: r\n    path: ../r\n    owner: cli-agent\n",
        encoding="utf-8",
    )
    (root / ".agents" / "registry" / "agents.yaml").write_text(
        "agents:\n  cli-agent:\n    repo: r\n", encoding="utf-8"
    )
    stem = "20260501T100000-core-agent-to-human-old"
    (root / ".agents" / "bus" / "active" / f"{stem}.md").write_text(
        f"---\nid: {stem}\nfrom: core-agent\nto: human\npriority: normal\ntype: info\n"
        "timestamp: 2026-05-01T10:00:00+00:00\nstatus: pending\n---\n\n## Subject: old\nb\n",
        encoding="utf-8",
    )
    # the ack is what makes it archivable — without it cleanup declines and the guard
    # would prove nothing
    (root / ".agents" / "bus" / "active" / "acks" / f"{stem}.human.ack").write_text(
        "resolved\n", encoding="utf-8"
    )

    return root


# ---------------------------------------------------------------------------
# the predicate


@pytest.mark.parametrize("flag", sorted(HELP_FLAGS))
def test_every_help_flag_is_recognised(flag):
    assert wants_help([flag])


def test_help_is_recognised_ANYWHERE_not_just_first():
    """`otaman cleanup --days 30 --help` is a request for help too, and the point is
    that no argument shape reaches a side effect while help was asked for."""
    assert wants_help(["--days", "30", "--help"])
    assert wants_help(["companion-repos", "-h"])
    assert not wants_help(["--days", "30"])


# ---------------------------------------------------------------------------
# the commands that were measured acting on --help


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_cleanup_help_archives_nothing(program, monkeypatch, capsys, flag):
    from otaman_cli.commands import cleanup as mod

    monkeypatch.setattr(mod, "find_project_root", lambda: program, raising=False)
    before = _fingerprint(program)

    rc = mod.cmd_cleanup([flag])

    assert rc == 0
    assert _fingerprint(program) == before, "cleanup --help moved or wrote something"
    assert "Usage:" in "".join(capsys.readouterr())


def test_the_fixture_really_gives_cleanup_work_to_do(program, monkeypatch):
    """Guards the guard: if cleanup would not have acted anyway, the test above is
    vacuous. This is the version of the fixture that caught my first attempt.
    """
    from otaman_cli.commands import cleanup as mod

    monkeypatch.setattr(mod, "find_project_root", lambda: program, raising=False)
    before = _fingerprint(program)

    mod.cmd_cleanup(["--days", "30"])

    assert _fingerprint(program) != before, (
        "the fixture has no archivable message, so the --help test proves nothing"
    )


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_scan_help_writes_nothing(tmp_path, monkeypatch, capsys, flag):
    from otaman_cli.commands import scan as mod

    workdir = tmp_path / "work"
    (workdir / "repo-a").mkdir(parents=True)
    monkeypatch.chdir(workdir)
    before = _fingerprint(workdir)

    rc = mod.cmd_scan([flag])

    assert rc == 0
    assert _fingerprint(workdir) == before, "scan --help wrote something"
    assert "Usage:" in "".join(capsys.readouterr())


# ---------------------------------------------------------------------------
# the single home


#: Modules that still re-derive the help predicate locally, measured 2026-10-03.
#: A RATCHET, not an exemption: this set may only SHRINK. Not swept in the same change
#: that introduced the shared home because several are `args[0] in (...)` —
#: first-position-only — and widening them to "anywhere" changes behaviour for any
#: command where `help` is a legitimate positional. That sweep needs its own care; the
#: two commands measured ACTING on `--help` (cleanup, scan) are fixed here.
_KNOWN_LOCAL_PREDICATES: frozenset[str] = frozenset(
    {
        "acting_lock.py",
        "blocked.py",
        "cli_group.py",
        "misc_readonly.py",
        "policy.py",
        "program.py",
        "propose_team.py",
        "spec.py",
    }
)


def test_no_NEW_command_module_rolls_its_own_help_predicate():
    """The defect class was being fixed per-command, which is why two commands had no
    fix at all. The predicate has one home; a local copy is how the next one goes
    missing — so a module not already on the ratchet may not add one.
    """
    import otaman_cli.commands as C

    pkg_dir = Path(C.__file__).parent
    offenders = {}
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "in (" in line and '"--help"' in line:
                offenders.setdefault(path.name, []).append(n)

    new_offenders = {k: v for k, v in offenders.items() if k not in _KNOWN_LOCAL_PREDICATES}
    assert not new_offenders, (
        "import `wants_help` from otaman_cli.commands instead of re-deriving the "
        f"predicate: {new_offenders}"
    )

    cleared = _KNOWN_LOCAL_PREDICATES - set(offenders)
    assert not cleared, (
        "these modules no longer re-derive it — remove them from "
        f"_KNOWN_LOCAL_PREDICATES so the ratchet keeps its teeth: {sorted(cleared)}"
    )
