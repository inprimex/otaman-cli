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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-25T14:43:43.1+00:00", "2026-09-25T14:43:43.100000+00:00"),
        ("2026-09-25T14:43:43.12+00:00", "2026-09-25T14:43:43.120000+00:00"),
        ("2026-09-25T14:43:43.123+00:00", "2026-09-25T14:43:43.123000+00:00"),
        ("2026-09-25T14:43:43.123456+00:00", "2026-09-25T14:43:43.123456+00:00"),
        ("2026-09-25T14:43:43.1234567+00:00", "2026-09-25T14:43:43.123456+00:00"),
        ("2026-09-25T14:43:43+00:00", "2026-09-25T14:43:43+00:00"),  # untouched
        ("2026-09-25T14:43:43.+00:00", "2026-09-25T14:43:43.+00:00"),  # no digits: untouched
        ("not a date", "not a date"),
    ],
)
def test_the_fraction_is_normalized_to_six_digits(raw, expected):
    """Tested DIRECTLY, not through the parse.

    The round-trip version of this guard is vacuous on 3.11+: `fromisoformat`
    there accepts any fraction length, so deleting the normalization changes
    nothing on CI or on this interpreter and the test still passes. The behaviour
    it protects only shows up on 3.10 — the package floor — where 3 or 6 digits
    are the only ones accepted. So assert the normalization itself, which is the
    same on every interpreter.
    """
    from otaman_cli.cleanup_bus import _normalize_fraction

    assert _normalize_fraction(raw) == expected


@pytest.mark.parametrize("digits", [1, 2, 3, 5, 6, 7, 9])
def test_fractional_seconds_of_any_length_parse(digits):
    """End to end, for the shapes a tenant can actually produce. On 3.11+ this
    passes with or without the normalization — see the direct test above, which
    is the guard that has teeth on every interpreter."""
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
    (active / "acks" / "20260101T000000-a.cli-agent.ack").write_text("ok", encoding="utf-8")

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
    (active / "acks" / "20260101T000000-a.cli-agent.ack").write_text("ok", encoding="utf-8")

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

    assert cleanup_cmd.cmd_cleanup(["--dry-run"]) == 0
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
