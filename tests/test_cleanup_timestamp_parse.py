"""`otaman cleanup` archived nothing because its timestamp parse rejected the bus.

deploy-agent root-caused it on the live fleet (20261001T142130): `otaman check`
taking 9–11s against 6,980 active messages and 1 archived, with
`otaman cleanup --dry-run` reporting "Nothing to clean up." for four and a half
months.

`parse_timestamp` tried three hand-listed `strptime` formats, none accepting
fractional seconds — while the producer writes `datetime.now(UTC).isoformat()`,
which emits them. Measured on the live bus before the fix: **6,919 of 7,032
(98.4%) unparseable**, of which 4,020 had microseconds and the rest were
`2026-05-24 21:29:29+00:00` — space-separated WITH an offset, which the listed
`%Y-%m-%d %H:%M:%S` cannot take either.

Every rejected message was then dropped by a bare `continue`, so it reached
neither the age check nor the ack check. That silence is the larger defect and has
its own tests below: the count of what a verb declined to examine is part of its
result. After the fix, on the same live bus: 2,426 archivable, 1,427 held on a
missing ack, 7 genuinely skipped (all with an EMPTY `timestamp:`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from otaman_cli.cleanup_bus import cleanup, parse_timestamp

#: Every shape the live bus actually carries. The first two were REJECTED.
LIVE_SHAPES = [
    # `.isoformat()` from the producer — 4,020 messages, the bulk of the loss.
    ("2026-09-25T14:43:43.447212+00:00", datetime(2026, 9, 25, 14, 43, 43, 447212, timezone.utc)),
    # space-separated WITH an offset — the second rejected family.
    ("2026-05-24 21:29:29+00:00", datetime(2026, 5, 24, 21, 29, 29, tzinfo=timezone.utc)),
    # the `Z` broadcasts — the 113 that DID parse, which must keep parsing.
    ("2026-09-30T16:17:07Z", datetime(2026, 9, 30, 16, 17, 7, tzinfo=timezone.utc)),
    ("2026-08-11 20:53:12", datetime(2026, 8, 11, 20, 53, 12, tzinfo=timezone.utc)),
    ("2026-09-30T16:17:07+03:00", datetime(2026, 9, 30, 13, 17, 7, tzinfo=timezone.utc)),
]


@pytest.mark.parametrize(("raw", "expected"), LIVE_SHAPES)
def test_every_shape_the_live_bus_carries_parses(raw, expected):
    got = parse_timestamp({"timestamp": raw})
    assert got is not None, f"{raw!r} did not parse — this is the bug"
    assert got == expected


def test_the_producer_s_own_output_parses():
    """The two sides are one rule: the consumer must accept what the producer
    emits. A hand-listed format set is a second implementation of that rule, and
    it was the broken one."""
    emitted = datetime.now(timezone.utc).isoformat()
    assert parse_timestamp({"timestamp": emitted}) is not None


def test_the_parse_is_cores_single_home_not_a_local_copy():
    """core #102 homed `parse_bus_timestamp` in response to cli #234's note. The
    local three-`strptime` parser is GONE, not shadowed — a second implementation is
    what let the two drift until cli's rejected the producer's own output."""
    import ast
    import inspect
    import textwrap

    from otaman_cli import cleanup_bus

    # Scoped to the function's CODE: `cleanup` legitimately calls `strptime`
    # elsewhere to read an archive month directory's `%Y-%m` name, so a file-wide ban
    # would forbid a correct use — and the docstring here NAMES the parser it
    # replaced, so a source-text check on the whole function matches its own prose. A
    # guard a comment can break is a guard about comments.
    func = ast.parse(textwrap.dedent(inspect.getsource(cleanup_bus.parse_timestamp))).body[0]
    code = "\n".join(ast.unparse(node) for node in func.body if not isinstance(node, ast.Expr))
    assert "parse_bus_timestamp(" in code, "the parse is no longer delegated to core"
    assert "strptime" not in code, "a local timestamp parser came back"
    assert not hasattr(cleanup_bus, "_LEGACY_FORMATS")
    assert not hasattr(cleanup_bus, "_normalize_fraction")


def test_a_naive_timestamp_is_forced_to_utc_at_this_call_site():
    """The one thing the wrapper still does, and the reason it survives the repoint.

    core's parser returns whatever tzinfo the input carried, so a naive value comes
    back naive — and `cleanup` compares the result against an AWARE cutoff, which
    raises `TypeError: can't compare offset-naive and offset-aware datetimes`.
    Measured on the live bus: 7,076 aware, 0 naive, so this guards a shape that is
    accepted at write and would crash the archive pass rather than one in the data.
    """
    from datetime import timedelta

    from otaman_core.frontmatter import parse_bus_timestamp

    raw = "2026-08-11 20:53:12"
    assert parse_bus_timestamp(raw).tzinfo is None, (
        "core started normalizing naive input — if that is now its contract, this wrapper can go"
    )
    got = parse_timestamp({"timestamp": raw})
    assert got is not None and got.tzinfo is not None
    # The comparison the archive pass actually performs must not raise.
    assert isinstance(got < datetime.now(timezone.utc) - timedelta(days=30), bool)


@pytest.mark.parametrize("digits", [1, 2, 3, 5, 6, 7, 9])
def test_fractional_seconds_of_any_length_parse(digits):
    """Any fractional-digit count, through core's parser.

    cli used to normalize the fraction to six digits because 3.10's `fromisoformat`
    accepts only 3 or 6. That normalization is gone and is not core's to add either:
    **otaman-core declares `requires-python = ">=3.11"`**, and cli depends on it, so
    3.10 was never a reachable target — which is a defect in cli's own declared floor,
    fixed in this change.
    """
    raw = "2026-09-25T14:43:43." + ("1" * digits) + "+00:00"
    got = parse_timestamp({"timestamp": raw})
    assert got is not None, f"{digits} fractional digits rejected"
    assert got.replace(microsecond=0) == datetime(2026, 9, 25, 14, 43, 43, tzinfo=timezone.utc)


def test_a_naive_timestamp_is_treated_as_utc():
    got = parse_timestamp({"timestamp": "2026-08-11 20:53:12"})
    assert got is not None and got.tzinfo is not None


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "2026-13-45T99:99:99", None])
def test_a_genuinely_malformed_timestamp_still_returns_none(raw):
    """Widening the parse must not make it credulous. The 7 live failures after
    the fix all carry an EMPTY `timestamp:`, and inferring one from the filename
    stem would be guessing at data — the same move as treating unexaminable input
    as examined, which is the defect."""
    assert parse_timestamp({"timestamp": raw} if raw is not None else {}) is None


# ---------------------------------------------------------------------------
# The bigger defect: the skip was invisible.


def _bus(root, messages):
    """A program root whose active bus holds *messages* — (name, timestamp, to)."""
    active = root / ".agents" / "bus" / "active"
    (active / "acks").mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    for name, ts, to in messages:
        (active / f"{name}.md").write_text(
            f"---\nid: {name}\nfrom: cli-agent\nto: {to}\ntype: info\n"
            f"timestamp: {ts}\nstatus: pending\n---\n\n## Subject: s\nbody\n",
            encoding="utf-8",
        )
    return active


def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_an_unparseable_timestamp_is_counted_not_silently_dropped(tmp_path):
    """THE DEFECT. The skip was a bare `continue`: not counted, not reported. Had
    the run said "skipped 6,893", it would have been a one-minute diagnosis four
    months earlier."""
    root = tmp_path / "prog"
    _bus(root, [("20260101T000000-a", "", "cli-agent"), ("20260101T000001-b", "junk", "cli-agent")])

    report = cleanup(root, dry_run=True)

    assert report["skipped_unparseable"] == 2
    assert len(report["skipped_samples"]) == 2
    # The SHAPE, not just the filename — the count alone does not tell a reader
    # which producer to fix.
    assert any(s["timestamp"] == "junk" for s in report["skipped_samples"])


def test_the_sample_is_bounded(tmp_path):
    """6,893 filenames are not a report."""
    root = tmp_path / "prog"
    _bus(root, [(f"2026010{i // 10}T00000{i % 10}-x{i}", "junk", "cli-agent") for i in range(12)])

    report = cleanup(root, dry_run=True)

    assert report["skipped_unparseable"] == 12
    assert len(report["skipped_samples"]) == 5


def test_a_message_held_only_by_a_missing_ack_is_counted_separately(tmp_path):
    """The question that arrives right after the parse fix: 1,427 messages are old
    enough and still here. Answered in the report rather than becoming the next
    four-month mystery."""
    root = tmp_path / "prog"
    _bus(root, [("20260101T000000-a", _old(90), "cli-agent")])

    report = cleanup(root, dry_run=True)

    assert report["held_unacked"] == 1
    assert report["archived"] == []
    assert report["skipped_unparseable"] == 0


def test_an_acked_old_message_with_microseconds_archives(tmp_path):
    """End to end on the exact shape that was being rejected."""
    root = tmp_path / "prog"
    active = _bus(root, [("20260101T000000-a", _old(90), "cli-agent")])
    (active / "acks" / "20260101T000000-a.cli-agent.ack").write_text("resolved\n", encoding="utf-8")

    report = cleanup(root, dry_run=True)

    assert report["archived"] == ["20260101T000000-a.md"]
    assert report["held_unacked"] == 0


def test_a_young_message_is_neither_archived_nor_counted_as_held(tmp_path):
    root = tmp_path / "prog"
    _bus(root, [("20260101T000000-a", _old(1), "cli-agent")])

    report = cleanup(root, dry_run=True)

    assert report["archived"] == []
    assert report["held_unacked"] == 0
    assert report["skipped_unparseable"] == 0


def test_dry_run_moves_nothing(tmp_path):
    root = tmp_path / "prog"
    active = _bus(root, [("20260101T000000-a", _old(90), "cli-agent")])
    (active / "acks" / "20260101T000000-a.cli-agent.ack").write_text("resolved\n", encoding="utf-8")

    cleanup(root, dry_run=True)

    assert (active / "20260101T000000-a.md").is_file()


# ---------------------------------------------------------------------------
# The surface: "Nothing to clean up." must never cover a discarded input.


def test_the_command_says_what_it_did_not_examine(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands import cleanup as cleanup_cmd

    root = tmp_path / "prog"
    _bus(root, [("20260101T000000-a", "", "cli-agent")])
    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cleanup_cmd,
        "run_script",
        lambda *a, **kw: _Result(cleanup(root, dry_run=True)),
    )

    # Exit 3, not 0: this fixture is ONE message and it is unreadable, so the run
    # examined a minority of its input. The code changed when deploy escalated their
    # second ask (20261001T192057); what this test is ABOUT is the reporting below,
    # and the expectation follows the behaviour rather than pinning the old one.
    assert cleanup_cmd.cmd_cleanup(["--dry-run"]) == cleanup_cmd.EXIT_MOSTLY_UNEXAMINED
    out = capsys.readouterr().out

    assert "NOT EXAMINED" in out
    assert "unparseable timestamp" in out
    # The sentence that hid this for four months, and the replacement that cannot.
    assert "Nothing to clean up." not in out
    assert "that COULD be examined" in out


def test_a_truly_empty_bus_still_says_nothing_to_clean_up(tmp_path, monkeypatch, capsys):
    """The honest sentence must survive for the case it was written for — a
    replacement that never says it is as uninformative as one that always does."""
    from otaman_cli.commands import cleanup as cleanup_cmd

    root = tmp_path / "prog"
    _bus(root, [])
    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cleanup_cmd,
        "run_script",
        lambda *a, **kw: _Result(cleanup(root, dry_run=True)),
    )

    assert cleanup_cmd.cmd_cleanup(["--dry-run"]) == 0
    out = capsys.readouterr().out

    assert "Nothing to clean up." in out
    assert "NOT EXAMINED" not in out


class _Result:
    """What `run_script(capture=True)` returns: the report as JSON on stdout."""

    returncode = 0
    stderr = ""

    def __init__(self, report):
        import json

        self.stdout = json.dumps(report)


# ---------------------------------------------------------------------------
# deploy-agent's second ask, escalated to a task-assignment (20261001T192057).


def test_the_minority_examined_exit_code_is_not_zero_and_not_one():
    """Distinct from 1 so a caller can tell it from an ordinary failure, and not 0
    because a run that read a minority of the bus has not done the job its name
    claims."""
    from otaman_cli.commands.cleanup import EXIT_MOSTLY_UNEXAMINED

    assert EXIT_MOSTLY_UNEXAMINED not in (0, 1)


def test_the_four_month_state_now_exits_non_zero(tmp_path, monkeypatch, capsys):
    """THE escalation. deploy measured 6,893 of 7,006 discarded while the command
    exited 0 and printed "Nothing to clean up." — across 8 of 8 tenants, 4 already
    accumulating. An exit code is what automation reads, and it said success."""
    from otaman_cli.commands import cleanup as cleanup_cmd

    root = tmp_path / "prog"
    _bus(root, [(f"20260101T0000{i:02d}-x{i}", "", "cli-agent") for i in range(12)])
    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cleanup_cmd, "run_script", lambda *a, **kw: _Result(cleanup(root, dry_run=True))
    )

    rc = cleanup_cmd.cmd_cleanup(["--dry-run"])
    out = capsys.readouterr().out

    assert rc == cleanup_cmd.EXIT_MOSTLY_UNEXAMINED
    assert "Examined a MINORITY" in out
    assert "12 skipped" in out


def test_a_handful_of_unreadable_messages_is_data_and_stays_zero(tmp_path, monkeypatch, capsys):
    """The live bus today: 7 valueless-timestamp June messages out of 7,084. That is
    DATA, not a parser mismatch, and a run that read everything else did its job.

    The rule is "I examined a minority of what I was given" rather than a percentage
    threshold — percentages are arbitrary and get argued about; this one separates a
    tool failure from bad rows.
    """
    from otaman_cli.commands import cleanup as cleanup_cmd

    root = tmp_path / "prog"
    messages = [("20260101T000000-bad", "", "cli-agent")]
    messages += [(f"20260101T0001{i:02d}-ok{i}", _old(1), "cli-agent") for i in range(6)]
    _bus(root, messages)
    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cleanup_cmd, "run_script", lambda *a, **kw: _Result(cleanup(root, dry_run=True))
    )

    rc = cleanup_cmd.cmd_cleanup(["--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0, "one unreadable message among seven is not a failed run"
    assert "NOT EXAMINED: 1 message" in out, "and it is still reported"
    assert "Examined a MINORITY" not in out


def test_a_clean_bus_exits_zero(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands import cleanup as cleanup_cmd

    root = tmp_path / "prog"
    _bus(root, [("20260101T000000-a", _old(1), "cli-agent")])
    monkeypatch.setattr(cleanup_cmd, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cleanup_cmd, "run_script", lambda *a, **kw: _Result(cleanup(root, dry_run=True))
    )

    assert cleanup_cmd.cmd_cleanup(["--dry-run"]) == 0
    assert "Examined a MINORITY" not in capsys.readouterr().out


def test_archived_messages_count_as_examined(tmp_path):
    """They left `active_count`, so the denominator has to add them back — otherwise a
    successful cleanup looks like it examined less and less the better it worked."""
    from otaman_cli.commands.cleanup import _examined_remainder

    report = {
        "active_count": 100,
        "skipped_unparseable": 5,
        "skipped_no_frontmatter": 0,
        "held_unacked": 20,
    }

    assert _examined_remainder(report) == 75


def test_the_remainder_never_goes_negative():
    """A report whose counts do not add up must not produce a negative denominator and
    flip the comparison."""
    from otaman_cli.commands.cleanup import _examined_remainder

    assert _examined_remainder({"active_count": 1, "skipped_unparseable": 50}) == 0
    assert _examined_remainder({}) == 0
