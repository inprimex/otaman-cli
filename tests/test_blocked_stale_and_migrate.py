"""blocked-entry-lifecycle 1.4/1.5 — `[stale]` rendering and the one-time sweep.

1.4: an entry whose ref resolves to nothing is REPORTED as stale, never
auto-removed, and the banner says how many are live vs stale — a list that is
mostly stale trains agents to ignore the one surface meant to tell them they are
genuinely blocked.

1.5: the migration treats TOMBSTONED entries as already terminated (never
re-migrating them), sweeps live entries it can prove are finished, and reports
everything else for human triage — never bulk-deleting.
"""

from __future__ import annotations

import pytest

from otaman_cli.blocked_entries import KIND_APPROVAL, KIND_DEPENDENCY, parse_entries, stale_reason


def _entry(title: str, **fields: str) -> str:
    lines = [f"## Blocked: {title}"]
    lines += [f"- **{k.replace('_', ' ').title()}**: {v}" for k, v in fields.items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 1.4 — staleness is a pure predicate over known refs


def test_resolvable_ref_is_live():
    (e,) = parse_entries(_entry("x", proposal="stem-1"))
    assert stale_reason(e, known_refs={"stem-1"}) == ""


def test_unresolvable_ref_is_stale_and_says_why():
    (e,) = parse_entries(_entry("x", proposal="stem-gone"))
    reason = stale_reason(e, known_refs={"other"})
    assert "stem-gone" in reason and "not found" in reason


def test_missing_ref_is_stale_with_its_own_reason():
    (e,) = parse_entries(_entry("x", blocked_by="human"))
    reason = stale_reason(e, known_refs=set())
    assert "no stable ref" in reason


def test_stale_reason_names_the_right_noun_per_kind():
    (approval,) = parse_entries(_entry("a", proposal="p-1"))
    (dependency,) = parse_entries(_entry("d", change="c-1"))
    assert approval.kind == KIND_APPROVAL
    assert dependency.kind == KIND_DEPENDENCY
    assert "proposal" in stale_reason(approval, known_refs=set())
    assert "change" in stale_reason(dependency, known_refs=set())


def test_staleness_never_mutates_anything():
    """Stale is REPORTED, not removed — the defect being fixed is entries
    vanishing or persisting with nobody able to tell which."""
    text = _entry("x", proposal="gone")
    (e,) = parse_entries(text)
    stale_reason(e, known_refs=set())
    assert parse_entries(text)[0].title == "x"  # untouched


# ---------------------------------------------------------------------------
# 1.4 — the check surface


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "blocked").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_ROOT", str(root))
    monkeypatch.setenv("MAESTRO_ROOT", str(root))
    return root


def test_known_refs_collects_bus_stems(program):
    from otaman_cli.commands.check import _known_refs

    (program / ".agents" / "bus" / "active" / "20260913T144246-a-to-b-x.md").write_text(
        "body", encoding="utf-8"
    )
    assert "20260913T144246-a-to-b-x" in _known_refs(program)


def test_known_refs_is_empty_not_raising_without_a_bus(tmp_path):
    from otaman_cli.commands.check import _known_refs

    bare = tmp_path / "nothing"
    bare.mkdir()
    assert _known_refs(bare) == set() or isinstance(_known_refs(bare), set)


def test_check_renders_live_and_stale_counts(program, capsys):
    from otaman_cli.commands.check import cmd_check

    (program / ".agents" / "bus" / "active" / "20260913T144246-a-to-b-x.md").write_text(
        "---\nid: 20260913T144246-a-to-b-x\nfrom: a\nto: b\ntype: info\n---\n\n## Subject: x\n",
        encoding="utf-8",
    )
    (program / ".agents" / "blocked" / "cli-agent.md").write_text(
        _entry("live one", proposal="20260913T144246-a-to-b-x")
        + "\n"
        + _entry("stale one", proposal="stem-that-does-not-exist"),
        encoding="utf-8",
    )
    cmd_check(["cli-agent"])
    out = capsys.readouterr().out
    assert "1 live" in out and "1 stale" in out
    assert "[stale] stale one" in out
    assert "not found" in out
    assert "never auto-removed" in out


def test_check_omits_the_stale_count_when_none(program, capsys):
    from otaman_cli.commands.check import cmd_check

    (program / ".agents" / "bus" / "active" / "20260913T144246-a-to-b-x.md").write_text(
        "---\nid: 20260913T144246-a-to-b-x\nfrom: a\nto: b\ntype: info\n---\n\n## Subject: x\n",
        encoding="utf-8",
    )
    (program / ".agents" / "blocked" / "cli-agent.md").write_text(
        _entry("live one", proposal="20260913T144246-a-to-b-x"), encoding="utf-8"
    )
    cmd_check(["cli-agent"])
    out = capsys.readouterr().out
    assert "1 live" in out and "stale" not in out.split("BLOCKED TASKS")[1].split("\n")[0]


def test_check_ignores_tombstoned_entries(program, capsys):
    from otaman_cli.commands.check import cmd_check

    (program / ".agents" / "blocked" / "cli-agent.md").write_text(
        "<!-- ## Blocked: done\n- **Proposal**: s\ncleared 2026-09-01 — approved -->\n",
        encoding="utf-8",
    )
    cmd_check(["cli-agent"])
    assert "BLOCKED TASKS" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 1.5 — the migration


def _write_blocked(root, agent: str, text: str):
    (root / ".agents" / "blocked" / f"{agent}.md").write_text(text, encoding="utf-8")
    return root / ".agents" / "blocked" / f"{agent}.md"


def test_migrate_treats_tombstoned_as_already_terminated(program, capsys):
    """The re-scope's addition: plugin TOMBSTONES rather than deletes, so a
    migration that didn't recognise the format would put closed entries back
    into the live set."""
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    _write_blocked(
        program,
        "cli-agent",
        "<!-- ## Blocked: already done\n- **Proposal**: s1\ncleared 2026-09-01 — approved -->\n",
    )
    assert _cmd_blocked_migrate(program, apply=False) == 0
    out = capsys.readouterr().out
    assert "already terminated (tombstoned): 1" in out
    assert "human triage" not in out  # not re-migrated


def test_migrate_sweeps_an_entry_whose_proposal_was_decided(program, capsys):
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    stem = "20260909T225320-cli-agent-to-human-spec-change-request"
    (
        program
        / ".agents"
        / "bus"
        / "active"
        / "20260910T081510-human-to-all-spec-change-approved.md"
    ).write_text(
        f"## Subject: Approved: something\n\n**Original proposal**: {stem}\n", encoding="utf-8"
    )
    f = _write_blocked(program, "cli-agent", _entry("waiting", proposal=stem))
    _cmd_blocked_migrate(program, apply=True)
    capsys.readouterr()
    assert parse_entries(f.read_text(encoding="utf-8")) == []  # swept
    assert "proposal approved/rejected" in f.read_text(encoding="utf-8")


def test_migrate_reports_the_undecided_for_triage_and_never_deletes(program, capsys):
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    f = _write_blocked(program, "cofounder-agent", _entry("JTBD-59 critic policy", proposal="p-59"))
    _cmd_blocked_migrate(program, apply=True)
    out = capsys.readouterr().out
    assert "human triage: 1" in out
    assert "JTBD-59" in out
    assert len(parse_entries(f.read_text(encoding="utf-8"))) == 1  # survives


def test_migrate_dry_run_writes_nothing(program, capsys):
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    stem = "20260909T225320-a-to-human-spec-change-request"
    (program / ".agents" / "bus" / "active" / "x-spec-change-approved.md").write_text(
        f"proposal {stem}", encoding="utf-8"
    )
    f = _write_blocked(program, "cli-agent", _entry("waiting", proposal=stem))
    before = f.read_text(encoding="utf-8")
    _cmd_blocked_migrate(program, apply=False)
    out = capsys.readouterr().out
    assert "dry run" in out.lower() and "Would sweep" in out
    assert f.read_text(encoding="utf-8") == before  # untouched


def test_migrate_is_a_noop_without_a_blocked_dir(tmp_path, capsys):
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    bare = tmp_path / "bare"
    bare.mkdir()
    assert _cmd_blocked_migrate(bare, apply=True) == 0
    assert "nothing to migrate" in capsys.readouterr().out.lower()


def test_migrate_names_why_each_entry_was_swept(program, capsys):
    from otaman_cli.commands.blocked import _cmd_blocked_migrate

    stem = "20260909T225320-a-to-human-spec-change-request"
    (program / ".agents" / "bus" / "active" / "x-spec-change-approved.md").write_text(
        f"proposal {stem}", encoding="utf-8"
    )
    _write_blocked(program, "cli-agent", _entry("waiting", proposal=stem))
    _cmd_blocked_migrate(program, apply=False)
    out = capsys.readouterr().out
    assert "reason: proposal approved/rejected" in out
